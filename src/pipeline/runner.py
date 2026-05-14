import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from pipeline.metrics import KnownMetric, fmt_duration, now_human

TIMEOUT_EXIT = 124


def _first_line(text: str) -> str:
    return text.splitlines()[0]


def _summarize_tool_input(name: str, inp: dict) -> str:
    if name in ("Read", "Write", "Edit"):
        return inp.get("file_path", "")
    if name == "Bash":
        cmd = inp.get("command") or ""
        return _first_line(cmd)[:100] if cmd else ""
    if name == "Grep":
        return repr(inp.get("pattern", ""))
    if name == "Glob":
        return repr(inp.get("pattern", ""))
    if name == "TodoWrite":
        todos = inp.get("todos") or []
        return f"{len(todos)} todos"
    compact = json.dumps(inp, ensure_ascii=False)
    return compact[:100]


def _format_event(event: dict) -> str | None:
    kind = event.get("type")

    if kind == "assistant":
        parts = []
        for block in event.get("message", {}).get("content", []):
            bt = block.get("type")
            if bt == "text":
                text = (block.get("text") or "").strip()
                if text:
                    parts.append(f"[text] {_first_line(text)}")
            elif bt == "thinking":
                thought = (block.get("thinking") or "").strip()
                if thought:
                    parts.append(f"[think] {_first_line(thought)}")
            elif bt == "tool_use":
                name = block.get("name", "?")
                summary = _summarize_tool_input(name, block.get("input") or {})
                parts.append(f"[tool] {name}({summary})")
        return "\n".join(parts) or None

    if kind == "user":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict)
                    )
                text = str(content).strip()
                if text:
                    return f"[ok]   {_first_line(text)[:120]}"
        return None

    if kind == "system":
        subtype = event.get("subtype") or "event"
        return f"[sys]  {subtype}"

    if kind == "result":
        cost = event.get("total_cost_usd")
        duration = event.get("duration_ms")
        bits = []
        if duration is not None:
            bits.append(f"{duration / 1000:.1f}s")
        if cost is not None:
            bits.append(f"${cost:.4f}")
        return "[done] " + " ".join(bits) if bits else "[done]"

    return None


@dataclass
class LLMResult:
    rc: int
    final_text: str


class StageHandle:
    def __init__(self):
        self._status = "ok"
        self._countdown_until: float | None = None
        self._stderr_lines: list[str] = []

    def update(self, text: str) -> None:
        self._status = text

    def sleep(self, seconds: float, label: str | None = None) -> None:
        if seconds <= 0:
            return
        prev = self._status
        if label is not None:
            self._status = label
        self._countdown_until = time.monotonic() + seconds
        try:
            time.sleep(seconds)
        finally:
            self._countdown_until = None
            if label is not None:
                self._status = prev

    def stderr(self, text: str) -> None:
        self._stderr_lines.append(text)


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
            for msg in handle._stderr_lines:
                sys.stderr.write(msg + ("\n" if not msg.endswith("\n") else ""))
            sys.stderr.flush()

    def run_command(
        self,
        cmd: list[str],
        cwd: str | Path | None,
        log_path: Path,
        timeout: float | None = None,
        line_formatter=None,
    ) -> int:
        with open(log_path, "a") as log_fd:
            log_fd.write(f"=== new attempt @ {now_human()} ===\n")
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

    def run_llm(
        self,
        prompt: str,
        cwd,
        log_path: Path | None,
        step_metrics,
        *,
        allowed_tools: list[str] = (),
        extra_args: list[str] = (),
        timeout: float | None = None,
    ) -> LLMResult:
        argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if allowed_tools:
            argv += ["--allowedTools", ",".join(allowed_tools)]
        argv += list(extra_args)

        wall_start = monotonic()
        state = {
            "final_text": "",
            "cost": 0.0,
            "tokens_in": 0,
            "tokens_out": 0,
            "duration_ms": 0,
        }

        with open(log_path if log_path is not None else os.devnull, "a") as log_fd:
            log_fd.write(f"=== new attempt @ {now_human()} ===\n")
            log_fd.flush()

            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            except FileNotFoundError as err:
                raise RuntimeError("claude binary not found on PATH") from err

            def _stream():
                for raw_line in proc.stdout:
                    line = raw_line.decode(errors="replace")
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        log_fd.write(line)
                        log_fd.flush()
                        continue
                    formatted = _format_event(event)
                    if formatted:
                        log_fd.write(formatted + "\n")
                        log_fd.flush()
                    if event.get("type") == "assistant":
                        for block in event.get("message", {}).get("content", []):
                            if (
                                block.get("type") == "text"
                                and (block.get("text") or "").strip()
                            ):
                                state["final_text"] = block["text"]
                    elif event.get("type") == "result":
                        state["cost"] = event.get("total_cost_usd") or 0.0
                        state["duration_ms"] = event.get("duration_ms") or 0
                        usage = event.get("usage") or {}
                        state["tokens_in"] = usage.get("input_tokens") or 0
                        state["tokens_out"] = usage.get("output_tokens") or 0

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

        if state["duration_ms"] == 0:
            state["duration_ms"] = int((monotonic() - wall_start) * 1000)

        step_metrics.add(KnownMetric.LLM_COST_USD, state["cost"])
        step_metrics.add(KnownMetric.LLM_INPUT_TOKENS, state["tokens_in"])
        step_metrics.add(KnownMetric.LLM_OUTPUT_TOKENS, state["tokens_out"])
        step_metrics.add(KnownMetric.LLM_DURATION_MS, state["duration_ms"])

        return LLMResult(rc=rc, final_text=state["final_text"])
