import subprocess
from unittest.mock import MagicMock, patch

import pytest

import draft.steps.open_pr as pr_open_mod
from draft.steps.open_pr import STEP_DIR, OpenPrStep
from pipeline import StepError
from pipeline.runner import TIMEOUT_EXIT, LLMResult

_PARSEABLE_FINAL_TEXT = (
    "<<<PR-TITLE>>>\nT\n<<</PR-TITLE>>>\n<<<PR-BODY>>>\nB\n<<</PR-BODY>>>"
)


def _make_ctx(
    cfg, tmp_path, branch="draft/feat", repo="/repo", base_branch="main", wt_dir="/wt"
):
    ctx = MagicMock()
    ctx.config.return_value = cfg
    ctx.get.side_effect = lambda key, default=None: {
        "repo": repo,
        "branch": branch,
        "base_branch": base_branch,
        "wt_dir": wt_dir,
    }.get(key, default)
    ctx.log_path.side_effect = lambda name: tmp_path / f"{name}.log"
    (tmp_path / "open-pr-claude.log").write_text("")
    (tmp_path / "open-pr.log").write_text("")
    return ctx


def _make_engine():
    engine = MagicMock()
    stage_cm = MagicMock()
    stage_cm.__enter__ = MagicMock(return_value=MagicMock())
    stage_cm.__exit__ = MagicMock(return_value=False)
    engine.stage.return_value = stage_cm
    engine.run_command.return_value = 0
    engine.run_llm.return_value = LLMResult(rc=0, final_text=_PARSEABLE_FINAL_TEXT)
    return engine


def _subprocess_factory(diff_stdout=b"", log_stdout=b""):
    def side_effect(cmd, **kwargs):
        if "diff" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=diff_stdout, stderr=b""
            )
        if "log" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=log_stdout, stderr=b""
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=b"", stderr=b""
        )

    return side_effect


def test_custom_body_path_used_in_prompt(tmp_path):
    tpl = tmp_path / "my_template.md"
    tpl.write_text("## Summary\n")

    cfg = {"timeout": 300, "title_prefix": "", "pr_body_template": str(tpl)}
    ctx = _make_ctx(cfg, tmp_path)
    engine = _make_engine()

    with patch.object(pr_open_mod, "subprocess") as mock_sub:
        mock_sub.run.side_effect = _subprocess_factory()
        mock_sub.TimeoutExpired = subprocess.TimeoutExpired
        OpenPrStep().run(ctx, engine, MagicMock(), MagicMock())

    prompt = engine.run_llm.call_args.kwargs["prompt"]
    assert "## Summary" in prompt


def test_bundled_default_used_when_no_template(tmp_path):
    cfg = {"timeout": 300, "title_prefix": ""}
    ctx = _make_ctx(cfg, tmp_path)
    engine = _make_engine()

    with patch.object(pr_open_mod, "subprocess") as mock_sub:
        mock_sub.run.side_effect = _subprocess_factory()
        mock_sub.TimeoutExpired = subprocess.TimeoutExpired
        OpenPrStep().run(ctx, engine, MagicMock(), MagicMock())

    prompt = engine.run_llm.call_args.kwargs["prompt"]
    bundled_content = (STEP_DIR / "pull-request-template.md").read_text()
    assert bundled_content[:50] in prompt


def test_missing_body_path_raises_step_error_without_claude(tmp_path, capsys):
    missing = tmp_path / "gone.md"
    cfg = {"timeout": 300, "title_prefix": "", "pr_body_template": str(missing)}
    ctx = _make_ctx(cfg, tmp_path)
    engine = _make_engine()

    with patch.object(pr_open_mod, "subprocess") as mock_sub:
        step = OpenPrStep()
        with pytest.raises(StepError) as exc_info:
            step.run(ctx, engine, MagicMock(), MagicMock())

    assert exc_info.value.step_name == "open-pr"
    assert exc_info.value.exit_code == 1
    engine.run_llm.assert_not_called()
    engine.run_command.assert_not_called()
    mock_sub.run.assert_not_called()
    captured = capsys.readouterr()
    assert str(missing) in captured.err


def test_no_regression_bundled_path_under_step_dir():
    bundled = STEP_DIR / "pull-request-template.md"
    assert bundled.is_file(), "bundled pull-request-template.md must exist"


def test_diff_content_in_prompt(tmp_path):
    cfg = {"timeout": 300, "title_prefix": ""}
    ctx = _make_ctx(cfg, tmp_path)
    engine = _make_engine()

    diff_bytes = b"diff --git a/x b/x\n+hi\n"
    with patch.object(pr_open_mod, "subprocess") as mock_sub:
        mock_sub.run.side_effect = _subprocess_factory(diff_stdout=diff_bytes)
        mock_sub.TimeoutExpired = subprocess.TimeoutExpired
        OpenPrStep().run(ctx, engine, MagicMock(), MagicMock())

    prompt = engine.run_llm.call_args.kwargs["prompt"]
    assert "diff --git a/x b/x" in prompt
    assert "+hi" in prompt


def test_log_content_in_prompt(tmp_path):
    cfg = {"timeout": 300, "title_prefix": ""}
    ctx = _make_ctx(cfg, tmp_path)
    engine = _make_engine()

    log_bytes = b"subject line\n\nbody line\n"
    with patch.object(pr_open_mod, "subprocess") as mock_sub:
        mock_sub.run.side_effect = _subprocess_factory(log_stdout=log_bytes)
        mock_sub.TimeoutExpired = subprocess.TimeoutExpired
        OpenPrStep().run(ctx, engine, MagicMock(), MagicMock())

    prompt = engine.run_llm.call_args.kwargs["prompt"]
    assert "subject line" in prompt
    assert "body line" in prompt


def test_no_stale_placeholders_in_rendered_prompt(tmp_path):
    cfg = {"timeout": 300, "title_prefix": ""}
    ctx = _make_ctx(cfg, tmp_path)
    engine = _make_engine()

    with patch.object(pr_open_mod, "subprocess") as mock_sub:
        mock_sub.run.side_effect = _subprocess_factory()
        mock_sub.TimeoutExpired = subprocess.TimeoutExpired
        OpenPrStep().run(ctx, engine, MagicMock(), MagicMock())

    prompt = engine.run_llm.call_args.kwargs["prompt"]
    for placeholder in (
        "{{PR_BODY_TEMPLATE}}",
        "{{GIT_DIFF}}",
        "{{GIT_LOG}}",
        "{{BASE_BRANCH}}",
        "{{PR_BODY_TEMPLATE_PATH}}",
    ):
        assert placeholder not in prompt


def test_subprocess_called_twice_before_claude(tmp_path):
    cfg = {"timeout": 300, "title_prefix": ""}
    ctx = _make_ctx(cfg, tmp_path, wt_dir="/wt")
    engine = _make_engine()

    with patch.object(pr_open_mod, "subprocess") as mock_sub:
        mock_sub.run.side_effect = _subprocess_factory()
        mock_sub.TimeoutExpired = subprocess.TimeoutExpired
        OpenPrStep().run(ctx, engine, MagicMock(), MagicMock())

    assert mock_sub.run.call_count == 2
    calls = mock_sub.run.call_args_list
    assert calls[0].kwargs["cwd"] == "/wt"
    assert calls[1].kwargs["cwd"] == "/wt"
    assert "diff" in calls[0].args[0]
    assert "log" in calls[1].args[0]
    assert engine.run_llm.call_count == 1


def test_git_diff_log_files_written(tmp_path):
    cfg = {"timeout": 300, "title_prefix": ""}
    ctx = _make_ctx(cfg, tmp_path)
    engine = _make_engine()

    diff_bytes = b"diff content\n"
    log_bytes = b"log content\n"
    with patch.object(pr_open_mod, "subprocess") as mock_sub:
        mock_sub.run.side_effect = _subprocess_factory(
            diff_stdout=diff_bytes, log_stdout=log_bytes
        )
        mock_sub.TimeoutExpired = subprocess.TimeoutExpired
        OpenPrStep().run(ctx, engine, MagicMock(), MagicMock())

    diff_log = tmp_path / "open-pr-git-diff.log"
    git_log_file = tmp_path / "open-pr-git-log.log"
    assert diff_log.exists()
    assert git_log_file.exists()
    assert "diff content" in diff_log.read_text()
    assert "log content" in git_log_file.read_text()


def test_git_diff_failure_raises_step_error(tmp_path, capsys):
    cfg = {"timeout": 300, "title_prefix": ""}
    ctx = _make_ctx(cfg, tmp_path)
    engine = _make_engine()

    def fail_diff(cmd, **kwargs):
        if "diff" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=128,
                stdout=b"",
                stderr=b"fatal: ambiguous argument\n",
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=b"", stderr=b""
        )

    with patch.object(pr_open_mod, "subprocess") as mock_sub:
        mock_sub.run.side_effect = fail_diff
        mock_sub.TimeoutExpired = subprocess.TimeoutExpired
        with pytest.raises(StepError) as exc_info:
            OpenPrStep().run(ctx, engine, MagicMock(), MagicMock())

    assert exc_info.value.step_name == "open-pr"
    assert exc_info.value.exit_code == 128
    assert mock_sub.run.call_count == 1
    engine.run_llm.assert_not_called()
    engine.run_command.assert_not_called()
    captured = capsys.readouterr()
    assert "fatal: ambiguous argument" in captured.err


def test_git_log_failure_raises_step_error(tmp_path):
    cfg = {"timeout": 300, "title_prefix": ""}
    ctx = _make_ctx(cfg, tmp_path)
    engine = _make_engine()

    def fail_log(cmd, **kwargs):
        if "log" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout=b"", stderr=b""
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=b"", stderr=b""
        )

    with patch.object(pr_open_mod, "subprocess") as mock_sub:
        mock_sub.run.side_effect = fail_log
        mock_sub.TimeoutExpired = subprocess.TimeoutExpired
        with pytest.raises(StepError) as exc_info:
            OpenPrStep().run(ctx, engine, MagicMock(), MagicMock())

    assert exc_info.value.exit_code == 1
    engine.run_llm.assert_not_called()
    engine.run_command.assert_not_called()


def test_timeout_raises_step_error(tmp_path):
    cfg = {"timeout": 300, "title_prefix": ""}
    ctx = _make_ctx(cfg, tmp_path)
    engine = _make_engine()

    def raise_timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 300)

    with patch.object(pr_open_mod, "subprocess") as mock_sub:
        mock_sub.run.side_effect = raise_timeout
        mock_sub.TimeoutExpired = subprocess.TimeoutExpired
        with pytest.raises(StepError) as exc_info:
            OpenPrStep().run(ctx, engine, MagicMock(), MagicMock())

    assert exc_info.value.exit_code == TIMEOUT_EXIT
    diff_log = tmp_path / "open-pr-git-diff.log"
    assert diff_log.exists()
    assert "timed out after" in diff_log.read_text()


def test_non_utf8_template_does_not_raise(tmp_path):
    tpl = tmp_path / "tpl.md"
    tpl.write_bytes(b"## Summary\xff\n")

    cfg = {"timeout": 300, "title_prefix": "", "pr_body_template": str(tpl)}
    ctx = _make_ctx(cfg, tmp_path)
    engine = _make_engine()

    with patch.object(pr_open_mod, "subprocess") as mock_sub:
        mock_sub.run.side_effect = _subprocess_factory()
        mock_sub.TimeoutExpired = subprocess.TimeoutExpired
        OpenPrStep().run(ctx, engine, MagicMock(), MagicMock())

    prompt = engine.run_llm.call_args.kwargs["prompt"]
    assert "�" in prompt


def test_bundled_open_pr_md_contains_required_placeholders():
    content = (STEP_DIR / "open_pr.md").read_text()
    for placeholder in (
        "<<<PR-TITLE>>>",
        "<<</PR-TITLE>>>",
        "<<<PR-BODY>>>",
        "<<</PR-BODY>>>",
        "{{PR_BODY_TEMPLATE}}",
        "{{GIT_DIFF}}",
        "{{GIT_LOG}}",
    ):
        assert placeholder in content, f"missing {placeholder} in open_pr.md"


def test_bundled_open_pr_md_no_old_placeholders():
    content = (STEP_DIR / "open_pr.md").read_text()
    assert "{{BASE_BRANCH}}" not in content
    assert "{{PR_BODY_TEMPLATE_PATH}}" not in content
