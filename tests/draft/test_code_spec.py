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
    _parse_suggestions,
    _render_prompt,
    _render_verify_commands,
    _run_suggested_checks,
)
from pipeline import StepError
from pipeline.runner import TIMEOUT_EXIT, LLMResult

_BUNDLED_MARKER = "{{SPEC}}"

_SUGGEST_DISABLED_DEFAULTS = {
    "suggest_extra_checks": False,
    "max_checks": 5,
    "per_check_timeout": 120,
    "suggester_timeout": 120,
    "suggester_total_budget": 300,
}


def _make_ctx(cfg, spec="my spec", verify_errors="", tmp_path=None):
    full_cfg = {**_SUGGEST_DISABLED_DEFAULTS, **cfg}
    ctx = MagicMock()
    ctx.config.return_value = full_cfg
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

    cfg = {"max_retries": 3, "timeout": 60, "prompt_template": str(tpl)}

    ctx = _make_ctx(cfg, spec="s", tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
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
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    assert read_count == 1


def test_get_hooks_called_once_across_retries(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_COMMANDS}}\n{{VERIFY_ERRORS}}\n")

    cfg = {"max_retries": 2, "timeout": 60, "prompt_template": str(tpl)}
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
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    lifecycle.get_hooks.assert_called_once_with("implement-spec", "verify")


def test_run_prompt_contains_verify_commands_when_configured(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_COMMANDS}}\n{{VERIFY_ERRORS}}\n")

    cfg = {"max_retries": 1, "timeout": 60, "prompt_template": str(tpl)}
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
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    prompt = engine.run_llm.call_args[1]["prompt"]
    assert "## Verify commands" in prompt
    assert "make test" in prompt


def test_run_prompt_no_verify_commands_section_when_empty(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_COMMANDS}}\n{{VERIFY_ERRORS}}\n")

    cfg = {"max_retries": 1, "timeout": 60, "prompt_template": str(tpl)}
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
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    prompt = engine.run_llm.call_args[1]["prompt"]
    assert "## Verify commands" not in prompt


def test_custom_template_file_removed_before_step_runs(tmp_path):
    tpl = tmp_path / "gone.md"
    cfg = {"max_retries": 1, "timeout": 60, "prompt_template": str(tpl)}

    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()

    step = ImplementSpecStep()
    with pytest.raises(StepError) as exc_info:
        step.run(ctx, engine, lifecycle, MagicMock())
    assert exc_info.value.step_name == "implement-spec"


def test_no_changes_after_agent_loops_and_records_verify_error(tmp_path):
    cfg = {"max_retries": 2, "timeout": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()

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
    cfg = {"max_retries": 3, "timeout": 60}
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
    cfg = {"max_retries": 2, "timeout": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
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
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    mock_commit.assert_called_once()
    commit_call_args = mock_commit.call_args[0][0]
    assert commit_call_args == ["git", "commit", "-m", "Subject line\n\nBody"]
    assert "--no-verify" not in commit_call_args


def test_commit_message_fallback_recorded(tmp_path):
    cfg = {"max_retries": 2, "timeout": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
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
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    fallback_calls = [c for c in calls if c.args[1] == "commit_message_fallback"]
    assert len(fallback_calls) == 1
    assert fallback_calls[0].args[2] is True


def test_pre_commit_hook_failure_feeds_back(tmp_path):
    cfg = {"max_retries": 3, "timeout": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
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
    cfg = {"max_retries": 3, "timeout": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
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
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    verify_calls = [c for c in calls if c.args[1] == "verify_errors"]
    first_error = next(c.args[2] for c in verify_calls if c.args[2])
    assert "timed out after" in first_error


def test_max_retries_exhausted_raises_step_error(tmp_path):
    cfg = {"max_retries": 3, "timeout": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()

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


# --- _parse_suggestions tests ---


def test_parse_suggestions_empty_array():
    assert _parse_suggestions("[]") == []


def test_parse_suggestions_invalid_json():
    assert _parse_suggestions("not json") == []


def test_parse_suggestions_top_level_not_array():
    assert _parse_suggestions('{"cmd":"x"}') == []


def test_parse_suggestions_filters_empty_and_missing_cmd():
    result = _parse_suggestions('[{"cmd":"a"},{"cmd":""},{"timeout":5}]')
    assert result == [{"cmd": "a"}]


def test_parse_suggestions_coerces_string_timeout():
    result = _parse_suggestions('[{"cmd":"a","timeout":"60"}]')
    assert result == [{"cmd": "a", "timeout": 60}]


def test_parse_suggestions_drops_nonpositive_timeout():
    assert _parse_suggestions('[{"cmd":"a","timeout":-1}]') == []


def test_parse_suggestions_drops_zero_timeout():
    assert _parse_suggestions('[{"cmd":"a","timeout":0}]') == []


# --- _filter_dupes tests ---


def test_filter_dupes_whitespace_normalization():
    result = _filter_dupes([{"cmd": "ruff  check  src"}], ["ruff check src"])
    assert result == []


def test_filter_dupes_different_args_kept():
    result = _filter_dupes([{"cmd": "ruff check ."}], ["ruff check src"])
    assert result == [{"cmd": "ruff check ."}]


# --- _format_suggested_failures tests ---


def test_format_suggested_failures_structure():
    failures = [HookResult(cmd="pytest -x", rc=1, output="E\n", duration=0.5)]
    text = _format_suggested_failures(failures)
    assert text.startswith("## Suggested check failures")
    assert "$ pytest -x" in text
    assert "E" in text


# --- _run_suggested_checks tests ---


def _make_run_suggested_cfg(**overrides):
    cfg = {
        "per_check_timeout": 120,
        "suggester_total_budget": 300,
    }
    cfg.update(overrides)
    return cfg


def _make_hook_result(cmd="echo ok", rc=0, output="ok\n", duration=1.0):
    return HookResult(cmd=cmd, rc=rc, output=output, duration=duration)


def test_run_suggested_checks_all_pass(tmp_path):
    suggested = [{"cmd": "echo ok"}, {"cmd": "echo hi"}]
    cfg = _make_run_suggested_cfg()

    engine = MagicMock()
    stage = MagicMock()

    with patch(
        "draft.steps.implement_spec._run_hook_cmd",
        return_value=_make_hook_result(rc=0),
    ):
        failures = _run_suggested_checks(suggested, "/wt", tmp_path, engine, cfg, stage)

    assert failures == []
    assert stage.update.call_count == 2
    log_content = (tmp_path / "implement-spec.suggested.log").read_text()
    assert "--- exit 0" in log_content


def test_run_suggested_checks_first_failure_short_circuits(tmp_path):
    suggested = [{"cmd": "false"}, {"cmd": "echo ok"}]
    cfg = _make_run_suggested_cfg()

    engine = MagicMock()
    stage = MagicMock()

    fail_result = _make_hook_result(cmd="false", rc=1, output="fail\n")

    with patch(
        "draft.steps.implement_spec._run_hook_cmd",
        return_value=fail_result,
    ) as mock_hook:
        failures = _run_suggested_checks(suggested, "/wt", tmp_path, engine, cfg, stage)

    assert len(failures) == 1
    assert failures[0].rc == 1
    assert mock_hook.call_count == 1


def test_run_suggested_checks_timeout_capped(tmp_path):
    suggested = [{"cmd": "slow", "timeout": 300}]
    cfg = _make_run_suggested_cfg(per_check_timeout=120)

    engine = MagicMock()
    stage = MagicMock()

    with patch(
        "draft.steps.implement_spec._run_hook_cmd",
        return_value=_make_hook_result(),
    ) as mock_hook:
        _run_suggested_checks(suggested, "/wt", tmp_path, engine, cfg, stage)

    mock_hook.assert_called_once_with("slow", 120, "/wt")


def test_run_suggested_checks_default_timeout_used(tmp_path):
    suggested = [{"cmd": "echo ok"}]
    cfg = _make_run_suggested_cfg(per_check_timeout=90)

    engine = MagicMock()
    stage = MagicMock()

    with patch(
        "draft.steps.implement_spec._run_hook_cmd",
        return_value=_make_hook_result(),
    ) as mock_hook:
        _run_suggested_checks(suggested, "/wt", tmp_path, engine, cfg, stage)

    mock_hook.assert_called_once_with("echo ok", 90, "/wt")


def test_run_suggested_checks_budget_exhausted(tmp_path):
    suggested = [{"cmd": "long"}, {"cmd": "next"}]
    cfg = _make_run_suggested_cfg(suggester_total_budget=5)

    engine = MagicMock()
    stage = MagicMock()

    long_result = _make_hook_result(cmd="long", rc=0, duration=10.0)

    with patch(
        "draft.steps.implement_spec._run_hook_cmd",
        return_value=long_result,
    ) as mock_hook:
        failures = _run_suggested_checks(suggested, "/wt", tmp_path, engine, cfg, stage)

    assert failures == []
    assert mock_hook.call_count == 1
    log_content = (tmp_path / "implement-spec.suggested.log").read_text()
    assert "skipped (budget exhausted)" in log_content


# --- Integration tests for ImplementSpecStep.run with suggest_extra_checks ---


def _make_suggest_cfg(**overrides):
    cfg = {
        "max_retries": 2,
        "timeout": 60,
        "suggest_extra_checks": True,
        "max_checks": 5,
        "per_check_timeout": 120,
        "suggester_timeout": 120,
        "suggester_total_budget": 300,
    }
    cfg.update(overrides)
    return cfg


def test_suggest_disabled_static_passes_no_suggester_called(tmp_path):
    cfg = {"max_retries": 1, "timeout": 60, "suggest_extra_checks": False}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
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
        patch("draft.steps.implement_spec._suggest_checks") as mock_suggest,
        patch("draft.steps.implement_spec._run_suggested_checks") as mock_run_sugg,
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    mock_suggest.assert_not_called()
    mock_run_sugg.assert_not_called()
    keys_written = [c.args[1] for c in ctx.step_set.call_args_list]
    assert "suggested_checks" not in keys_written


def test_suggest_disabled_static_fails_no_suggester_called(tmp_path):
    cfg = {"max_retries": 2, "timeout": 60, "suggest_extra_checks": False}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()

    fail_result = MagicMock()
    fail_result.rc = 1
    fail_result.cmd = "make test"
    fail_result.output = "boom"

    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = [fail_result]

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._suggest_checks") as mock_suggest,
        pytest.raises(StepError),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    mock_suggest.assert_not_called()


def test_suggest_enabled_static_fails_no_suggester_called(tmp_path):
    cfg = _make_suggest_cfg(max_retries=1)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()

    fail_result = MagicMock()
    fail_result.rc = 1
    fail_result.cmd = "make test"
    fail_result.output = "boom"

    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = [fail_result]

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._load_suggest_template", return_value="tpl"),
        patch("draft.steps.implement_spec._suggest_checks") as mock_suggest,
        pytest.raises(StepError),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    mock_suggest.assert_not_called()


def test_suggest_enabled_empty_list_commit_path_runs(tmp_path):
    cfg = _make_suggest_cfg(max_retries=1)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = []

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._load_suggest_template", return_value="tpl"),
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
        patch("draft.steps.implement_spec._run_suggested_checks", return_value=[]),
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
    suggested_calls = [c for c in calls if c.args[1] == "suggested_checks"]
    assert any(c.args[2] == [] for c in suggested_calls)


def test_suggest_enabled_check_passes_commit_runs(tmp_path):
    cfg = _make_suggest_cfg(max_retries=1)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = []

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._load_suggest_template", return_value="tpl"),
        patch(
            "draft.steps.implement_spec._suggest_checks",
            return_value=[{"cmd": "echo ok"}],
        ),
        patch("draft.steps.implement_spec._run_suggested_checks", return_value=[]),
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
    commit_sha_calls = [c for c in calls if c.args[1] == "commit_sha"]
    assert len(commit_sha_calls) == 1


def test_suggest_enabled_check_fails_verify_errors_set_commit_not_taken(tmp_path):
    cfg = _make_suggest_cfg(max_retries=2)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = []

    fail_result = HookResult(cmd="false", rc=1, output="fail\n", duration=0.1)
    sugg_call_count = 0

    def run_suggested_side(suggested, wt_dir, run_dir, eng, c, stage):
        nonlocal sugg_call_count
        sugg_call_count += 1
        if sugg_call_count == 1:
            return [fail_result]
        return []

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._load_suggest_template", return_value="tpl"),
        patch(
            "draft.steps.implement_spec._suggest_checks",
            return_value=[{"cmd": "false"}],
        ),
        patch(
            "draft.steps.implement_spec._run_suggested_checks",
            side_effect=run_suggested_side,
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
    commit_sha_calls = [c for c in calls if c.args[1] == "commit_sha"]
    assert len(commit_sha_calls) == 1


def test_suggest_invalid_json_treated_as_empty_commit_runs(tmp_path):
    cfg = _make_suggest_cfg(max_retries=1)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = []

    engine.run_llm.side_effect = [
        LLMResult(rc=0, final_text="implementing"),
        LLMResult(rc=0, final_text="not json"),
    ]

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._load_suggest_template", return_value="tpl"),
        patch("draft.steps.implement_spec._run_git_capture", return_value="file.py\n"),
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("msg", False),
        ),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    commit_sha_calls = [c for c in calls if c.args[1] == "commit_sha"]
    assert len(commit_sha_calls) == 1


def test_suggest_llm_rc1_with_valid_json_used(tmp_path):
    cfg = _make_suggest_cfg(max_retries=1)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = []

    engine.run_llm.side_effect = [
        LLMResult(rc=0, final_text="implementing"),
        LLMResult(rc=1, final_text='[{"cmd":"echo ok"}]'),
    ]

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._load_suggest_template", return_value="tpl"),
        patch("draft.steps.implement_spec._run_git_capture", return_value="file.py\n"),
        patch(
            "draft.steps.implement_spec._run_suggested_checks", return_value=[]
        ) as mock_run_sugg,
        patch(
            "draft.steps.implement_spec._generate_commit_message",
            return_value=("msg", False),
        ),
        patch(
            "draft.steps.implement_spec._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    mock_run_sugg.assert_called_once()
    suggested_arg = mock_run_sugg.call_args[0][0]
    assert suggested_arg == [{"cmd": "echo ok"}]


def test_suggest_max_retries_exhausted_raises_step_error(tmp_path):
    cfg = _make_suggest_cfg(max_retries=2)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = []

    fail_result = HookResult(cmd="false", rc=1, output="fail\n", duration=0.1)

    step = ImplementSpecStep()
    with (
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._load_suggest_template", return_value="tpl"),
        patch(
            "draft.steps.implement_spec._suggest_checks",
            return_value=[{"cmd": "false"}],
        ),
        patch(
            "draft.steps.implement_spec._run_suggested_checks",
            return_value=[fail_result],
        ),
        pytest.raises(StepError) as exc_info,
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    assert exc_info.value.step_name == "implement-spec"


def test_suggest_template_loaded_once_across_retries(tmp_path):
    cfg = _make_suggest_cfg(max_retries=3)
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = []

    load_count = 0

    def counting_load():
        nonlocal load_count
        load_count += 1
        return "suggest template"

    step = ImplementSpecStep()
    with (
        patch(
            "draft.steps.implement_spec._load_suggest_template",
            side_effect=counting_load,
        ),
        patch("draft.steps.implement_spec._has_changes", return_value=True),
        patch("draft.steps.implement_spec._suggest_checks", return_value=[]),
        patch("draft.steps.implement_spec._run_suggested_checks", return_value=[]),
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


# --- Template content tests ---


def test_bundled_suggest_checks_has_required_markers():
    from importlib.resources import files

    content = (
        files("draft.steps.implement_spec").joinpath("suggest_checks.md").read_text()
    )
    assert "{{SPEC}}" in content
    assert "{{CHANGED_FILES}}" in content
    assert "{{STATIC_CHECKS}}" in content
    assert "{{PER_CHECK_TIMEOUT}}" in content


def test_bundled_suggest_checks_has_no_diff_marker():
    from importlib.resources import files

    content = (
        files("draft.steps.implement_spec").joinpath("suggest_checks.md").read_text()
    )
    assert "{{DIFF}}" not in content


# --- LiveStatusSummarizer integration ---


def _run_step_with_patches(ctx, engine, lifecycle, isatty=False):
    step = ImplementSpecStep()
    with (
        patch("sys.stdout.isatty", return_value=isatty),
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


def test_summarizer_not_created_when_not_tty(tmp_path):
    cfg = {"max_retries": 1, "timeout": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = []

    with patch(
        "draft.steps.implement_spec.LiveStatusSummarizer"
    ) as mock_summarizer_cls:
        _run_step_with_patches(ctx, engine, lifecycle, isatty=False)

    mock_summarizer_cls.assert_not_called()


def test_summarizer_created_and_stopped_when_tty(tmp_path):
    cfg = {"max_retries": 1, "timeout": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = []

    mock_instance = MagicMock()
    mock_instance.start.return_value = mock_instance

    with patch(
        "draft.steps.implement_spec.LiveStatusSummarizer", return_value=mock_instance
    ):
        _run_step_with_patches(ctx, engine, lifecycle, isatty=True)

    mock_instance.start.assert_called_once()
    mock_instance.stop.assert_called_once()


def test_summarizer_stopped_in_finally_when_run_llm_raises(tmp_path):
    cfg = {"max_retries": 1, "timeout": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    engine.run_llm.side_effect = RuntimeError("boom")
    lifecycle = MagicMock()

    mock_instance = MagicMock()
    mock_instance.start.return_value = mock_instance

    step = ImplementSpecStep()
    with (
        patch("sys.stdout.isatty", return_value=True),
        patch(
            "draft.steps.implement_spec.LiveStatusSummarizer",
            return_value=mock_instance,
        ),
        pytest.raises((StepError, RuntimeError)),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    mock_instance.stop.assert_called_once()


# --- attempt prefix tests ---


def _run_step_capturing_s(cfg, tmp_path, lifecycle, *, isatty=False, s=None):
    if s is None:
        s = MagicMock()
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine(s=s)
    step = ImplementSpecStep()
    with (
        patch("sys.stdout.isatty", return_value=isatty),
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
    return s


def test_first_attempt_status_has_no_prefix(tmp_path):
    cfg = {"max_retries": 1, "timeout": 60, "suggest_extra_checks": False}
    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = []

    s = _run_step_capturing_s(cfg, tmp_path, lifecycle)

    calls = [c.args[0] for c in s.update.call_args_list]
    assert all(
        c in ("implementing", "verifying", "writing commit", "ok") for c in calls
    )
    assert not any("attempt" in c or "—" in c for c in calls)


def test_second_attempt_status_has_prefix(tmp_path):
    cfg = {"max_retries": 3, "timeout": 60, "suggest_extra_checks": False}
    lifecycle = MagicMock()
    failing = HookResult(cmd="cmd", rc=1, output="fail", duration=0.1)
    lifecycle.run_hooks.side_effect = [[failing], []]

    s = _run_step_capturing_s(cfg, tmp_path, lifecycle)

    calls = [c.args[0] for c in s.update.call_args_list]
    implementing_indices = [
        i for i, c in enumerate(calls) if c.endswith("implementing")
    ]
    assert len(implementing_indices) == 2
    boundary = implementing_indices[1]
    attempt1_calls = calls[:boundary]
    attempt2_calls = calls[boundary:]
    assert not any("attempt" in c for c in attempt1_calls)
    non_ok = [c for c in attempt2_calls if c != "ok"]
    assert all(c.startswith("attempt 2/3 — ") for c in non_ok)


def test_max_retries_exhausted_last_status_has_prefix(tmp_path):
    cfg = {"max_retries": 3, "timeout": 60, "suggest_extra_checks": False}
    lifecycle = MagicMock()
    failing = HookResult(cmd="cmd", rc=1, output="fail", duration=0.1)
    lifecycle.run_hooks.return_value = [failing]

    s = MagicMock()
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine(s=s)
    step = ImplementSpecStep()
    with (
        patch("sys.stdout.isatty", return_value=False),
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
        pytest.raises(StepError),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = [c.args[0] for c in s.update.call_args_list]
    assert calls[0] == "implementing"
    assert calls[-1].startswith("attempt 3/3 — ")


def test_summarizer_constructed_with_empty_prefix_on_first_attempt(tmp_path):
    cfg = {"max_retries": 1, "timeout": 60, "suggest_extra_checks": False}
    lifecycle = MagicMock()
    lifecycle.run_hooks.return_value = []

    mock_instance = MagicMock()
    mock_instance.start.return_value = mock_instance

    with patch(
        "draft.steps.implement_spec.LiveStatusSummarizer", return_value=mock_instance
    ) as mock_cls:
        _run_step_capturing_s(cfg, tmp_path, lifecycle, isatty=True)

    mock_cls.assert_called_once()
    _, kwargs = mock_cls.call_args
    assert kwargs["prefix"] == ""


def test_summarizer_constructed_with_attempt_prefix_on_second_attempt(tmp_path):
    cfg = {"max_retries": 2, "timeout": 60, "suggest_extra_checks": False}
    lifecycle = MagicMock()
    failing = HookResult(cmd="cmd", rc=1, output="fail", duration=0.1)
    lifecycle.run_hooks.side_effect = [[failing], []]

    mock_instance = MagicMock()
    mock_instance.start.return_value = mock_instance

    with patch(
        "draft.steps.implement_spec.LiveStatusSummarizer", return_value=mock_instance
    ) as mock_cls:
        _run_step_capturing_s(cfg, tmp_path, lifecycle, isatty=True)

    assert mock_cls.call_count == 2
    prefixes = [call.kwargs["prefix"] for call in mock_cls.call_args_list]
    assert prefixes[0] == ""
    assert prefixes[1] == "attempt 2/2 — "


def test_bundled_summarize_status_has_tail_placeholder():
    from importlib.resources import files

    content = (
        files("draft.steps.implement_spec").joinpath("summarize_status.md").read_text()
    )
    assert "{{TAIL}}" in content
