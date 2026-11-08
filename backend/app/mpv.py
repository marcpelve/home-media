"""mpv JSON-IPC wrapper.

Spawns mpv with --input-ipc-server=<socket> and communicates via
JSON commands over a local Unix/Named socket.

Real IPC implementation lands in P3. This stub is importable and
unit-testable without mpv installed.
"""


class MpvClient:
    """Thin wrapper around an mpv child process using JSON IPC.

    Protocol: send JSON lines like {"command": ["load", url]} to the socket,
    receive JSON lines like {"event": "property-change", "data": {...}}.
    """

    def __init__(self, socket_path: str = "") -> None:
        self._socket_path = socket_path
        self._conn = None  # socket connection, set in start()

    def start(self, socket_path: str = "") -> None:
        """Spawn mpv with --input-ipc-server=<socket_path>.

        P3: subprocess.Popen(["mpv", "--input-ipc-server", socket_path, ...])
        """
        if socket_path:
            self._socket_path = socket_path
        raise NotImplementedError("mpv spawn lands in P3")

    def play(self, url_or_path: str) -> None:
        """Load and play a URL (online) or local file path (offline).

        P3: send {"command": ["loadfile", url_or_path, "replace"]}
        """
        raise NotImplementedError("mpv play lands in P3")

    def set_position(self, seconds: float) -> None:
        """Seek to the given position in seconds.

        P3: send {"command": ["set_property", "time-pos", seconds]}
        """
        raise NotImplementedError("mpv set_position lands in P3")

    def get_property(self, name: str) -> object:
        """Query an mpv property (e.g. 'time-pos', 'duration', 'pause').

        P3: send {"command": ["get_property", name]}, await reply.
        """
        raise NotImplementedError("mpv get_property lands in P3")

    def quit(self) -> None:
        """Send quit command and wait for the process to exit.

        P3: send {"command": ["quit"]}, then proc.wait()
        """
        raise NotImplementedError("mpv quit lands in P3")
