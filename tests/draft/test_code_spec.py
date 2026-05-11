import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from draft.steps.implement_spec import (
    _build_claude_cmd,
    _generate_commit_message,
    _has_changes,
    _load_template,
    _render_verify_commands,
    _run_git_capture,
    _run_git_capture_allow_fail,
    ImplementSpecStep,
)
from pipeline import StepError
from pipeline.runner import TIMEOUT_EXIT


_BUNDLED_MARKER = "{{SPEC}}"


def _make_ctx(cfg, spec="my spec", verify_errors="", tmp_path=None):
    ctx = MagicMock()
    ctx.config.return_value = cfg
    ctx.get.side_effect = lambda key, default=None: {"wt_dir": "/wt", "spec": spec}.get(key, default)
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


def test_build_claude_cmd_substitutes_spec(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("Spec: {{SPEC}} Commands: {{VERIFY_COMMANDS}} Errors: {{VERIFY_ERRORS}}")
    template = tpl.read_text()

    ctx = MagicMock()
    ctx.get.return_value = "my spec content"
    ctx.step_get.return_value = ""

    cmd = _build_claude_cmd(ctx, template, "")
    prompt = cmd[2]
    assert "my spec content" in prompt
    assert "{{SPEC}}" not in prompt


def test_build_claude_cmd_uses_bundled_template():
    bundled = _load_template({})
    ctx = MagicMock()
    ctx.get.return_value = "the spec"
    ctx.step_get.return_value = ""
    cmd = _build_claude_cmd(ctx, bundled, "")
    prompt = cmd[2]
    assert "the spec" in prompt
    assert "{{SPEC}}" not in prompt


def test_build_claude_cmd_substitutes_verify_commands(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_COMMANDS}}\n{{VERIFY_ERRORS}}")
    template = tpl.read_text()

    ctx = MagicMock()
    ctx.get.return_value = "spec"
    ctx.step_get.return_value = ""

    verify_commands = "## Verify commands\n\n```bash\nmake test\n```"
    cmd = _build_claude_cmd(ctx, template, verify_commands)
    prompt = cmd[2]
    assert "make test" in prompt
    assert "{{VERIFY_COMMANDS}}" not in prompt


def test_build_claude_cmd_empty_verify_commands_collapses_marker(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_COMMANDS}}\n{{VERIFY_ERRORS}}")
    template = tpl.read_text()

    ctx = MagicMock()
    ctx.get.return_value = "spec"
    ctx.step_get.return_value = ""

    cmd = _build_claude_cmd(ctx, template, "")
    prompt = cmd[2]
    assert "{{VERIFY_COMMANDS}}" not in prompt
    assert "## Verify commands" not in prompt


def test_build_claude_cmd_template_without_verify_commands_marker(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_ERRORS}}")
    template = tpl.read_text()

    ctx = MagicMock()
    ctx.get.return_value = "spec"
    ctx.step_get.return_value = ""

    cmd = _build_claude_cmd(ctx, template, "## Verify commands\n\n```bash\nmake test\n```")
    prompt = cmd[2]
    assert "spec" in prompt
    assert "{{SPEC}}" not in prompt


def test_build_claude_cmd_all_substitutions_work_together(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text("{{SPEC}}\n{{VERIFY_COMMANDS}}\n{{VERIFY_ERRORS}}")
    template = tpl.read_text()

    ctx = MagicMock()
    ctx.get.return_value = "my spec"
    ctx.step_get.return_value = "errors here"

    verify_commands = "## Verify commands\n\n```bash\nmake test\n```"
    cmd = _build_claude_cmd(ctx, template, verify_commands)
    prompt = cmd[2]
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
    content = files("draft.steps.implement_spec").joinpath("commit_message.md").read_text()
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

    with patch.object(Path, "read_text", counting_read):
        with patch("draft.steps.implement_spec._has_changes", return_value=True), \
             patch("draft.steps.implement_spec._generate_commit_message", return_value=("Add feature", False)), \
             patch("draft.steps.implement_spec._run_git_capture", return_value="abc123"), \
             patch("draft.steps.implement_spec._run_git_capture_allow_fail",
                   return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            step.run(ctx, engine, lifecycle)

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
    with patch("draft.steps.implement_spec._has_changes", return_value=True), \
         patch("draft.steps.implement_spec._generate_commit_message", return_value=("Add feature", False)), \
         patch("draft.steps.implement_spec._run_git_capture", return_value="abc123"), \
         patch("draft.steps.implement_spec._run_git_capture_allow_fail",
               return_value=subprocess.CompletedProcess([], 0, b"", b"")):
        step.run(ctx, engine, lifecycle)

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
    with patch("draft.steps.implement_spec._has_changes", return_value=True), \
         patch("draft.steps.implement_spec._generate_commit_message", return_value=("msg", False)), \
         patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"), \
         patch("draft.steps.implement_spec._run_git_capture_allow_fail",
               return_value=subprocess.CompletedProcess([], 0, b"", b"")):
        step.run(ctx, engine, lifecycle)

    prompt = engine.run_command.call_args[1]["cmd"][2]
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
    with patch("draft.steps.implement_spec._has_changes", return_value=True), \
         patch("draft.steps.implement_spec._generate_commit_message", return_value=("msg", False)), \
         patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"), \
         patch("draft.steps.implement_spec._run_git_capture_allow_fail",
               return_value=subprocess.CompletedProcess([], 0, b"", b"")):
        step.run(ctx, engine, lifecycle)

    prompt = engine.run_command.call_args[1]["cmd"][2]
    assert "## Verify commands" not in prompt


def test_custom_template_file_removed_before_step_runs(tmp_path):
    tpl = tmp_path / "gone.md"
    cfg = {"max_retries": 1, "timeout": 60, "prompt_template": str(tpl)}

    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()

    step = ImplementSpecStep()
    with pytest.raises(StepError) as exc_info:
        step.run(ctx, engine, lifecycle)
    assert exc_info.value.step_name == "implement-spec"


def test_no_changes_after_agent_loops_and_records_verify_error(tmp_path):
    cfg = {"max_retries": 2, "timeout": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = MagicMock()

    step = ImplementSpecStep()
    with patch("draft.steps.implement_spec._has_changes", return_value=False), \
         patch("draft.steps.implement_spec._generate_commit_message") as mock_gen:
        with pytest.raises(StepError) as exc_info:
            step.run(ctx, engine, lifecycle)

    assert exc_info.value.step_name == "implement-spec"
    mock_gen.assert_not_called()

    calls = ctx.step_set.call_args_list
    verify_error_calls = [c for c in calls if c.args[1] == "verify_errors"]
    assert any("agent produced no changes" in str(c.args[2]) for c in verify_error_calls)


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
    with patch("draft.steps.implement_spec._has_changes", return_value=True), \
         patch("draft.steps.implement_spec._generate_commit_message", return_value=("Add foo", False)) as mock_gen, \
         patch("draft.steps.implement_spec._run_git_capture", return_value="deadbeef\n"), \
         patch("draft.steps.implement_spec._run_git_capture_allow_fail",
               return_value=subprocess.CompletedProcess([], 0, b"", b"")):
        step.run(ctx, engine, lifecycle)

    assert engine.run_command.call_count == 2
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
    with patch("draft.steps.implement_spec._has_changes", return_value=True), \
         patch("draft.steps.implement_spec._generate_commit_message", return_value=("Subject line\n\nBody", False)), \
         patch("draft.steps.implement_spec._run_git_capture", return_value="abc\n") as mock_git, \
         patch("draft.steps.implement_spec._run_git_capture_allow_fail",
               return_value=subprocess.CompletedProcess([], 0, b"", b"")) as mock_commit:
        step.run(ctx, engine, lifecycle)

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
    with patch("draft.steps.implement_spec._has_changes", return_value=True), \
         patch("draft.steps.implement_spec._generate_commit_message", return_value=("Implement spec", True)), \
         patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"), \
         patch("draft.steps.implement_spec._run_git_capture_allow_fail",
               return_value=subprocess.CompletedProcess([], 0, b"", b"")):
        step.run(ctx, engine, lifecycle)

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
    with patch("draft.steps.implement_spec._has_changes", return_value=True), \
         patch("draft.steps.implement_spec._generate_commit_message", return_value=("Fix thing", False)), \
         patch("draft.steps.implement_spec._run_git_capture", return_value="sha123\n"), \
         patch("draft.steps.implement_spec._run_git_capture_allow_fail", side_effect=commit_side_effect):
        step.run(ctx, engine, lifecycle)

    assert engine.run_command.call_count == 2

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
    with patch("draft.steps.implement_spec._has_changes", return_value=True), \
         patch("draft.steps.implement_spec._generate_commit_message", return_value=("Fix thing", False)), \
         patch("draft.steps.implement_spec._run_git_capture", return_value="sha\n"), \
         patch("draft.steps.implement_spec._run_git_capture_allow_fail", side_effect=commit_side_effect):
        step.run(ctx, engine, lifecycle)

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
    with patch("draft.steps.implement_spec._has_changes", return_value=True), \
         patch("draft.steps.implement_spec._generate_commit_message") as mock_gen:
        with pytest.raises(StepError) as exc_info:
            step.run(ctx, engine, lifecycle)

    assert exc_info.value.step_name == "implement-spec"
    mock_gen.assert_not_called()


# Tests for _generate_commit_message


_GIT_OK = subprocess.CompletedProcess([], 0, b"", b"")


def test_generate_commit_message_returns_trimmed_stdout(tmp_path):
    log = tmp_path / "msg.log"
    # 2 git calls (diff, status) + 1 claude call
    results = [_GIT_OK, _GIT_OK, subprocess.CompletedProcess([], 0, b"  Add feature  \n", b"")]
    with patch("subprocess.run", side_effect=results) as mock_run:
        msg, used_fallback = _generate_commit_message("spec", "/wt", log, 120, 3)

    assert msg == "Add feature"
    assert used_fallback is False
    assert mock_run.call_count == 3  # 2 git + 1 claude


def test_generate_commit_message_retries_on_empty(tmp_path):
    log = tmp_path / "msg.log"
    # 2 git calls then 2 claude attempts
    results = [
        _GIT_OK, _GIT_OK,
        subprocess.CompletedProcess([], 0, b"\n", b""),
        subprocess.CompletedProcess([], 0, b"Add feature", b""),
    ]
    with patch("subprocess.run", side_effect=results) as mock_run:
        msg, used_fallback = _generate_commit_message("spec", "/wt", log, 120, 3)

    assert msg == "Add feature"
    assert used_fallback is False
    assert mock_run.call_count == 4  # 2 git + 2 claude


def test_generate_commit_message_retries_on_non_zero(tmp_path):
    log = tmp_path / "msg.log"
    results = [
        _GIT_OK, _GIT_OK,
        subprocess.CompletedProcess([], 2, b"", b""),
        subprocess.CompletedProcess([], 0, b"Add feature", b""),
    ]
    with patch("subprocess.run", side_effect=results) as mock_run:
        msg, used_fallback = _generate_commit_message("spec", "/wt", log, 120, 3)

    assert msg == "Add feature"
    assert used_fallback is False
    assert mock_run.call_count == 4  # 2 git + 2 claude


def test_generate_commit_message_retries_on_timeout(tmp_path):
    log = tmp_path / "msg.log"
    # git calls succeed, first claude times out, second succeeds
    results = [
        _GIT_OK, _GIT_OK,
        subprocess.TimeoutExpired(cmd=["claude"], timeout=120),
        subprocess.CompletedProcess([], 0, b"Add feature", b""),
    ]
    with patch("subprocess.run", side_effect=results) as mock_run:
        msg, used_fallback = _generate_commit_message("spec", "/wt", log, 120, 3)

    assert msg == "Add feature"
    assert used_fallback is False
    assert mock_run.call_count == 4  # 2 git + 2 claude


def test_generate_commit_message_falls_back_after_three_failures(tmp_path, capsys):
    log = tmp_path / "msg.log"
    # 2 git calls then 3 empty claude attempts
    results = [
        _GIT_OK, _GIT_OK,
        subprocess.CompletedProcess([], 0, b"", b""),
        subprocess.CompletedProcess([], 0, b"", b""),
        subprocess.CompletedProcess([], 0, b"", b""),
    ]
    with patch("subprocess.run", side_effect=results) as mock_run:
        msg, used_fallback = _generate_commit_message("spec", "/wt", log, 120, 3)

    assert msg == "Implement spec"
    assert used_fallback is True
    assert mock_run.call_count == 5  # 2 git + 3 claude
    captured = capsys.readouterr()
    assert "fallback" in captured.err.lower() or "Implement spec" in captured.err


def test_generate_commit_message_logs_to_file(tmp_path):
    log = tmp_path / "msg.log"
    results = [
        _GIT_OK, _GIT_OK,
        subprocess.CompletedProcess([], 0, b"\n", b""),
        subprocess.CompletedProcess([], 0, b"Add feature\n", b""),
    ]
    with patch("subprocess.run", side_effect=results):
        _generate_commit_message("spec", "/wt", log, 120, 3)

    content = log.read_text()
    assert "=== commit-message attempt 1 @" in content
    assert "=== commit-message attempt 2 @" in content
    assert "--- selected commit message (attempt 2) ---" in content
    assert "Add feature" in content
