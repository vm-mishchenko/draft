import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pipeline.metrics import fmt_duration

TIMEOUT_EXIT = 124


class StageHandle:
    def __init__(self):
        self._status = "ok"
        self._countdown_until: float | None = None

    def update(self, text: str) -> None:
        self._status = text


class Runner:
    LABEL_WIDTH = 36

    def __init__(self):
        self._active_stage: StageHandle | None = None

    @contextmanager
    def tty_ticker(self, label: str):
        is_tty = sys.stdout.isatty()
        padded = label[: self.LABEL_WIDTH].ljust(self.LABEL_WIDTH)
        start = time.monotonic()
        stop_event = threading.Event()
        final_status = ["?"]

        def _set_status(text: str) -> None:
            final_status[0] = text

        def _tick():
            while not stop_event.is_set():
                elapsed = fmt_duration(time.monotonic() - start)
                line = f"{padded} {elapsed:>7}  running"
                if is_tty:
                    sys.stdout.write(f"\r\033[K{line}")
                    sys.stdout.flush()
                stop_event.wait(1)

        thread = threading.Thread(target=_tick, daemon=True)
        if is_tty:
            thread.start()

        try:
            yield _set_status
        finally:
            stop_event.set()
            if is_tty:
                thread.join()
            elapsed = fmt_duration(time.monotonic() - start)
            line = f"{padded} {elapsed:>7}  {final_status[0]}"
            if is_tty:
                sys.stdout.write(f"\r\033[K{line}\n")
            else:
                sys.stdout.write(f"{line}\n")
            sys.stdout.flush()

    @contextmanager
    def stage(self, label: str):
        is_tty = sys.stdout.isatty()
        padded = label[: self.LABEL_WIDTH].ljust(self.LABEL_WIDTH)
        start = time.monotonic()
        stop_event = threading.Event()
        handle = StageHandle()
        self._active_stage = handle

        def _tick():
            while not stop_event.is_set():
                if handle._countdown_until is not None:
                    slot = fmt_duration(
                        max(0.0, handle._countdown_until - time.monotonic())
                    )
                else:
                    slot = fmt_duration(time.monotonic() - start)
                line = f"{padded} {slot:>7}  {handle._status}"
                if is_tty:
                    sys.stdout.write(f"\r\033[K{line}")
                    sys.stdout.flush()
                stop_event.wait(1)

        thread = threading.Thread(target=_tick, daemon=True)
        if is_tty:
            thread.start()

        failed = False
        try:
            yield handle
        except Exception:
            failed = True
            raise
        finally:
            self._active_stage = None
            stop_event.set()
            if is_tty:
                thread.join()
            elapsed = fmt_duration(time.monotonic() - start)
            status = "failed" if failed else handle._status
            line = f"{padded} {elapsed:>7}  {status}"
            if is_tty:
                sys.stdout.write(f"\r\033[K{line}\n")
            else:
                sys.stdout.write(f"{line}\n")
            sys.stdout.flush()

    def run_command(
        self,
        cmd: list[str],
        cwd: str | Path | None,
        log_path: Path,
        timeout: float | None = None,
        attempt: int = 1,
        line_formatter=None,
    ) -> int:
        with open(log_path, "a") as log_fd:
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            log_fd.write(f"=== attempt {attempt} @ {ts} ===\n")
            log_fd.flush()

            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            def _stream():
                for chunk in iter(lambda: proc.stdout.read(4096), b""):
                    text = chunk.decode(errors="replace")
                    if line_formatter is not None:
                        lines = text.splitlines(keepends=True)
                        out = []
                        for ln in lines:
                            formatted = line_formatter(ln)
                            if formatted is not None:
                                out.append(
                                    formatted
                                    if formatted.endswith("\n")
                                    else formatted + "\n"
                                )
                        text = "".join(out)
                    if text:
                        log_fd.write(text)
                        log_fd.flush()

            streamer = threading.Thread(target=_stream, daemon=True)
            streamer.start()

            rc = TIMEOUT_EXIT
            try:
                proc.wait(timeout=timeout)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                rc = TIMEOUT_EXIT
            finally:
                streamer.join()

        return rc

    def sleep(self, seconds: float, label: str = "waiting"):
        if seconds <= 0:
            return
        if self._active_stage is not None:
            handle = self._active_stage
            prev_status = handle._status
            handle._status = label
            handle._countdown_until = time.monotonic() + seconds
            try:
                time.sleep(seconds)
            finally:
                handle._countdown_until = None
                handle._status = prev_status
            return
        is_tty = sys.stdout.isatty()
        padded = label[: self.LABEL_WIDTH].ljust(self.LABEL_WIDTH)
        end = time.monotonic() + seconds
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            line = f"{padded} {int(remaining):>6}s..."
            if is_tty:
                sys.stdout.write(f"\r\033[K{line}")
                sys.stdout.flush()
            time.sleep(1)
        if is_tty:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
        else:
            sys.stdout.write("\n")
            sys.stdout.flush()
