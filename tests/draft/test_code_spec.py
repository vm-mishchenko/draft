import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from draft.hooks import HookResult
from draft.steps.implement_spec import (
    ImplementSpecStep,
    _filter_dupes,
    _format_suggested_failures,
    _generate_commit_message,
    _load_template,
    _normalize_cmd,
    _parse_suggestions,
    _render_prompt,
    _render_verify_commands,
    _run_suggested_checks,
)
from pipeline import StepError
from pipeline.runner import TIMEOUT_EXIT, LLMResult

_BUNDLED_MARKER = "{{SPEC}}"

_DEFAULT_CFG = {
    "max_retries": 10,
    "timeout": 1200,
    "suggest_extra_checks": True,
    "max_checks": 5,
    "per_check_timeout": 120,
    "suggester_timeout": 120,
    "suggester_total_budget": 300,
}


def _make_ctx(cfg, spec="my spec", verify_errors="", tmp_path=None):
    ctx = MagicMock()
    ctx.config.return_value = cfg
    ctx.get.side_effect = lambda key, default=None: {"wt_dir": "/wt", "spec": spec}.get(
        key, default
    )
    ctx.step_get.return_value = verify_errors
    ctx.log_path.return_value = "/tmp/log"
    ctx.run_dir = tmp_path if tmp_path is not None else Path("/tmp")
    return ctx


def _make_engine(s=None):
    engine = MagicMock()
    stage_ctx = MagicMock()
    stage_ctx.__enter__ = MagicMock(return_value=s or MagicMock())
    stage_ctx.__exit__ = MagicMock(return_value=False)
    engine.stage.return_value = stage_ctx
    engine.run_command.return_value = None
    engine.run_llm.return_value = LLMResult(rc=0, final_text="commit msg")
    return engine


def _make_cfg(**overrides):
    return {**_DEFAULT_CFG, **overrides}


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


def test_render_prompt_substitutes_spec(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text(
        "Spec: {{SPEC}} Commands: {{VERIFY_COMMANDS}} Errors: {{VERIFY_ERRORS}}"
    )
    template = tpl.read_text()

    ctx = MagicMock()
    ctx.get.return_value = "my spec content"
    ctx.step_get.return_value = ""

    prompt = _render_prompt(ctx, template, "")
    assert "my spec content" in prompt
    assert "{{SPEC}}" not in prompt


def test_render_prompt_uses_bundled_template():
    bundled = _load_template({})
    ctx = MagicMock()
    ctx.get.return_value = "the spec"
    ctx.step_get.return_value = ""
    prompt = _render_prompt(ctx, bundled, "")
    assert "the spec" in prompt
    assert "{{SPEC}}" not in prompt


def test_render_prompt_substitutes_verify_commands(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_COMMANDS}}\n{{VERIFY_ERRORS}}")
    template = tpl.read_text()

    ctx = MagicMock()
    ctx.get.return_value = "spec"
    ctx.step_get.return_value = ""

    verify_commands = "## Verify commands\n\n```bash\nmake test\n```"
    prompt = _render_prompt(ctx, template, verify_commands)
    assert "make test" in prompt
    assert "{{VERIFY_COMMANDS}}" not in prompt


def test_render_prompt_empty_verify_commands_collapses_marker(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_COMMANDS}}\n{{VERIFY_ERRORS}}")
    template = tpl.read_text()

    ctx = MagicMock()
    ctx.get.return_value = "spec"
    ctx.step_get.return_value = ""

    prompt = _render_prompt(ctx, template, "")
    assert "{{VERIFY_COMMANDS}}" not in prompt
    assert "## Verify commands" not in prompt


def test_render_prompt_template_without_verify_commands_marker(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_ERRORS}}")
    template = tpl.read_text()

    ctx = MagicMock()
    ctx.get.return_value = "spec"
    ctx.step_get.return_value = ""

    prompt = _render_prompt(
        ctx, template, "## Verify commands\n\n```bash\nmake test\n```"
    )
    assert "spec" in prompt
    assert "{{SPEC}}" not in prompt


def test_render_prompt_all_substitutions_work_together(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_COMMANDS}}\n{{VERIFY_ERRORS}}")
    template = tpl.read_text()

    ctx = MagicMock()
    ctx.get.return_value = "my spec"
    ctx.step_get.return_value = "errors here"

    verify_commands = "## Verify commands\n\n```bash\nmake test\n```"
    prompt = _render_prompt(ctx, template, verify_commands)
    assert "my spec" in prompt
    assert "make test" in prompt
    assert "errors here" in prompt
    assert "{{SPEC}}" not in prompt
    assert "{{VERIFY_COMMANDS}}" not in prompt
    assert "{{VERIFY_ERRORS}}" not in prompt


def test_bundled_code_spec_no_commit_instruction():
    bundled = _load_template({})
    assert "Commit your work" not in bundled
    assert "must not run `git commit`" in bundled


def test_bundled_code_spec_has_verify_commands_marker():
    bundled = _load_template({})
    assert "{{VERIFY_COMMANDS}}" in bundled


def test_render_verify_commands_empty_list():
    assert _render_verify_commands([]) == ""


def test_render_verify_commands_none_like_input():
    assert _render_verify_commands([]) == ""


def test_render_verify_commands_single_entry():
    result = _render_verify_commands([{"cmd": "make test"}])
    assert "## Verify commands" in result
    assert "Run them yourself before finishing if practical" in result
    assert "```bash" in result
    assert "make test" in result


def test_render_verify_commands_multiple_entries_in_order():
    result = _render_verify_commands([{"cmd": "make lint"}, {"cmd": "make test"}])
    assert result.index("make lint") < result.index("make test")
    assert result.count("```bash") == 1


def test_render_verify_commands_ignores_timeout():
    result = _render_verify_commands([{"cmd": "make test", "timeout": 120}])
    assert "120" not in result
    assert "timeout" not in result


def test_render_verify_commands_skips_entry_without_cmd():
    result = _render_verify_commands([{"timeout": 30}])
    assert result == ""


def test_render_verify_commands_skips_entry_with_empty_cmd():
    result = _render_verify_commands([{"cmd": ""}])
    assert result == ""


def test_render_verify_commands_multiline_cmd_verbatim():
    result = _render_verify_commands([{"cmd": "step1\nstep2"}])
    assert "step1\nstep2" in result


def test_bundled_commit_message_has_placeholders():
    from importlib.resources import files

    content = (
        files("draft.steps.implement_spec").joinpath("commit_message.md").read_text()
    )
    assert "{{SPEC}}" in content
    assert "{{DIFF}}" in content


def test_template_loaded_once_across_retries(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_ERRORS}}\n")

    cfg = _make_cfg(max_retries=3, timeout=60, prompt_template=str(tpl))

    ctx = _make_ctx(cfg, spec="s", tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    read_count = 0
    original_read = Path.read_text

    def counting_read(self, *args, **kwargs):
        nonlocal read_count
        if self == tpl:
            read_count += 1
        return original_read(self, *args, **kwargs)

    step = ImplementSpecStep()

    with (
        patch.object(Path, "read_text", counting_read),
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("Add feature", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="abc123"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    assert read_count == 1


def test_get_hooks_called_once_across_retries(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_COMMANDS}}\n{{VERIFY_ERRORS}}\n")

    cfg = _make_cfg(max_retries=2, timeout=60, prompt_template=str(tpl))
    ctx = _make_ctx(cfg, spec="s", tmp_path=tmp_path)
    engine = _make_engine()

    fail_result = MagicMock()
    fail_result.rc = 1
    fail_result.cmd = "make test"
    fail_result.output = "boom"

    verify_call_count = 0

    def run_hooks_side_effect(step_name, event):
        nonlocal verify_call_count
        if event == "verify":
            verify_call_count += 1
            if verify_call_count == 1:
                return [fail_result]
        return []

    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = [{"cmd": "make test"}]
    lifecycle.run_hooks.side_effect = run_hooks_side_effect

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("Add feature", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="abc123"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    lifecycle.get_hooks.assert_called_once_with("implement-spec", "verify")


def test_run_prompt_contains_verify_commands_when_configured(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_COMMANDS}}\n{{VERIFY_ERRORS}}\n")

    cfg = _make_cfg(max_retries=1, timeout=60, prompt_template=str(tpl))
    ctx = _make_ctx(cfg, spec="s", tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = [{"cmd": "make test"}]
    lifecycle.run_hooks.return_value = []

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("msg", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    # First run_llm call is the implement call
    prompt = engine.run_llm.call_args_list[0][1]["prompt"]
    assert "## Verify commands" in prompt
    assert "make test" in prompt


def test_run_prompt_no_verify_commands_section_when_empty(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_COMMANDS}}\n{{VERIFY_ERRORS}}\n")

    cfg = _make_cfg(max_retries=1, timeout=60, prompt_template=str(tpl))
    ctx = _make_ctx(cfg, spec="s", tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("msg", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    # First run_llm call is the implement call
    prompt = engine.run_llm.call_args_list[0][1]["prompt"]
    assert "## Verify commands" not in prompt


def test_custom_template_file_removed_before_step_runs(tmp_path):
    tpl = tmp_path / "gone.md"
    cfg = _make_cfg(max_retries=1, timeout=60, prompt_template=str(tpl))

    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()

    step = ImplementSpecStep()
    with pytest.raises(StepError) as exc_info:
        step.run(ctx, engine, lifecycle, MagicMock())
    assert exc_info.value.step_name == "implement-spec"


def test_no_changes_after_agent_loops_and_records_verify_error(tmp_path):
    cfg = _make_cfg(max_retries=2, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=False),
        patch("draft.steps.implement_spec._generate_commit_message") as mock_gen,
        pytest.raises(StepError) as exc_info,
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    assert exc_info.value.step_name == "implement-spec"
    mock_gen.assert_not_called()

    calls = ctx.step_set.call_args_list
    verify_error_calls = [c for c in calls if c.args[1] == "verify_errors"]
    assert any(
        "agent produced no changes" in str(c.args[2]) for c in verify_error_calls
    )


def test_verify_failure_feeds_back_and_skips_commit(tmp_path):
    cfg = _make_cfg(max_retries=3, timeout=60)
    ctx = _make_ctx(cfg, spec="my spec", tmp_path=tmp_path)

    fail_result = MagicMock()
    fail_result.rc = 1
    fail_result.cmd = "make test"
    fail_result.output = "boom"

    call_count = 0

    def hooks_side_effect(step_name, event):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [fail_result]
        return []

    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.side_effect = hooks_side_effect
    engine = _make_engine()

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("Add foo", False),
        ) as mock_gen,
        patch("draft.steps.implement_spec._run_git_capture", return_value="deadbeef\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    assert engine.run_llm.call_count == 2
    mock_gen.assert_called_once()

    calls = ctx.step_set.call_args_list
    verify_calls = [c for c in calls if c.args[1] == "verify_errors"]
    assert any("boom" in str(c.args[2]) for c in verify_calls)

    sha_calls = [c for c in calls if c.args[1] == "commit_sha"]
    assert len(sha_calls) == 1


def test_commit_message_used_in_git_commit(tmp_path):
    cfg = _make_cfg(max_retries=2, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("Subject line\n\nBody", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="abc\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ) as mock_commit,
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    mock_commit.assert_called_once()
    commit_call_args = mock_commit.call_args[0][0]
    assert commit_call_args == ["git", "commit", "-m", "Subject line\n\nBody"]
    assert "--no-verify" not in commit_call_args


def test_commit_message_fallback_recorded(tmp_path):
    cfg = _make_cfg(max_retries=2, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("Implement spec", True),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    fallback_calls = [c for c in calls if c.args[1] == "commit_message_fallback"]
    assert len(fallback_calls) == 1
    assert fallback_calls[0].args[2] is True


def test_pre_commit_hook_failure_feeds_back(tmp_path):
    cfg = _make_cfg(max_retries=3, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    call_count = 0

    def commit_side_effect(cmd, *args, **kwargs):
        nonlocal call_count
        if cmd[0] == "git" and cmd[1] == "commit":
            call_count += 1
            if call_count == 1:
                return subprocess.CompletedProcess(
                    cmd, 1, b"trailing whitespace fixed", b"pre-commit failed"
                )
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("Fix thing", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha123\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            side_effect=commit_side_effect,
        ),
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    assert engine.run_llm.call_count == 2

    calls = ctx.step_set.call_args_list
    verify_calls = [c for c in calls if c.args[1] == "verify_errors"]
    first_error = next(c.args[2] for c in verify_calls if c.args[2])
    assert "Pre-commit hook failures" in first_error

    sha_calls = [c for c in calls if c.args[1] == "commit_sha"]
    assert len(sha_calls) == 1


def test_pre_commit_hook_timeout_feeds_back(tmp_path):
    cfg = _make_cfg(max_retries=3, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    call_count = 0

    def commit_side_effect(cmd, *args, **kwargs):
        nonlocal call_count
        if cmd[0] == "git" and cmd[1] == "commit":
            call_count += 1
            if call_count == 1:
                return subprocess.CompletedProcess(
                    cmd, TIMEOUT_EXIT, b"", b"timed out after 60s\n"
                )
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("Fix thing", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            side_effect=commit_side_effect,
        ),
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    verify_calls = [c for c in calls if c.args[1] == "verify_errors"]
    first_error = next(c.args[2] for c in verify_calls if c.args[2])
    assert "timed out after" in first_error


def test_max_retries_exhausted_raises_step_error(tmp_path):
    cfg = _make_cfg(max_retries=3, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []

    fail_result = MagicMock()
    fail_result.rc = 1
    fail_result.cmd = "make test"
    fail_result.output = "fail"
    lifecycle.run_hooks.return_value = [fail_result]

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._generate_commit_message") as mock_gen,
        pytest.raises(StepError) as exc_info,
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    assert exc_info.value.step_name == "implement-spec"
    mock_gen.assert_not_called()


# Tests for _generate_commit_message


def test_generate_commit_message_returns_trimmed_stdout(tmp_path):
    log = tmp_path / "msg.log"
    engine = MagicMock()
    engine.run_llm.return_value = LLMResult(rc=0, final_text="  Add feature  ")
    with patch("draft.steps.implement_spec._run_git_capture", return_value=""):
        msg, used_fallback = _generate_commit_message(
            "spec", "/wt", log, 120, 3, engine, MagicMock()
        )
    assert msg == "Add feature"
    assert used_fallback is False
    assert engine.run_llm.call_count == 1


def test_generate_commit_message_retries_on_empty(tmp_path):
    log = tmp_path / "msg.log"
    engine = MagicMock()
    engine.run_llm.side_effect = [
        LLMResult(rc=0, final_text=""),
        LLMResult(rc=0, final_text="Add feature"),
    ]
    with patch("draft.steps.implement_spec._run_git_capture", return_value=""):
        msg, used_fallback = _generate_commit_message(
            "spec", "/wt", log, 120, 3, engine, MagicMock()
        )
    assert msg == "Add feature"
    assert used_fallback is False
    assert engine.run_llm.call_count == 2


def test_generate_commit_message_retries_on_non_zero(tmp_path):
    log = tmp_path / "msg.log"
    engine = MagicMock()
    engine.run_llm.side_effect = [
        LLMResult(rc=2, final_text=""),
        LLMResult(rc=0, final_text="Add feature"),
    ]
    with patch("draft.steps.implement_spec._run_git_capture", return_value=""):
        msg, used_fallback = _generate_commit_message(
            "spec", "/wt", log, 120, 3, engine, MagicMock()
        )
    assert msg == "Add feature"
    assert used_fallback is False
    assert engine.run_llm.call_count == 2


def test_generate_commit_message_retries_on_timeout(tmp_path):
    log = tmp_path / "msg.log"
    engine = MagicMock()
    engine.run_llm.side_effect = [
        LLMResult(rc=TIMEOUT_EXIT, final_text=""),
        LLMResult(rc=0, final_text="Add feature"),
    ]
    with patch("draft.steps.implement_spec._run_git_capture", return_value=""):
        msg, used_fallback = _generate_commit_message(
            "spec", "/wt", log, 120, 3, engine, MagicMock()
        )
    assert msg == "Add feature"
    assert used_fallback is False
    assert engine.run_llm.call_count == 2


def test_generate_commit_message_falls_back_after_three_failures(tmp_path, capsys):
    log = tmp_path / "msg.log"
    engine = MagicMock()
    engine.run_llm.side_effect = [
        LLMResult(rc=0, final_text=""),
        LLMResult(rc=0, final_text=""),
        LLMResult(rc=0, final_text=""),
    ]
    with patch("draft.steps.implement_spec._run_git_capture", return_value=""):
        msg, used_fallback = _generate_commit_message(
            "spec", "/wt", log, 120, 3, engine, MagicMock()
        )
    assert msg == "Implement spec"
    assert used_fallback is True
    assert engine.run_llm.call_count == 3
    captured = capsys.readouterr()
    assert "fallback" in captured.err.lower() or "Implement spec" in captured.err


def test_generate_commit_message_logs_to_file(tmp_path):
    log = tmp_path / "msg.log"
    engine = MagicMock()
    engine.run_llm.side_effect = [
        LLMResult(rc=0, final_text=""),
        LLMResult(rc=0, final_text="Add feature"),
    ]
    with patch("draft.steps.implement_spec._run_git_capture", return_value=""):
        _generate_commit_message("spec", "/wt", log, 120, 3, engine, MagicMock())

    content = log.read_text()
    assert "--- selected commit message (attempt 2) ---" in content
    assert "Add feature" in content


# --- _normalize_cmd ---


def test_normalize_cmd_strips_and_collapses():
    assert _normalize_cmd("  ruff  check  src  ") == "ruff check src"


def test_normalize_cmd_single_word():
    assert _normalize_cmd("pytest") == "pytest"


# --- _parse_suggestions ---


def test_parse_suggestions_empty_array():
    assert _parse_suggestions("[]") == []


def test_parse_suggestions_not_json():
    assert _parse_suggestions("not json") == []


def test_parse_suggestions_top_level_object():
    assert _parse_suggestions('{"cmd":"x"}') == []


def test_parse_suggestions_drops_empty_and_missing_cmd():
    result = _parse_suggestions('[{"cmd":"a"},{"cmd":""},{"timeout":5}]')
    assert result == [{"cmd": "a"}]


def test_parse_suggestions_coerces_string_timeout():
    result = _parse_suggestions('[{"cmd":"a","timeout":"60"}]')
    assert result == [{"cmd": "a", "timeout": 60}]


def test_parse_suggestions_drops_nonpositive_timeout():
    assert _parse_suggestions('[{"cmd":"a","timeout":-1}]') == []


def test_parse_suggestions_drops_zero_timeout():
    assert _parse_suggestions('[{"cmd":"a","timeout":0}]') == []


def test_parse_suggestions_accepts_valid_timeout():
    result = _parse_suggestions('[{"cmd":"a","timeout":30}]')
    assert result == [{"cmd": "a", "timeout": 30}]


def test_parse_suggestions_drops_non_dict_entry():
    result = _parse_suggestions('["string", {"cmd":"a"}]')
    assert result == [{"cmd": "a"}]


# --- _filter_dupes ---


def test_filter_dupes_whitespace_normalization():
    result = _filter_dupes([{"cmd": "ruff  check  src"}], ["ruff check src"])
    assert result == []


def test_filter_dupes_keeps_different_args():
    result = _filter_dupes([{"cmd": "ruff check ."}], ["ruff check src"])
    assert result == [{"cmd": "ruff check ."}]


def test_filter_dupes_empty_suggested():
    assert _filter_dupes([], ["make test"]) == []


def test_filter_dupes_empty_static():
    entries = [{"cmd": "pytest"}]
    assert _filter_dupes(entries, []) == entries


# --- _format_suggested_failures ---


def test_format_suggested_failures_structure():
    result = _format_suggested_failures(
        [HookResult(cmd="pytest -x", rc=1, output="E\n", duration=0.5)]
    )
    assert result.startswith("## Suggested check failures")
    assert "$ pytest -x" in result
    assert "E" in result


def test_format_suggested_failures_multiple():
    failures = [
        HookResult(cmd="cmd1", rc=1, output="err1", duration=1.0),
        HookResult(cmd="cmd2", rc=2, output="err2", duration=0.5),
    ]
    result = _format_suggested_failures(failures)
    assert "$ cmd1" in result
    assert "err1" in result
    assert "$ cmd2" in result
    assert "err2" in result


# --- _run_suggested_checks ---


def _make_run_suggested_cfg(**overrides):
    base = {"per_check_timeout": 120, "suggester_total_budget": 300}
    return {**base, **overrides}


def test_run_suggested_checks_all_pass(tmp_path):
    engine = MagicMock()
    cfg = _make_run_suggested_cfg()
    suggested = [{"cmd": "echo ok"}, {"cmd": "echo also ok"}]

    with patch(
        "draft.steps.implement_spec._run_hook_cmd",
        side_effect=[
            HookResult(cmd="echo ok", rc=0, output="ok\n", duration=0.1),
            HookResult(cmd="echo also ok", rc=0, output="also ok\n", duration=0.1),
        ],
    ):
        failures = _run_suggested_checks(suggested, "/wt", tmp_path, engine, cfg)

    assert failures == []
    log = (tmp_path / "implement-spec.suggested.log").read_text()
    assert "--- exit 0" in log


def test_run_suggested_checks_first_fails_stops_early(tmp_path):
    engine = MagicMock()
    cfg = _make_run_suggested_cfg()
    suggested = [{"cmd": "false"}, {"cmd": "echo never"}]

    with patch(
        "draft.steps.implement_spec._run_hook_cmd",
        side_effect=[
            HookResult(cmd="false", rc=1, output="", duration=0.1),
        ],
    ) as mock_run:
        failures = _run_suggested_checks(suggested, "/wt", tmp_path, engine, cfg)

    assert len(failures) == 1
    assert failures[0].rc == 1
    assert mock_run.call_count == 1


def test_run_suggested_checks_caps_timeout(tmp_path):
    engine = MagicMock()
    cfg = _make_run_suggested_cfg(per_check_timeout=120)
    suggested = [{"cmd": "slow", "timeout": 300}]

    with patch(
        "draft.steps.implement_spec._run_hook_cmd",
        return_value=HookResult(cmd="slow", rc=0, output="", duration=0.1),
    ) as mock_run:
        _run_suggested_checks(suggested, "/wt", tmp_path, engine, cfg)

    mock_run.assert_called_once_with("slow", 120, "/wt")


def test_run_suggested_checks_uses_default_timeout_when_omitted(tmp_path):
    engine = MagicMock()
    cfg = _make_run_suggested_cfg(per_check_timeout=90)
    suggested = [{"cmd": "pytest"}]

    with patch(
        "draft.steps.implement_spec._run_hook_cmd",
        return_value=HookResult(cmd="pytest", rc=0, output="", duration=0.1),
    ) as mock_run:
        _run_suggested_checks(suggested, "/wt", tmp_path, engine, cfg)

    mock_run.assert_called_once_with("pytest", 90, "/wt")


def test_run_suggested_checks_budget_exhausted(tmp_path):
    engine = MagicMock()
    cfg = _make_run_suggested_cfg(per_check_timeout=120, suggester_total_budget=10)
    suggested = [{"cmd": "slow"}, {"cmd": "skipped"}]

    with patch(
        "draft.steps.implement_spec._run_hook_cmd",
        side_effect=[
            HookResult(cmd="slow", rc=0, output="", duration=15.0),
        ],
    ) as mock_run:
        failures = _run_suggested_checks(suggested, "/wt", tmp_path, engine, cfg)

    assert failures == []
    assert mock_run.call_count == 1
    log = (tmp_path / "implement-spec.suggested.log").read_text()
    assert "skipped (budget exhausted)" in log


# --- Integration via ImplementSpecStep.run ---


def test_static_verify_fail_skips_suggester(tmp_path):
    cfg = _make_cfg(max_retries=1, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []

    fail_result = MagicMock()
    fail_result.rc = 1
    fail_result.cmd = "make test"
    fail_result.output = "boom"
    lifecycle.run_hooks.return_value = [fail_result]

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._suggest_checks") as mock_suggest,
        patch("draft.steps.implement_spec._run_suggested_checks") as mock_run_suggest,
        pytest.raises(StepError),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    mock_suggest.assert_not_called()
    mock_run_suggest.assert_not_called()

    calls = ctx.step_set.call_args_list
    verify_calls = [c for c in calls if c.args[1] == "verify_errors"]
    assert any("boom" in str(c.args[2]) for c in verify_calls)


def test_static_verify_passes_empty_suggestions_commits(tmp_path):
    cfg = _make_cfg(max_retries=1, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("msg", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    # Verify suggested_checks is cleared on success (may also be set during the attempt)
    sha_idx = next(i for i, c in enumerate(calls) if c.args[1] == "commit_sha")
    post_commit_clears = [
        c
        for c in calls[sha_idx:]
        if c.args[1] == "suggested_checks" and c.args[2] == []
    ]
    assert len(post_commit_clears) == 1


def test_static_verify_passes_suggestion_succeeds_commits(tmp_path):
    cfg = _make_cfg(max_retries=1, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._suggest_checks",
            return_value=[{"cmd": "echo ok"}],
        ),
        patch(
            "draft.steps.implement_spec._run_suggested_checks",
            return_value=[],
        ),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("msg", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    sha_calls = [c for c in calls if c.args[1] == "commit_sha"]
    assert len(sha_calls) == 1


def test_suggested_check_fails_sets_verify_errors_and_retries(tmp_path):
    cfg = _make_cfg(max_retries=2, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    fail_result = HookResult(cmd="false", rc=1, output="failed\n", duration=0.1)
    suggest_call_count = 0

    def suggest_side_effect(*args, **kwargs):
        nonlocal suggest_call_count
        suggest_call_count += 1
        if suggest_call_count == 1:
            return [{"cmd": "false"}]
        return []

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._suggest_checks",
            side_effect=suggest_side_effect,
        ),
        patch(
            "draft.steps.implement_spec._run_suggested_checks",
            side_effect=[
                [fail_result],
                [],
            ],
        ),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("msg", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    verify_calls = [c for c in calls if c.args[1] == "verify_errors"]
    assert any("Suggested check failures" in str(c.args[2]) for c in verify_calls)

    sha_calls = [c for c in calls if c.args[1] == "commit_sha"]
    assert len(sha_calls) == 1


def test_suggester_llm_not_json_returns_empty_commits(tmp_path):
    cfg = _make_cfg(max_retries=1, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    engine = _make_engine()
    # First run_llm (implement) returns "not json", second run_llm (suggest) also returns "not json"
    engine.run_llm.return_value = LLMResult(rc=0, final_text="not json")

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("msg", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    sha_calls = [c for c in calls if c.args[1] == "commit_sha"]
    assert len(sha_calls) == 1


def test_suggester_llm_rc1_with_valid_json_uses_suggestions(tmp_path):
    cfg = _make_cfg(max_retries=1, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    engine = _make_engine()
    # Implement call returns anything, suggest call returns rc=1 but valid JSON
    engine.run_llm.side_effect = [
        LLMResult(rc=0, final_text="anything"),  # implement
        LLMResult(rc=1, final_text='[{"cmd":"echo hi"}]'),  # suggest
    ]

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._run_suggested_checks",
            return_value=[],
        ) as mock_run_suggest,
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("msg", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    mock_run_suggest.assert_called_once()
    suggested_arg = mock_run_suggest.call_args[0][0]
    assert suggested_arg == [{"cmd": "echo hi"}]


def test_max_retries_exhausted_on_persistent_suggested_failure(tmp_path):
    cfg = _make_cfg(max_retries=2, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    fail_result = HookResult(cmd="false", rc=1, output="fail\n", duration=0.1)

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch(
            "draft.steps.implement_spec._suggest_checks",
            return_value=[{"cmd": "false"}],
        ),
        patch(
            "draft.steps.implement_spec._run_suggested_checks",
            return_value=[fail_result],
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"),
        pytest.raises(StepError) as exc_info,
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    assert exc_info.value.step_name == "implement-spec"


def test_suggest_template_loaded_once_across_retries(tmp_path):
    cfg = _make_cfg(max_retries=3, timeout=60)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []

    fail_verify_count = 0

    def run_hooks_side_effect(step_name, event):
        nonlocal fail_verify_count
        if event == "verify":
            fail_verify_count += 1
            if fail_verify_count <= 2:
                mock = MagicMock()
                mock.rc = 1
                mock.cmd = "x"
                mock.output = "fail"
                return [mock]
        return []

    lifecycle.run_hooks.side_effect = run_hooks_side_effect
    engine = _make_engine()

    step = ImplementSpecStep()
    load_count = 0

    original_load = __import__(
        "draft.steps.implement_spec", fromlist=["_load_suggest_template"]
    )._load_suggest_template

    def counting_load():
        nonlocal load_count
        load_count += 1
        return original_load()

    with (
        patch(
            "draft.steps.implement_spec._load_suggest_template",
            side_effect=counting_load,
        ),
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("msg", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    assert load_count == 1


# --- Bundled suggest_checks.md template ---


def test_bundled_suggest_checks_has_required_markers():
    from importlib.resources import files

    content = (
        files("draft.steps.implement_spec").joinpath("suggest_checks.md").read_text()
    )
    assert "{{SPEC}}" in content
    assert "{{CHANGED_FILES}}" in content
    assert "{{STATIC_CHECKS}}" in content


def test_bundled_suggest_checks_no_diff_marker():
    from importlib.resources import files

    content = (
        files("draft.steps.implement_spec").joinpath("suggest_checks.md").read_text()
    )
    assert "{{DIFF}}" not in content


# --- suggest_extra_checks=False integration ---


def test_suggest_extra_checks_false_static_passes_no_suggester(tmp_path):
    cfg = _make_cfg(max_retries=1, timeout=60, suggest_extra_checks=False)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []
    lifecycle.run_hooks.return_value = []

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._suggest_checks") as mock_suggest,
        patch("draft.steps.implement_spec._run_suggested_checks") as mock_run_suggest,
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("msg", False),
        ),
        patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    mock_suggest.assert_not_called()
    mock_run_suggest.assert_not_called()

    calls = ctx.step_set.call_args_list
    suggested_calls = [c for c in calls if c.args[1] == "suggested_checks"]
    assert len(suggested_calls) == 0

    sha_calls = [c for c in calls if c.args[1] == "commit_sha"]
    assert len(sha_calls) == 1


def test_suggest_extra_checks_false_static_fails_no_suggester(tmp_path):
    cfg = _make_cfg(max_retries=1, timeout=60, suggest_extra_checks=False)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = []

    fail_result = MagicMock()
    fail_result.rc = 1
    fail_result.cmd = "make test"
    fail_result.output = "boom"
    lifecycle.run_hooks.return_value = [fail_result]

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._suggest_checks") as mock_suggest,
        patch("draft.steps.implement_spec._run_suggested_checks") as mock_run_suggest,
        pytest.raises(StepError),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    mock_suggest.assert_not_called()
    mock_run_suggest.assert_not_called()
