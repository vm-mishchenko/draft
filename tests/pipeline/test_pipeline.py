import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from pipeline.context import RunContext
from pipeline.runner import Runner, TIMEOUT_EXIT
from pipeline.pipeline import Pipeline, PipelineLifecycle, Step, StepError


# --- Helpers ---

def make_ctx(tmp_path, step_configs=None):
    ctx = RunContext("260505-120000", tmp_path, step_configs=step_configs or {})
    ctx.set("cwd", str(tmp_path))
    return ctx


class AlwaysOkStep(Step):
    name = "ok-step"

    def defaults(self):
        return {"max_retries": 1, "timeout": None, "retry_delay": 0}

    def cmd(self, ctx):
        return ["true"]


class AlwaysFailStep(Step):
    name = "fail-step"

    def defaults(self):
        return {"max_retries": 1, "timeout": None, "retry_delay": 0}

    def cmd(self, ctx):
        return ["false"]


class CountingStep(Step):
    name = "counting-step"

    def __init__(self):
        self.call_count = 0

    def defaults(self):
        return {"max_retries": 3, "timeout": None, "retry_delay": 0}

    def cmd(self, ctx):
        self.call_count += 1
        return ["false"]


# --- Pipeline tests ---

def test_pipeline_skips_completed_steps(tmp_path):
    ctx = make_ctx(tmp_path, {"ok-step": {"max_retries": 1, "timeout": None, "retry_delay": 0}})
    ctx.mark_done("ok-step")
    ctx.save()

    ran = []

    class TrackStep(Step):
        name = "ok-step"
        def defaults(self): return {"max_retries": 1, "timeout": None, "retry_delay": 0}
        def run(self, ctx, engine, lifecycle=None): ran.append(self.name)

    Pipeline([TrackStep()]).run(ctx, Runner())
    assert ran == []


def test_pipeline_step_error_propagates_with_lifecycle(tmp_path):
    ctx = make_ctx(tmp_path, {"fail-step": {"max_retries": 1, "timeout": None, "retry_delay": 0}})

    lc = MagicMock(spec=PipelineLifecycle)

    class ImmediateFailStep(Step):
        name = "fail-step"
        def defaults(self): return {"max_retries": 1, "timeout": None, "retry_delay": 0}
        def run(self, ctx, engine, lifecycle=None): raise StepError("fail-step", 1)

    with pytest.raises(StepError):
        Pipeline([ImmediateFailStep()]).run(ctx, Runner(), lifecycle=lc)

    lc.before_step.assert_called_once()
    lc.on_step_error.assert_called_once()
    lc.after_step.assert_called_once()
    lc.on_step_success.assert_not_called()


def test_pipeline_lifecycle_order(tmp_path):
    ctx = make_ctx(tmp_path, {"ok-step": {"max_retries": 1, "timeout": None, "retry_delay": 0}})
    events = []

    class RecordingLifecycle(PipelineLifecycle):
        def before_step(self, step, ctx): events.append("before")
        def after_step(self, step, ctx): events.append("after")
        def on_step_success(self, step, ctx): events.append("success")
        def on_step_error(self, step, ctx, exc): events.append("error")

    class OkStep(Step):
        name = "ok-step"
        def defaults(self): return {"max_retries": 1, "timeout": None, "retry_delay": 0}
        def run(self, ctx, engine, lifecycle=None): pass

    Pipeline([OkStep()]).run(ctx, Runner(), lifecycle=RecordingLifecycle())
    assert events == ["before", "success", "after"]


def test_step_default_run_retries_on_failure(tmp_path):
    step_configs = {"counting-step": {"max_retries": 3, "timeout": None, "retry_delay": 0}}
    ctx = make_ctx(tmp_path, step_configs)
    step = CountingStep()
    engine = Runner()

    with pytest.raises(StepError) as exc_info:
        step.run(ctx, engine)

    assert exc_info.value.step_name == "counting-step"
    assert step.call_count == 3


def test_step_default_run_raises_after_max_retries(tmp_path):
    step_configs = {"fail-step": {"max_retries": 2, "timeout": None, "retry_delay": 0}}
    ctx = make_ctx(tmp_path, step_configs)

    class FailCmd(Step):
        name = "fail-step"
        def defaults(self): return {"max_retries": 2, "timeout": None, "retry_delay": 0}
        def cmd(self, ctx): return ["false"]

    with pytest.raises(StepError) as exc_info:
        FailCmd().run(ctx, Runner())
    assert exc_info.value.exit_code != 0


def test_engine_timeout_returns_timeout_exit(tmp_path):
    engine = Runner()
    log = tmp_path / "test.log"
    rc = engine.run_command(
        cmd=["sleep", "10"],
        cwd=str(tmp_path),
        log_path=log,
        attempt=1,
        timeout=0.1,
    )
    assert rc == TIMEOUT_EXIT
