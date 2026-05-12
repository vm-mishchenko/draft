import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from draft.steps.babysit_pr import (
    BabysitPrStep,
    _generate_commit_message,
    _render_verify_commands,
)
from pipeline.runner import LLMResult


def _make_ctx(cfg, tmp_path=None, verify_errors=""):
    ctx = MagicMock()
    ctx.config.return_value = cfg
    ctx.get.side_effect = lambda key, default=None: {
        "wt_dir": "/wt",
        "pr_url": "https://github.com/org/repo/pull/1",
    }.get(key, default)
    ctx.step_get.return_value = verify_errors
    ctx.log_path.return_value = Path("/tmp/babysit.log")
    ctx.run_dir = tmp_path if tmp_path is not None else Path("/tmp")
    return ctx


def _make_engine(s=None):
    engine = MagicMock()
    stage_ctx = MagicMock()
    stage_ctx.__enter__ = MagicMock(return_value=s or MagicMock())
    stage_ctx.__exit__ = MagicMock(return_value=False)
    engine.stage.return_value = stage_ctx
    engine.run_command.return_value = None
    engine.run_llm.return_value = LLMResult(rc=0, final_text="Fix CI check failure")
    return engine


def _make_lifecycle(hooks=None, hook_results=None):
    lifecycle = MagicMock()
    lifecycle.get_hooks.return_value = hooks or []
    lifecycle.run_hooks.return_value = hook_results or []
    return lifecycle


# --- Template tests ---


def test_babysit_prompt_has_do_not_commit_section():
    from importlib.resources import files

    content = files("draft.steps.babysit_pr").joinpath("babysit_pr.md").read_text()
    assert "must not run `git commit`" in content
    assert "Do not commit" in content


def test_babysit_prompt_has_verify_commands_placeholder():
    from importlib.resources import files

    content = files("draft.steps.babysit_pr").joinpath("babysit_pr.md").read_text()
    assert "{{VERIFY_COMMANDS}}" in content


def test_babysit_prompt_no_commit_instruction():
    from importlib.resources import files

    content = files("draft.steps.babysit_pr").joinpath("babysit_pr.md").read_text()
    assert "Commit your changes" not in content


def test_babysit_commit_message_has_placeholders():
    from importlib.resources import files

    content = files("draft.steps.babysit_pr").joinpath("commit_message.md").read_text()
    assert "{{DIFF}}" in content
    assert "{{VERIFY_ERRORS}}" in content
    assert "{{SPEC}}" not in content


# --- _render_verify_commands tests (duplicated from implement_spec) ---


def test_render_verify_commands_empty_list():
    assert _render_verify_commands([]) == ""


def test_render_verify_commands_single_entry():
    result = _render_verify_commands([{"cmd": "make test"}])
    assert "## Verify commands" in result
    assert "make test" in result
    assert "```bash" in result


def test_render_verify_commands_multiple_entries():
    result = _render_verify_commands([{"cmd": "make lint"}, {"cmd": "make test"}])
    assert result.index("make lint") < result.index("make test")


# --- Prompt verify_commands substitution ---


def test_prompt_contains_verify_commands_when_configured(tmp_path):
    cfg = {"max_retries": 1, "timeout": 60, "checks_delay": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = _make_lifecycle(hooks=[{"cmd": "make test"}])

    step = BabysitPrStep()
    with (
        patch(
            "draft.steps.babysit_pr._check_ci",
            return_value={"success": 0, "failure": 1, "pending": 0},
        ),
        patch("draft.steps.babysit_pr._has_changes", return_value=True),
        patch(
            "draft.steps.babysit_pr._generate_commit_message",
            return_value=("Fix CI", False),
        ),
        patch("draft.steps.babysit_pr._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.babysit_pr._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    prompt = engine.run_llm.call_args_list[0][1]["prompt"]
    assert "## Verify commands" in prompt
    assert "make test" in prompt


def test_prompt_no_verify_commands_section_when_empty(tmp_path):
    cfg = {"max_retries": 1, "timeout": 60, "checks_delay": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = _make_lifecycle(hooks=[])

    step = BabysitPrStep()
    with (
        patch(
            "draft.steps.babysit_pr._check_ci",
            return_value={"success": 0, "failure": 1, "pending": 0},
        ),
        patch("draft.steps.babysit_pr._has_changes", return_value=True),
        patch(
            "draft.steps.babysit_pr._generate_commit_message",
            return_value=("Fix CI", False),
        ),
        patch("draft.steps.babysit_pr._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.babysit_pr._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    prompt = engine.run_llm.call_args_list[0][1]["prompt"]
    assert "## Verify commands" not in prompt
    assert "{{VERIFY_COMMANDS}}" not in prompt


# --- BabysitPrStep.run flow tests ---


def test_ci_green_branch_clean_returns_without_git(tmp_path):
    cfg = {"max_retries": 5, "timeout": 60, "checks_delay": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = _make_lifecycle()

    step = BabysitPrStep()
    with (
        patch(
            "draft.steps.babysit_pr._check_ci",
            return_value={"success": 3, "failure": 0, "pending": 0},
        ),
        patch("draft.steps.babysit_pr._is_branch_clean", return_value=True),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    engine.run_llm.assert_not_called()
    engine.run_command.assert_not_called()


def test_no_changes_after_agent_sets_verify_errors_and_continues(tmp_path):
    cfg = {"max_retries": 2, "timeout": 60, "checks_delay": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = _make_lifecycle()

    call_count = 0

    def ci_side_effect(pr_url):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return {"success": 0, "failure": 1, "pending": 0}
        return {"success": 1, "failure": 0, "pending": 0}

    step = BabysitPrStep()
    with (
        patch("draft.steps.babysit_pr._check_ci", side_effect=ci_side_effect),
        patch("draft.steps.babysit_pr._has_changes", return_value=False),
        patch("draft.steps.babysit_pr._is_branch_clean", return_value=True),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    verify_calls = [c for c in calls if c.args[1] == "verify_errors"]
    assert any("agent produced no changes" in str(c.args[2]) for c in verify_calls)
    engine.run_command.assert_not_called()


def test_verify_hooks_fail_sets_errors_no_commit(tmp_path):
    cfg = {"max_retries": 2, "timeout": 60, "checks_delay": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()

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

    lifecycle = _make_lifecycle(hooks=[])
    lifecycle.run_hooks.side_effect = hooks_side_effect

    step = BabysitPrStep()
    with (
        patch(
            "draft.steps.babysit_pr._check_ci",
            return_value={"success": 0, "failure": 1, "pending": 0},
        ),
        patch("draft.steps.babysit_pr._has_changes", return_value=True),
        patch(
            "draft.steps.babysit_pr._generate_commit_message",
            return_value=("Fix CI", False),
        ) as mock_gen,
        patch("draft.steps.babysit_pr._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.babysit_pr._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
        patch("draft.steps.babysit_pr._is_branch_clean", return_value=True),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    verify_calls = [c for c in calls if c.args[1] == "verify_errors"]
    assert any("boom" in str(c.args[2]) for c in verify_calls)
    assert mock_gen.call_count == 1


def test_verify_passes_commits_and_pushes(tmp_path):
    cfg = {"max_retries": 1, "timeout": 60, "checks_delay": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = _make_lifecycle()

    step = BabysitPrStep()
    with (
        patch(
            "draft.steps.babysit_pr._check_ci",
            return_value={"success": 0, "failure": 1, "pending": 0},
        ),
        patch("draft.steps.babysit_pr._has_changes", return_value=True),
        patch(
            "draft.steps.babysit_pr._generate_commit_message",
            return_value=("Fix flaky test", False),
        ),
        patch("draft.steps.babysit_pr._run_git_capture", return_value="deadbeef\n"),
        patch(
            "draft.steps.babysit_pr._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    sha_calls = [c for c in calls if c.args[1] == "commit_sha"]
    assert len(sha_calls) == 1
    assert sha_calls[0].args[2] == "deadbeef"

    fallback_calls = [c for c in calls if c.args[1] == "commit_message_fallback"]
    assert len(fallback_calls) == 1
    assert fallback_calls[0].args[2] is False

    clear_err_calls = [
        c for c in calls if c.args[1] == "verify_errors" and c.args[2] == ""
    ]
    assert len(clear_err_calls) >= 1

    engine.run_command.assert_called_once()
    push_call = engine.run_command.call_args
    assert push_call[1]["cmd"] == ["git", "push", "origin", "HEAD"]


def test_commit_message_fallback_used_and_recorded(tmp_path):
    cfg = {"max_retries": 1, "timeout": 60, "checks_delay": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = _make_lifecycle()

    step = BabysitPrStep()
    with (
        patch(
            "draft.steps.babysit_pr._check_ci",
            return_value={"success": 0, "failure": 1, "pending": 0},
        ),
        patch("draft.steps.babysit_pr._has_changes", return_value=True),
        patch(
            "draft.steps.babysit_pr._generate_commit_message",
            return_value=("Fix CI checks", True),
        ),
        patch("draft.steps.babysit_pr._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.babysit_pr._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    fallback_calls = [c for c in calls if c.args[1] == "commit_message_fallback"]
    assert len(fallback_calls) == 1
    assert fallback_calls[0].args[2] is True


def test_pre_commit_hook_failure_sets_verify_errors_no_push(tmp_path):
    cfg = {"max_retries": 2, "timeout": 60, "checks_delay": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()
    lifecycle = _make_lifecycle()

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

    step = BabysitPrStep()
    with (
        patch(
            "draft.steps.babysit_pr._check_ci",
            return_value={"success": 0, "failure": 1, "pending": 0},
        ),
        patch("draft.steps.babysit_pr._has_changes", return_value=True),
        patch(
            "draft.steps.babysit_pr._generate_commit_message",
            return_value=("Fix CI", False),
        ),
        patch("draft.steps.babysit_pr._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.babysit_pr._run_git_capture_allow_fail",
            side_effect=commit_side_effect,
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    calls = ctx.step_set.call_args_list
    verify_calls = [c for c in calls if c.args[1] == "verify_errors"]
    first_error = next(c.args[2] for c in verify_calls if c.args[2])
    assert "Pre-commit hook failures" in first_error

    sha_calls = [c for c in calls if c.args[1] == "commit_sha"]
    assert len(sha_calls) == 1

    assert engine.run_command.call_count == 1


def test_get_hooks_called_once_across_attempts(tmp_path):
    cfg = {"max_retries": 3, "timeout": 60, "checks_delay": 60}
    ctx = _make_ctx(cfg, tmp_path=tmp_path)
    engine = _make_engine()

    fail_result = MagicMock()
    fail_result.rc = 1
    fail_result.cmd = "make test"
    fail_result.output = "fail"

    call_count = 0

    def hooks_side_effect(step_name, event):
        nonlocal call_count
        if event == "verify":
            call_count += 1
            if call_count == 1:
                return [fail_result]
        return []

    lifecycle = _make_lifecycle(hooks=[{"cmd": "make test"}])
    lifecycle.run_hooks.side_effect = hooks_side_effect

    step = BabysitPrStep()
    with (
        patch(
            "draft.steps.babysit_pr._check_ci",
            return_value={"success": 0, "failure": 1, "pending": 0},
        ),
        patch("draft.steps.babysit_pr._has_changes", return_value=True),
        patch(
            "draft.steps.babysit_pr._generate_commit_message",
            return_value=("Fix CI", False),
        ),
        patch("draft.steps.babysit_pr._run_git_capture", return_value="sha\n"),
        patch(
            "draft.steps.babysit_pr._run_git_capture_allow_fail",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
    ):
        step.run(ctx, engine, lifecycle, MagicMock())

    lifecycle.get_hooks.assert_called_once_with("babysit-pr", "verify")


# --- _generate_commit_message tests ---


def test_generate_commit_message_returns_message(tmp_path):
    log = tmp_path / "msg.log"
    engine = MagicMock()
    engine.run_llm.return_value = LLMResult(rc=0, final_text="  Fix flaky test  ")
    with patch("draft.steps.babysit_pr._run_git_capture", return_value=""):
        msg, used_fallback = _generate_commit_message(
            "", "/wt", log, 120, 3, engine, MagicMock()
        )
    assert msg == "Fix flaky test"
    assert used_fallback is False
    assert engine.run_llm.call_count == 1


def test_generate_commit_message_retries_on_empty(tmp_path):
    log = tmp_path / "msg.log"
    engine = MagicMock()
    engine.run_llm.side_effect = [
        LLMResult(rc=0, final_text=""),
        LLMResult(rc=0, final_text="Fix CI failure"),
    ]
    with patch("draft.steps.babysit_pr._run_git_capture", return_value=""):
        msg, used_fallback = _generate_commit_message(
            "", "/wt", log, 120, 3, engine, MagicMock()
        )
    assert msg == "Fix CI failure"
    assert used_fallback is False
    assert engine.run_llm.call_count == 2


def test_generate_commit_message_falls_back_after_exhaustion(tmp_path, capsys):
    log = tmp_path / "msg.log"
    engine = MagicMock()
    engine.run_llm.side_effect = [
        LLMResult(rc=0, final_text=""),
        LLMResult(rc=0, final_text=""),
        LLMResult(rc=0, final_text=""),
    ]
    with patch("draft.steps.babysit_pr._run_git_capture", return_value=""):
        msg, used_fallback = _generate_commit_message(
            "", "/wt", log, 120, 3, engine, MagicMock()
        )
    assert msg == "Fix CI checks"
    assert used_fallback is True
    assert engine.run_llm.call_count == 3
    captured = capsys.readouterr()
    assert "fallback" in captured.err.lower() or "Fix CI checks" in captured.err


def test_generate_commit_message_includes_verify_errors_in_prompt(tmp_path):
    log = tmp_path / "msg.log"
    engine = MagicMock()
    engine.run_llm.return_value = LLMResult(rc=0, final_text="Fix thing")

    with patch("draft.steps.babysit_pr._run_git_capture", return_value=""):
        _generate_commit_message(
            "some previous errors", "/wt", log, 120, 3, engine, MagicMock()
        )

    prompt = engine.run_llm.call_args[1]["prompt"]
    assert "some previous errors" in prompt
    assert "previous verify failures" in prompt


def test_generate_commit_message_no_verify_errors_section_when_empty(tmp_path):
    log = tmp_path / "msg.log"
    engine = MagicMock()
    engine.run_llm.return_value = LLMResult(rc=0, final_text="Fix thing")

    with patch("draft.steps.babysit_pr._run_git_capture", return_value=""):
        _generate_commit_message("", "/wt", log, 120, 3, engine, MagicMock())

    prompt = engine.run_llm.call_args[1]["prompt"]
    assert "previous verify failures" not in prompt


def test_generate_commit_message_logs_to_file(tmp_path):
    log = tmp_path / "msg.log"
    engine = MagicMock()
    engine.run_llm.side_effect = [
        LLMResult(rc=0, final_text=""),
        LLMResult(rc=0, final_text="Fix CI"),
    ]
    with patch("draft.steps.babysit_pr._run_git_capture", return_value=""):
        _generate_commit_message("", "/wt", log, 120, 3, engine, MagicMock())

    content = log.read_text()
    assert "--- selected commit message (attempt 2) ---" in content
    assert "Fix CI" in content
