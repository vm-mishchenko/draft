from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.context import RunContext
    from pipeline.engine import Engine


class StepError(Exception):
    def __init__(self, step_name: str, exit_code: int):
        super().__init__(f"Step '{step_name}' failed with exit code {exit_code}")
        self.step_name = step_name
        self.exit_code = exit_code


class PipelineLifecycle:
    def before_step(self, step: "Step", ctx: "RunContext"):
        pass

    def after_step(self, step: "Step", ctx: "RunContext"):
        pass

    def on_step_success(self, step: "Step", ctx: "RunContext"):
        pass

    def on_step_error(self, step: "Step", ctx: "RunContext", exc: StepError):
        pass


class Step:
    name: str

    def defaults(self) -> dict:
        return {"max_retries": 1, "timeout": None, "retry_delay": 0}

    def cmd(self, ctx: "RunContext") -> list[str]:
        raise NotImplementedError

    def run(self, ctx: "RunContext", engine: "Engine"):
        cfg = ctx.config(self.name)
        last_rc = 1
        for attempt in range(1, cfg["max_retries"] + 1):
            rc = engine.run_stage(
                label=self.name,
                cmd=self.cmd(ctx),
                cwd=ctx.get("cwd"),
                log_path=ctx.log_path(self.name),
                attempt=attempt,
                timeout=cfg["timeout"],
            )
            if rc == 0:
                return
            last_rc = rc
            if attempt < cfg["max_retries"]:
                engine.sleep(cfg["retry_delay"])
        raise StepError(self.name, last_rc)


class Pipeline:
    def __init__(self, steps: list[Step]):
        self.steps = steps

    def run(self, ctx: "RunContext", engine: "Engine", lifecycle: PipelineLifecycle | None = None):
        lc = lifecycle or PipelineLifecycle()
        for step in self.steps:
            if ctx.is_completed(step.name):
                continue
            lc.before_step(step, ctx)
            try:
                step.run(ctx, engine)
                ctx.mark_done(step.name)
                lc.on_step_success(step, ctx)
            except StepError as exc:
                lc.on_step_error(step, ctx, exc)
                raise
            finally:
                lc.after_step(step, ctx)
