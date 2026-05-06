import os
import subprocess
from dataclasses import dataclass

from pipeline import PipelineLifecycle, StepError


@dataclass
class HookResult:
    cmd: str
    rc: int
    output: str


class HookError(Exception):
    pass


def _run_hook_cmd(cmd: str, timeout: int, retry: int, cwd: str | None) -> HookResult:
    for attempt in range(retry):
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                return HookResult(cmd, 0, output)
            if attempt + 1 == retry:
                return HookResult(cmd, result.returncode, output)
        except subprocess.TimeoutExpired:
            if attempt + 1 == retry:
                return HookResult(cmd, 124, f"timed out after {timeout}s")
    return HookResult(cmd, 1, "")


class HookRunner:
    def __init__(self, config: dict, cwd: str | None = None):
        self._steps_config = config.get("steps", {})
        self._cwd = cwd

    def run(self, step_name: str, event: str) -> list[HookResult]:
        if self._cwd and not os.path.isdir(self._cwd):
            return []
        entries = (
            self._steps_config
            .get(step_name, {})
            .get("hooks", {})
            .get(event, [])
        )
        results = []
        for entry in entries:
            result = _run_hook_cmd(
                entry["cmd"],
                entry.get("timeout", 30),
                entry.get("retry", 1),
                self._cwd,
            )
            results.append(result)
        _raise_if_failed(results)
        return results


def _raise_if_failed(results: list[HookResult]) -> None:
    for r in results:
        if r.rc != 0:
            raise HookError(f"Hook command failed (exit {r.rc}): {r.cmd}")


class DraftLifecycle(PipelineLifecycle):
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
