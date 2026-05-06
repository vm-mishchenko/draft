import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pipeline import PipelineLifecycle, StepError


@dataclass
class HookResult:
    cmd: str
    rc: int
    output: str
    duration: float


class HookError(Exception):
    pass


def _run_hook_cmd(cmd: str, timeout: int, cwd: str | None) -> HookResult:
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        duration = time.monotonic() - start
        output = result.stdout + result.stderr
        return HookResult(cmd, result.returncode, output, duration)
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return HookResult(cmd, 124, f"timed out after {timeout}s", duration)


def _status_text(rc: int) -> str:
    if rc == 0:
        return "ok"
    if rc == 124:
        return "timeout"
    return f"fail rc={rc}"


def _footer(rc: int, duration: float, timeout: int) -> str:
    if rc == 124:
        return f"--- timed out after {timeout}s ---\n\n"
    return f"--- exit {rc} in {duration:.1f}s ---\n\n"


class HookRunner:
    def __init__(self, config: dict, cwd: str | None, run_dir: str | Path, engine):
        self._steps_config = config.get("steps", {})
        self._cwd = cwd
        self._run_dir = Path(run_dir)
        self._engine = engine

    def run(self, step_name: str, event: str) -> list[HookResult]:
        entries = (
            self._steps_config
            .get(step_name, {})
            .get("hooks", {})
            .get(event, [])
        )
        if not entries:
            return []

        if self._cwd and not os.path.isdir(self._cwd):
            for i, entry in enumerate(entries):
                label = f"{step_name}.{event}[{i}] {entry['cmd']}"
                with self._engine.tty_ticker(label) as set_status:
                    set_status("skipped (cwd missing)")
            return []

        log_path = self._run_dir / f"{step_name}.{event}.log"
        results: list[HookResult] = []

        log_fd = None
        try:
            log_fd = open(log_path, "w")
        except OSError as exc:
            print(
                f"warning: could not write hook log {log_path}: {exc}",
                file=sys.stderr,
            )

        try:
            for i, entry in enumerate(entries):
                cmd = entry["cmd"]
                timeout = entry.get("timeout", 30)
                label = f"{step_name}.{event}[{i}] {cmd}"

                if log_fd is not None:
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    log_fd.write(f"=== {step_name}.{event}[{i}] @ {ts} ===\n")
                    log_fd.write(f"$ {cmd}\n")
                    log_fd.flush()

                with self._engine.tty_ticker(label) as set_status:
                    result = _run_hook_cmd(cmd, timeout, self._cwd)

                    if log_fd is not None:
                        if result.output:
                            log_fd.write(result.output)
                            if not result.output.endswith("\n"):
                                log_fd.write("\n")
                        log_fd.write(_footer(result.rc, result.duration, timeout))
                        log_fd.flush()

                    set_status(_status_text(result.rc))

                results.append(result)

                if result.rc != 0:
                    raise HookError(
                        f"Hook command failed (exit {result.rc}): {cmd}"
                    )
        finally:
            if log_fd is not None:
                log_fd.close()

        return results


class DraftLifecycle(PipelineLifecycle):
    def __init__(self, hook_runner: HookRunner):
        self._hooks = hook_runner

    def before_step(self, step, ctx):
        self._hooks.run(step.name, "pre")

    def after_step(self, step, ctx):
        self._hooks.run(step.name, "post")

    def on_step_success(self, step, ctx):
        self._hooks.run(step.name, "on_success")

    def on_step_error(self, step, ctx, exc: StepError):
        self._hooks.run(step.name, "on_error")

    def run_hooks(self, step_name: str, event: str) -> list[HookResult]:
        return self._hooks.run(step_name, event)
