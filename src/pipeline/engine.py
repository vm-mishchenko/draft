import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

TIMEOUT_EXIT = 124


def _fmt_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s"


class Engine:
    LABEL_WIDTH = 36

    def run_stage(
        self,
        label: str,
        cmd: list[str],
        cwd: str | Path | None,
        log_path: Path,
        attempt: int = 1,
        timeout: float | None = None,
        line_formatter=None,
    ) -> int:
        is_tty = sys.stdout.isatty()
        padded = label[:self.LABEL_WIDTH].ljust(self.LABEL_WIDTH)

        with open(log_path, "a") as log_fd:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            log_fd.write(f"=== attempt {attempt} @ {ts} ===\n")
            log_fd.flush()

            start = time.monotonic()
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            stop_event = threading.Event()

            def _ticker():
                while not stop_event.is_set():
                    elapsed = _fmt_elapsed(time.monotonic() - start)
                    line = f"{padded} {elapsed:>7}  running"
                    if is_tty:
                        sys.stdout.write(f"\r\033[K{line}")
                        sys.stdout.flush()
                    stop_event.wait(1)

            def _stream():
                for chunk in iter(lambda: proc.stdout.read(4096), b""):
                    text = chunk.decode(errors="replace")
                    if line_formatter is not None:
                        lines = text.splitlines(keepends=True)
                        out = []
                        for ln in lines:
                            formatted = line_formatter(ln)
                            if formatted is not None:
                                out.append(formatted if formatted.endswith("\n") else formatted + "\n")
                        text = "".join(out)
                    if text:
                        log_fd.write(text)
                        log_fd.flush()

            ticker = threading.Thread(target=_ticker, daemon=True)
            streamer = threading.Thread(target=_stream, daemon=True)
            if is_tty:
                ticker.start()
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
                stop_event.set()
                if is_tty:
                    ticker.join()

        elapsed = _fmt_elapsed(time.monotonic() - start)
        status = "ok" if rc == 0 else ("timeout" if rc == TIMEOUT_EXIT else f"exit {rc}")
        line = f"{padded} {elapsed:>7}  {status}"
        if is_tty:
            sys.stdout.write(f"\r\033[K{line}\n")
        else:
            sys.stdout.write(f"{line}\n")
        sys.stdout.flush()

        return rc

    def sleep(self, seconds: float, label: str = "waiting"):
        if seconds <= 0:
            return
        is_tty = sys.stdout.isatty()
        padded = label[:self.LABEL_WIDTH].ljust(self.LABEL_WIDTH)
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
