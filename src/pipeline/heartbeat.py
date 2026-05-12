import contextlib
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

from pipeline.metrics import now_human, parse_human

HEARTBEAT_INTERVAL_SECONDS = 5
HEARTBEAT_FILENAME = "heartbeat"


class Heartbeat:
    FILENAME = HEARTBEAT_FILENAME

    def __init__(self, run_dir: Path):
        self._path = run_dir / self.FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def write_now(self) -> None:
        try:
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(now_human())
            os.replace(tmp, self._path)
        except OSError as exc:
            print(f"heartbeat write error: {exc}", file=sys.stderr)

    def read(self) -> "datetime | None":
        try:
            return parse_human(self._path.read_text().strip())
        except (OSError, ValueError):
            return None

    def delete(self) -> None:
        with contextlib.suppress(OSError):
            self._path.unlink(missing_ok=True)


class HeartbeatPulse:
    def __init__(
        self, heartbeat: Heartbeat, interval: float = HEARTBEAT_INTERVAL_SECONDS
    ):
        self._hb = heartbeat
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "HeartbeatPulse":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._hb.delete()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._hb.write_now()
            self._stop.wait(self._interval)
