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
        return {"timeout": None}

    def cmd(self, ctx):
        return ["true"]


class AlwaysFailStep(Step):
    name = "fail-step"

    def defaults(self):
        return {"timeout": None}

    def cmd(self, ctx):
        return ["false"]


# --- Pipeline tests ---

def test_pipeline_skips_completed_steps(tmp_path):
    ctx = make_ctx(tmp_path, {"ok-step": {"timeout": None}})
    ctx.mark_done("ok-step")
    ctx.save()

    ran = []

    class TrackStep(Step):
        name = "ok-step"
        def defaults(self): return {"timeout": None}
        def run(self, ctx, engine, lifecycle=None): ran.append(self.name)

    Pipeline([TrackStep()]).run(ctx, Runner())
    assert ran == []


def test_pipeline_step_error_propagates_with_lifecycle(tmp_path):
    ctx = make_ctx(tmp_path, {"fail-step": {"timeout": None}})

    lc = MagicMock(spec=PipelineLifecycle)

    class ImmediateFailStep(Step):
        name = "fail-step"
        def defaults(self): return {"timeout": None}
        def run(self, ctx, engine, lifecycle=None): raise StepError("fail-step", 1)

    with pytest.raises(StepError):
        Pipeline([ImmediateFailStep()]).run(ctx, Runner(), lifecycle=lc)

    lc.before_step.assert_called_once()
    lc.on_step_error.assert_called_once()
    lc.after_step.assert_called_once()
    lc.on_step_success.assert_not_called()


def test_pipeline_lifecycle_order(tmp_path):
    ctx = make_ctx(tmp_path, {"ok-step": {"timeout": None}})
    events = []

    class RecordingLifecycle(PipelineLifecycle):
        def before_step(self, step, ctx): events.append("before")
        def after_step(self, step, ctx): events.append("after")
        def on_step_success(self, step, ctx): events.append("success")
        def on_step_error(self, step, ctx, exc): events.append("error")

    class OkStep(Step):
        name = "ok-step"
        def defaults(self): return {"timeout": None}
        def run(self, ctx, engine, lifecycle=None): pass

    Pipeline([OkStep()]).run(ctx, Runner(), lifecycle=RecordingLifecycle())
    assert events == ["before", "success", "after"]


def test_step_default_run_is_one_shot(tmp_path):
    class OkCmd(Step):
        name = "ok-step"
        def defaults(self): return {"timeout": None}
        def cmd(self, ctx): return ["true"]

    class FailCmd(Step):
        name = "fail-step"
        def defaults(self): return {"timeout": None}
        def cmd(self, ctx): return ["false"]

    ok_dir = tmp_path / "ok"
    ok_dir.mkdir()
    ctx_ok = make_ctx(ok_dir, {"ok-step": {"timeout": None}})
    OkCmd().run(ctx_ok, Runner())

    fail_dir = tmp_path / "fail"
    fail_dir.mkdir()
    ctx_fail = make_ctx(fail_dir, {"fail-step": {"timeout": None}})
    with pytest.raises(StepError) as exc_info:
        FailCmd().run(ctx_fail, Runner())
    assert exc_info.value.exit_code != 0
    assert exc_info.value.step_name == "fail-step"

    mock_runner = MagicMock()
    stage_cm = MagicMock()
    stage_cm.__enter__ = MagicMock(return_value=MagicMock())
    stage_cm.__exit__ = MagicMock(return_value=False)
    mock_runner.stage.return_value = stage_cm
    mock_runner.run_command.return_value = 7

    mock_dir = tmp_path / "mock"
    mock_dir.mkdir()
    ctx_mock = make_ctx(mock_dir, {"fail-step": {"timeout": None}})
    with pytest.raises(StepError) as exc_info2:
        FailCmd().run(ctx_mock, mock_runner)
    assert exc_info2.value.exit_code == 7
    assert mock_runner.run_command.call_count == 1


def test_step_defaults_is_timeout_only():
    class MyStep(Step):
        name = "my-step"
        def cmd(self, ctx): return []

    assert MyStep().defaults() == {"timeout": None}


def test_engine_timeout_returns_timeout_exit(tmp_path):
    ctx = make_ctx(tmp_path)
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
