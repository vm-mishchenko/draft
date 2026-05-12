import os
import sys
import threading
from pathlib import Path

HEARTBEAT_INTERVAL_SECONDS = 10
HEARTBEAT_FILENAME = "heartbeat"

from pipeline.metrics import now_human


class Heartbeat:
    FILENAME = HEARTBEAT_FILENAME

    def __init__(self, run_dir: Path, interval: float = HEARTBEAT_INTERVAL_SECONDS):
        self._path = run_dir / self.FILENAME
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "Heartbeat":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass

    def _loop(self):
        while not self._stop.is_set():
            self._write_once()
            self._stop.wait(self._interval)

    def _write_once(self):
        try:
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(now_human())
            os.replace(tmp, self._path)
        except OSError as exc:
            print(f"heartbeat write error: {exc}", file=sys.stderr)
