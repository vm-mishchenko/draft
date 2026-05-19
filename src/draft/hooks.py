import contextlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from pipeline import PipelineLifecycle, StepError

if TYPE_CHECKING:
    from pipeline.context import RunContext


_SKIP = object()


def _to_env_str(value):
    if value is None:
        return _SKIP
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


@dataclass
class HookResult:
    """Result of a single hook command execution."""

    cmd: str
    rc: int
    output: str
    duration: float


class HookError(Exception):
    """Raised when a hook command exits with a non-zero return code."""


def _run_hook_cmd(
    cmd: str, timeout: int, cwd: str | None, env: dict | None = None
) -> HookResult:
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
            env=env,
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
    return "failed"


def _footer(rc: int, duration: float, timeout: int) -> str:
    if rc == 124:
        return f"--- timed out after {timeout}s ---\n\n"
    return f"--- exit {rc} in {duration:.1f}s ---\n\n"


class HookRunner:
    """Executes shell hook commands for a given pipeline step and lifecycle event."""

    def __init__(
        self,
        config: dict,
        cwd: str | None,
        run_dir: str | Path,
        engine,
        ctx: "RunContext | None" = None,
    ):
        self._steps_config = config.get("steps", {})
        self._cwd = cwd
        self._run_dir = Path(run_dir)
        self._engine = engine
        self._ctx = ctx

    def _build_env(self) -> dict:
        env = dict(os.environ)
        for name, key in (
            ("DRAFT_BRANCH", "branch"),
            ("DRAFT_BASE_BRANCH", "base_branch"),
        ):
            if self._ctx is None:
                env.pop(name, None)
                continue
            v = _to_env_str(self._ctx.get(key))
            if v is _SKIP:
                env.pop(name, None)
                continue
            env[name] = v
        return env

    def get_hooks(self, step_name: str, event: str) -> list[dict]:
        return list(
            self._steps_config.get(step_name, {}).get("hooks", {}).get(event, []) or []
        )

    def run(self, step_name: str, event: str) -> list[HookResult]:
        entries = self._steps_config.get(step_name, {}).get("hooks", {}).get(event, [])
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
        with contextlib.ExitStack() as stack:
            try:
                log_fd = stack.enter_context(open(log_path, "w"))
            except OSError as exc:
                print(
                    f"warning: could not write hook log {log_path}: {exc}",
                    file=sys.stderr,
                )

            for i, entry in enumerate(entries):
                cmd = entry["cmd"]
                timeout = entry.get("timeout", 30)
                label = f"{step_name}.{event}[{i}] {cmd}"

                if log_fd is not None:
                    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                    log_fd.write(f"=== {step_name}.{event}[{i}] @ {ts} ===\n")
                    log_fd.write(f"$ {cmd}\n")
                    log_fd.flush()

                with self._engine.tty_ticker(label) as set_status:
                    result = _run_hook_cmd(
                        cmd, timeout, self._cwd, env=self._build_env()
                    )

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
                    break

        return results


def _raise_if_failed(results: list[HookResult]) -> None:
    for r in results:
        if r.rc != 0:
            raise HookError(f"Hook command failed (exit {r.rc}): {r.cmd}")


class DraftLifecycle(PipelineLifecycle):
    """Runs user-defined hooks at each pipeline step lifecycle event."""

    def __init__(self, hook_runner: HookRunner):
        self._hooks = hook_runner

    def before_step(self, step, ctx):
        _raise_if_failed(self._hooks.run(step.name, "pre"))

    def after_step(self, step, ctx):
        _raise_if_failed(self._hooks.run(step.name, "post"))

    def on_step_success(self, step, ctx):
        _raise_if_failed(self._hooks.run(step.name, "on_success"))

    def on_step_error(self, step, ctx, exc: StepError):
        _raise_if_failed(self._hooks.run(step.name, "on_error"))

    def run_hooks(self, step_name: str, event: str) -> list[HookResult]:
        return self._hooks.run(step_name, event)

    def get_hooks(self, step_name: str, event: str) -> list[dict]:
        return self._hooks.get_hooks(step_name, event)
