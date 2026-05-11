from pathlib import Path
from unittest.mock import MagicMock

import pytest

from draft.steps.pr_open import PrOpenStep, STEP_DIR
from pipeline import StepError


def _make_ctx(cfg, branch="draft/feat", repo="/repo", base_branch="main", wt_dir="/wt"):
    ctx = MagicMock()
    ctx.config.return_value = cfg
    ctx.get.side_effect = lambda key, default=None: {
        "repo": repo,
        "branch": branch,
        "base_branch": base_branch,
        "wt_dir": wt_dir,
    }.get(key, default)
    log = MagicMock()
    log.read_text.return_value = ""
    ctx.log_path.return_value = log
    return ctx


def _make_engine():
    engine = MagicMock()
    stage_cm = MagicMock()
    stage_cm.__enter__ = MagicMock(return_value=MagicMock())
    stage_cm.__exit__ = MagicMock(return_value=False)
    engine.stage.return_value = stage_cm
    engine.run_command.return_value = 0
    return engine


def test_custom_body_path_used_in_prompt(tmp_path):
    tpl = tmp_path / "my_template.md"
    tpl.write_text("## Summary\n")

    cfg = {"max_retries": 1, "timeout": 300, "retry_delay": 0, "title_prefix": "", "pr_body_template": str(tpl)}
    ctx = _make_ctx(cfg)
    engine = _make_engine()

    step = PrOpenStep()
    step.run(ctx, engine, MagicMock())

    first_call_cmd = engine.run_command.call_args_list[0].kwargs["cmd"]
    prompt = first_call_cmd[2]
    assert str(tpl.resolve()) in prompt


def test_bundled_default_used_when_no_template(tmp_path):
    cfg = {"max_retries": 1, "timeout": 300, "retry_delay": 0, "title_prefix": ""}
    ctx = _make_ctx(cfg)
    engine = _make_engine()

    step = PrOpenStep()
    step.run(ctx, engine, MagicMock())

    first_call_cmd = engine.run_command.call_args_list[0].kwargs["cmd"]
    prompt = first_call_cmd[2]
    bundled = str((STEP_DIR / "pull-request-template.md").resolve())
    assert bundled in prompt


def test_missing_body_path_raises_step_error_without_claude(tmp_path, capsys):
    missing = tmp_path / "gone.md"
    cfg = {"max_retries": 1, "timeout": 300, "retry_delay": 0, "title_prefix": "", "pr_body_template": str(missing)}
    ctx = _make_ctx(cfg)
    engine = _make_engine()

    step = PrOpenStep()
    with pytest.raises(StepError) as exc_info:
        step.run(ctx, engine, MagicMock())

    assert exc_info.value.step_name == "open-pr"
    assert exc_info.value.exit_code == 1
    engine.run_command.assert_not_called()
    captured = capsys.readouterr()
    assert str(missing) in captured.err


def test_no_regression_bundled_path_under_step_dir():
    bundled = STEP_DIR / "pull-request-template.md"
    assert bundled.is_file(), "bundled pull-request-template.md must exist"
