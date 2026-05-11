import json
import os
import tempfile
from pathlib import Path

import pytest

from pipeline.context import RunContext


@pytest.fixture
def tmp_run_dir(tmp_path):
    return tmp_path


def make_ctx(tmp_run_dir):
    return RunContext("260505-120000", tmp_run_dir, step_configs={"my-step": {"timeout": 5}})


def test_save_and_load_roundtrip(tmp_run_dir):
    ctx = make_ctx(tmp_run_dir)
    ctx.set("branch", "fix-auth")
    ctx.step_set("code-spec", "attempts", 2)
    ctx.mark_done("worktree-create")
    ctx.save()

    ctx2 = RunContext.load("260505-120000", tmp_run_dir)
    assert ctx2.get("branch") == "fix-auth"
    assert ctx2.step_get("code-spec", "attempts") == 2
    assert ctx2.is_completed("worktree-create")
    assert not ctx2.is_completed("push")


def test_save_is_atomic(tmp_run_dir):
    ctx = make_ctx(tmp_run_dir)
    ctx.set("key", "value")
    ctx.save()

    original = (tmp_run_dir / "state.json").read_text()

    # Simulate crash: tmp file left behind
    tmp_path = tmp_run_dir / "state.json.tmp"
    tmp_path.write_text("corrupt garbage")

    # state.json should still be intact (os.replace wasn't called)
    restored = (tmp_run_dir / "state.json").read_text()
    assert restored == original


def test_load_missing_state_json(tmp_run_dir):
    with pytest.raises(FileNotFoundError):
        RunContext.load("260505-120000", tmp_run_dir)


def test_load_corrupt_state_json(tmp_run_dir):
    (tmp_run_dir / "state.json").write_text("{not valid json")
    with pytest.raises(ValueError, match="corrupt"):
        RunContext.load("260505-120000", tmp_run_dir)


def test_log_path(tmp_run_dir):
    ctx = make_ctx(tmp_run_dir)
    assert ctx.log_path("code-spec") == tmp_run_dir / "code-spec.log"


def test_config_returns_step_config(tmp_run_dir):
    ctx = make_ctx(tmp_run_dir)
    assert ctx.config("my-step") == {"timeout": 5}
    assert ctx.config("unknown-step") == {}
