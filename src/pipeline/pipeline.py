from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.context import RunContext
    from pipeline.metrics import SessionMetrics
    from pipeline.runner import Runner


class StepError(Exception):
    def __init__(self, step_name: str, exit_code: int):
        super().__init__(f"Step '{step_name}' failed with exit code {exit_code}")
        self.step_name = step_name
        self.exit_code = exit_code


class PipelineLifecycle:
    def before_step(self, step: Step, ctx: RunContext):
        pass

    def after_step(self, step: Step, ctx: RunContext):
        pass

    def on_step_success(self, step: Step, ctx: RunContext):
        pass

    def on_step_error(self, step: Step, ctx: RunContext, exc: StepError):
        pass

    def run_hooks(self, step_name: str, event: str) -> list:
        return []


class Step:
    name: str

    def defaults(self) -> dict:
        return {"timeout": None}

    def cmd(self, ctx: RunContext) -> list[str]:
        raise NotImplementedError

    def run(
        self,
        ctx: RunContext,
        runner: Runner,
        lifecycle: PipelineLifecycle | None = None,
        step_metrics=None,
    ):
        cfg = ctx.config(self.name)
        with runner.stage(self.name):
            rc = runner.run_command(
                cmd=self.cmd(ctx),
                cwd=ctx.get("cwd"),
                log_path=ctx.log_path(self.name),
                timeout=cfg.get("timeout"),
            )
            if rc != 0:
                raise StepError(self.name, rc)


class Pipeline:
    def __init__(self, steps: list[Step]):
        self.steps = steps

    def run(
        self,
        ctx: RunContext,
        runner: Runner,
        lifecycle: PipelineLifecycle,
        session_metrics: SessionMetrics,
    ):
        for step in self.steps:
            if ctx.is_completed(step.name):
                continue
            lifecycle.before_step(step, ctx)
            step_metrics = session_metrics.step_begin(step.name)
            ctx.save()
            try:
                step.run(ctx, runner, lifecycle, step_metrics)
                ctx.mark_done(step.name)
                step_metrics.end(0)
                ctx.save()
                lifecycle.on_step_success(step, ctx)
            except StepError as exc:
                step_metrics.end(exc.exit_code)
                ctx.save()
                lifecycle.on_step_error(step, ctx, exc)
                raise
            except BaseException:
                step_metrics.end(-1)
                ctx.save()
                raise
            finally:
                lifecycle.after_step(step, ctx)
