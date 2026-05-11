from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from draft.steps.code_spec import _build_claude_cmd, _load_template, CodeSpecStep
from pipeline import StepError


_BUNDLED_MARKER = "{{SPEC}}"


def _make_ctx(cfg, spec="my spec", verify_errors=""):
    ctx = MagicMock()
    ctx.config.return_value = cfg
    ctx.get.return_value = "/wt"
    ctx.step_get.return_value = verify_errors
    ctx.log_path.return_value = "/tmp/log"
    return ctx


def test_load_template_returns_bundled_default_when_no_path():
    cfg = {"timeout": 60}
    result = _load_template(cfg)
    assert "{{SPEC}}" in result


def test_load_template_returns_custom_content(tmp_path):
    tpl = tmp_path / "custom.md"
    tpl.write_text("Custom {{SPEC}} template {{VERIFY_ERRORS}}")
    cfg = {"timeout": 60, "prompt_template": str(tpl)}
    result = _load_template(cfg)
    assert result == "Custom {{SPEC}} template {{VERIFY_ERRORS}}"


def test_build_claude_cmd_substitutes_spec(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("Spec: {{SPEC}} Errors: {{VERIFY_ERRORS}}")
    template = tpl.read_text()

    ctx = MagicMock()
    ctx.get.return_value = "my spec content"
    ctx.step_get.return_value = ""

    cmd = _build_claude_cmd(ctx, template)
    prompt = cmd[2]
    assert "my spec content" in prompt
    assert "{{SPEC}}" not in prompt


def test_build_claude_cmd_uses_bundled_template():
    bundled = _load_template({})
    ctx = MagicMock()
    ctx.get.return_value = "the spec"
    ctx.step_get.return_value = ""
    cmd = _build_claude_cmd(ctx, bundled)
    prompt = cmd[2]
    assert "the spec" in prompt
    assert "{{SPEC}}" not in prompt


def test_template_loaded_once_across_retries(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_ERRORS}}\n")

    cfg = {"max_retries": 3, "timeout": 60, "prompt_template": str(tpl)}

    ctx = _make_ctx(cfg, spec="s")
    engine = MagicMock()
    engine.stage.return_value.__enter__ = MagicMock(return_value=None)
    engine.stage.return_value.__exit__ = MagicMock(return_value=False)
    engine.run_command.return_value = None

    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = []

    read_count = 0
    original_read = Path.read_text

    def counting_read(self, *args, **kwargs):
        nonlocal read_count
        if self == tpl:
            read_count += 1
        return original_read(self, *args, **kwargs)

    step = CodeSpecStep()

    with patch.object(Path, "read_text", counting_read):
        with patch("draft.steps.code_spec._is_branch_clean", return_value=True), \
             patch("draft.steps.code_spec._commits_ahead", return_value=1):
            step.run(ctx, engine, lifecycle)

    assert read_count == 1


def test_custom_template_file_removed_before_step_runs(tmp_path):
    tpl = tmp_path / "gone.md"
    cfg = {"max_retries": 1, "timeout": 60, "prompt_template": str(tpl)}

    ctx = _make_ctx(cfg)
    engine = MagicMock()
    engine.stage.return_value.__enter__ = MagicMock(return_value=None)
    engine.stage.return_value.__exit__ = MagicMock(return_value=False)
    lifecycle = MagicMock()

    step = CodeSpecStep()
    with pytest.raises(StepError) as exc_info:
        step.run(ctx, engine, lifecycle)
    assert exc_info.value.step_name == "implement-spec"
