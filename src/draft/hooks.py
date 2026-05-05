import subprocess

from pipeline import PipelineLifecycle, StepError


class HookError(Exception):
    pass


def _run_hook_cmd(cmd: str, timeout: int, retry: int, cwd: str | None):
    for attempt in range(retry):
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                timeout=timeout,
            )
            if result.returncode == 0:
                return
            if attempt + 1 == retry:
                raise HookError(f"Hook command failed (exit {result.returncode}): {cmd}")
        except subprocess.TimeoutExpired:
            if attempt + 1 == retry:
                raise HookError(f"Hook command timed out after {timeout}s: {cmd}")


class HookRunner:
    def __init__(self, config: dict, cwd: str | None = None):
        self._steps_config = config.get("steps", {})
        self._cwd = cwd

    def run(self, step_name: str, event: str):
        entries = (
            self._steps_config
            .get(step_name, {})
            .get("hooks", {})
            .get(event, [])
        )
        for entry in entries:
            _run_hook_cmd(
                entry["cmd"],
                entry.get("timeout", 30),
                entry.get("retry", 1),
                self._cwd,
            )


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
