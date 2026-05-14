import threading
from importlib.resources import files
from pathlib import Path

INTERVAL = 20
MODEL = "haiku"
TAIL_BYTES = 4096
MIN_BYTES = 200
MAX_CHARS = 30
TIMEOUT = 10

_TEMPLATE = (
    files("draft.steps.implement_spec").joinpath("summarize_status.md").read_text()
)


def _read_tail(path: Path, size: int, max_bytes: int) -> str:
    with open(path, "rb") as f:
        f.seek(max(0, size - max_bytes))
        return f.read().decode("utf-8", errors="replace")


def _normalize(text: str, max_chars: int) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            filtered = "".join(c for c in stripped if ord(c) >= 0x20)
            if len(filtered) > max_chars:
                return filtered[: max_chars - 1] + "…"
            return filtered
    return ""


class LiveStatusSummarizer:
    def __init__(
        self,
        handle,
        *,
        engine,
        step_metrics,
        log_path: Path,
        prefix: str,
        fallback: str = "implementing",
    ):
        self._handle = handle
        self._engine = engine
        self._step_metrics = step_metrics
        self._log_path = log_path
        self._prefix = prefix
        self._fallback = fallback
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._last_size: int = -1

    def start(self) -> "LiveStatusSummarizer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _tick(self) -> None:
        try:
            size = self._log_path.stat().st_size
        except OSError:
            self._handle.update(f"{self._prefix}{self._fallback}")
            return

        if size < MIN_BYTES:
            return

        if size == self._last_size:
            return

        self._last_size = size
        tail = _read_tail(self._log_path, size, TAIL_BYTES)

        try:
            result = self._engine.run_llm(
                prompt=_TEMPLATE.replace("{{TAIL}}", tail),
                cwd=None,
                log_path=None,
                step_metrics=self._step_metrics,
                allowed_tools=[],
                extra_args=["--model", MODEL],
                timeout=TIMEOUT,
            )
        except Exception:
            self._handle.update(f"{self._prefix}{self._fallback}")
            return

        if result.rc != 0:
            self._handle.update(f"{self._prefix}{self._fallback}")
            return

        phrase = _normalize(result.final_text, MAX_CHARS)
        if not phrase:
            self._handle.update(f"{self._prefix}{self._fallback}")
            return

        self._handle.update(f"{self._prefix}{phrase}")

    def _loop(self) -> None:
        while not self._stop.wait(INTERVAL):
            self._tick()
