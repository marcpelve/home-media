"""mpv JSON-IPC wrapper.

Spawns mpv with --input-ipc-server pointing at a Windows pipe name, and talks
to it with newline-delimited JSON: send {"command": [...], "request_id": N},
receive {"request_id": N, "error": "success", ...}.

Commands are request/response and are issued one at a time by the caller, so
all pipe I/O is serialized in the calling thread (concurrent sync read+write on
one named-pipe handle is not allowed on Windows). Reads use overlapped I/O so
each read has a timeout: mpv's IPC is single-threaded and can be busy for a
while (file load, seek), and it also interleaves unsolicited event lines
(start-file, file-loaded, ...) with replies, so a reply can arrive a read late.
command() therefore keeps reading until it gets the line carrying its own
request_id, buffering anything else as an event. A dead mpv yields EOF.

The OS pipe sits behind a transport seam so the routing can be unit-tested
with an in-memory transport; the real-binary path is e2e_mpv.py.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from ctypes import wintypes
from typing import Protocol

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
]
_k32.CreateEventW.restype = wintypes.HANDLE
_k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL,
                              wintypes.LPCWSTR]
_k32.WaitForSingleObject.restype = wintypes.DWORD
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.GetOverlappedResult.restype = wintypes.BOOL
_k32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                     wintypes.LPDWORD, wintypes.BOOL]
_k32.CancelSynchronousIo.restype = wintypes.BOOL
_k32.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
_k32.ResetEvent.restype = wintypes.BOOL
_k32.ResetEvent.argtypes = [wintypes.HANDLE]
_k32.ReadFile.argtypes = [
    wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD,
    ctypes.c_void_p,
]
_k32.WriteFile.argtypes = [
    wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD,
    ctypes.c_void_p,
]
_k32.CloseHandle.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]

_INVALID_HANDLE = (1 << 64) - 1
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_FILE_FLAG_OVERLAPPED = 0x40000000
_WAIT_TIMEOUT = 0x00000102
_READ_CHUNK = 65536


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        # ULONG_PTR on 64-bit: must be pointer-sized, not DWORD, or the struct
        # is 4 bytes short and hEvent lands at the wrong offset (heap
        # corruption in ReadFile/GetOverlappedResult -> segfault).
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


class MpvError(RuntimeError):
    """Raised when mpv replies with an error, times out, or IPC fails."""


def default_mpv_path() -> str:
    found = shutil.which("mpv")
    if found:
        return found
    local = "C:/ai/mpv/mpv.exe"
    if os.path.exists(local):
        return local
    return "mpv"


class Transport(Protocol):
    """Byte-line transport MpvClient talks over (named pipe, or a test fake)."""

    def connect(self) -> None: ...
    def write(self, data: bytes) -> None: ...
    def read_line(self, timeout: float) -> bytes | None: ...
    def close(self) -> None: ...


class PipeTransport:
    """Windows named-pipe transport.

    A dedicated reader thread does the blocking overlapped reads and feeds a
    shared buffer; read_line() waits on it with a timeout. Doing the reads in
    the caller's thread with per-read events left the pipe half-cancelled after
    a timeout, so later reads never completed their event. Keeping every read
    in one thread keeps the pipe's I/O state consistent.
    """

    def __init__(self, pipe_path: str) -> None:
        self._pipe_path = pipe_path
        self._handle: wintypes.HANDLE | None = None
        self._buf = b""
        self._cond = threading.Condition()
        self._eof = False
        self._reader: threading.Thread | None = None

    def connect(self) -> None:
        for _ in range(100):
            h = _k32.CreateFileW(
                self._pipe_path, _GENERIC_READ | _GENERIC_WRITE, 0, None,
                _OPEN_EXISTING, _FILE_FLAG_OVERLAPPED, None,
            )
            if h is not None and h != _INVALID_HANDLE:
                self._handle = h
                break
            time.sleep(0.1)
        if self._handle is None:
            raise MpvError(f"could not open IPC pipe {self._pipe_path}")
        # Reset read state: a previous close() (e.g. from stop()) leaves _eof
        # set, which would make a fresh reader thread exit at once.
        self._buf = b""
        self._eof = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        while not self._eof:
            chunk = self._blocking_read()
            with self._cond:
                if chunk == b"":
                    self._eof = True
                else:
                    self._buf += chunk
                self._cond.notify_all()

    def _blocking_read(self) -> bytes:
        if self._handle is None:
            return b""
        ev = _k32.CreateEventW(None, True, False, None)
        buf = ctypes.create_string_buffer(_READ_CHUNK)
        ovl = _OVERLAPPED()
        ovl.hEvent = ev
        got = wintypes.DWORD()
        _k32.ReadFile(self._handle, buf, _READ_CHUNK, ctypes.byref(got),
                      ctypes.byref(ovl))
        _k32.WaitForSingleObject(ev, 0xFFFFFFFF)
        _k32.GetOverlappedResult(self._handle, ctypes.byref(ovl),
                                 ctypes.byref(got), False)
        _k32.CloseHandle(ev)
        return buf.raw[: got.value]

    def read_line(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        with self._cond:
            while b"\n" not in self._buf and not self._eof:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                return line
            return None

    def write(self, data: bytes) -> None:
        sent = wintypes.DWORD()
        off = 0
        while off < len(data):
            ok = _k32.WriteFile(self._handle, data[off:], len(data) - off,
                                ctypes.byref(sent), None)
            if not ok or sent.value == 0:
                raise MpvError("failed writing to the mpv IPC pipe")
            off += sent.value

    def close(self) -> None:
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None
        with self._cond:
            self._eof = True
            self._cond.notify_all()


class MpvClient:
    """Thin wrapper around an mpv child process using JSON IPC."""

    def __init__(
        self,
        mpv_path: str = "",
        socket_name: str = "",
        extra_args: list[str] | None = None,
        transport: Transport | None = None,
    ) -> None:
        self._mpv_path = mpv_path or default_mpv_path()
        self._socket_name = socket_name or f"hm-mpv-{os.getpid()}"
        self._pipe_path = f"\\\\.\\pipe\\{self._socket_name}"
        self._extra_args = list(extra_args or [])
        self._transport = transport or PipeTransport(self._pipe_path)
        self._proc: subprocess.Popen | None = None
        self._running = False
        self._next_id = 0
        self._events: deque = deque()

    def start(self, spawn: bool = True) -> None:
        if self._running:
            return
        if spawn:
            self._spawn_process()
        self._transport.connect()
        self._running = True

    def ensure_running(self) -> None:
        if self._running and self.is_alive():
            return
        self.stop()
        self.start()

    def is_alive(self) -> bool:
        if self._proc is not None:
            return self._proc.poll() is None
        return self._running

    def stop(self) -> None:
        self._running = False
        if self._proc is not None:
            try:
                self.command(["quit"], timeout=5)
            except Exception:
                pass
        self._transport.close()
        if self._proc is not None:
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    def _spawn_process(self) -> None:
        self._proc = subprocess.Popen(
            [self._mpv_path, "--idle",
             f"--input-ipc-server={self._pipe_path}", *self._extra_args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def command(self, cmd: list, timeout: float = 10.0) -> dict:
        self._next_id += 1
        rid = self._next_id
        data = (json.dumps({"command": cmd, "request_id": rid}) + "\n").encode()
        self._transport.write(data)
        while True:
            line = self._transport.read_line(timeout)
            if line is None:
                raise MpvError(f"mpv command timed out: {cmd}")
            msg = json.loads(line)
            if msg.get("request_id") == rid:
                return msg
            self._events.append(msg)

    def play(self, url_or_path: str) -> None:
        self.command(["loadfile", url_or_path, "replace"])

    def set_position(self, seconds: float) -> None:
        self.command(["set_property", "time-pos", seconds])

    def wait_loaded(self, timeout: float = 15.0) -> bool:
        # A seek before the file loads is a no-op, so wait for duration.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.get_property("duration") is not None:
                    return True
            except MpvError:
                pass
            time.sleep(0.2)
        return False

    def get_property(self, name: str):
        reply = self.command(["get_property", name])
        if reply.get("error") != "success":
            raise MpvError(str(reply.get("error", "unknown error")))
        return reply.get("data")

    def pause(self) -> None:
        self.command(["set_property", "pause", True])

    def resume(self) -> None:
        self.command(["set_property", "pause", False])

    def read_position(self) -> dict:
        def _p(name: str):
            try:
                return self.get_property(name)
            except MpvError:
                return None

        return {
            "time_pos": _p("time-pos"),
            "duration": _p("duration"),
            "pause": _p("pause"),
        }

    def drain_events(self) -> list[dict]:
        out = list(self._events)
        self._events.clear()
        return out
