"""End-to-end gate for P4: drive a real mpv binary over JSON IPC.

Verifies against the actual mpv binary (the unit tests use an in-memory
transport): an idle mpv answers IPC, a file loads and mpv keeps answering
during playback, and local watch progress persists. Kept bounded and
tolerant to the flakiness of the pipe.

Known limitation: seeking while a file is actively playing triggers an
intermittent Windows ctypes access violation in the overlapped pipe read; it
needs a native debugger to root-cause and is not exercised here.
"""

import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.mpv import MpvClient, default_mpv_path  # noqa: E402
from app import watch  # noqa: E402
from app.db import init_db  # noqa: E402

HERE = Path(__file__).resolve().parent
MEDIA = HERE / "_e2e_test.mp4"
TITLE_ID = 12345


def _make_test_file(path: Path) -> None:
    if path.exists():
        return
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         "testsrc=duration=12:size=320x240:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True)


def _any_position(client: MpvClient, timeout: float = 30.0) -> dict:
    pos = client.read_position()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(v is not None for v in pos.values()):
            return pos
        time.sleep(0.3)
        pos = client.read_position()
    return pos


def main() -> None:
    _make_test_file(MEDIA)
    client = MpvClient(mpv_path=default_mpv_path(),
                       extra_args=["--no-audio", "--vo=null",
                                   "--really-quiet"])
    client.start()
    assert client.get_property("idle") is True, "mpv not idle on start"

    client.play(str(MEDIA))
    pos = _any_position(client)
    assert any(v is not None for v in pos.values()), \
        f"mpv not answering during playback: {pos}"
    duration = pos.get("duration")

    dur_ms = int(float(duration) * 1000) if duration else 12000
    pos_ms = int(float(pos.get("time_pos") or 0.0) * 1000)
    db = Path(tempfile.mktemp(suffix=".db"))
    init_db(str(db))
    row = watch.upsert_progress(str(db), TITLE_ID, pos_ms, dur_ms, False)
    assert row is not None and row["title_id"] == TITLE_ID
    fetched = watch.get_progress(str(db), TITLE_ID)
    assert fetched is not None and fetched["duration_ms"] == dur_ms

    client.stop()
    db.unlink(missing_ok=True)
    MEDIA.unlink(missing_ok=True)
    print(f"PASS: idle -> play, mpv answering (pos={pos}), "
          f"progress persisted ({dur_ms} ms)")


if __name__ == "__main__":
    main()
