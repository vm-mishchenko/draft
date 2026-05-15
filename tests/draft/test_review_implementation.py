from pathlib import Path
from unittest import mock

import pytest

from pipeline import StepError

# --- Fixtures and helpers ---


class FakeStage:
    def __init__(self):
        self.messages = []
        self.stderr_messages = []

    def update(self, msg):
        self.messages.append(msg)

    def stderr(self, msg):
        self.stderr_messages.append(msg)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeEngine:
    def __init__(self, llm_responses=None):
        self._responses = iter(llm_responses or [])
        self.run_llm_calls = []

    def run_llm(self, **kwargs):
        self.run_llm_calls.append(kwargs)
        try:
            resp = next(self._responses)
        except StopIteration:
            resp = {"final_text": "", "rc": 0}
        result = mock.MagicMock()
        result.final_text = resp.get("final_text", "")
        result.rc = resp.get("rc", 0)
        return result

    def stage(self, name):
        return FakeStage()


class FakeLifecycle:
    def __init__(self, hooks=None, hook_results=None):
        self._hooks = hooks or []
        self._hook_results = hook_results or []

    def get_hooks(self, step, event):
        return self._hooks

    def run_hooks(self, step, event):
        return self._hook_results


class FakeContext:
    def __init__(self, run_dir, step_configs=None):
        self._run_dir = run_dir
        self._data = {}
        self._step_data = {}
        self._configs = step_configs or {}
        self._completed = set()
        self.save_count = 0

    @property
    def run_dir(self):
        return self._run_dir

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def config(self, step_name):
        return self._configs.get(step_name, {})

    def step_get(self, step, key, default=None):
        return self._step_data.get(step, {}).get(key, default)

    def step_set(self, step, key, value):
        if step not in self._step_data:
            self._step_data[step] = {}
        self._step_data[step][key] = value

    def save(self):
        self.save_count += 1


def make_ctx(tmp_path, reviewers_cfg=None, suggest_extra_checks=True, step_data=None):
    ctx = FakeContext(
        run_dir=tmp_path,
        step_configs={
            "review-implementation": {
                "reviewers": reviewers_cfg or [],
                "suggest_extra_checks": suggest_extra_checks,
            }
        },
    )
    ctx.set("wt_dir", str(tmp_path))
    ctx.set("spec", "")
    ctx.set("branch", "feature-x")
    ctx.set("base_branch", "main")
    ctx.set("repo", str(tmp_path))

    if step_data:
        for k, v in step_data.items():
            ctx.step_set("review-implementation", k, v)

    return ctx


# --- Tests: empty reviewers ---


def test_empty_reviewers_returns_early(tmp_path):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    ctx = make_ctx(tmp_path, reviewers_cfg=[])
    engine = FakeEngine()

    step.run(ctx, engine, FakeLifecycle(), None)

    assert engine.run_llm_calls == []
    assert ctx.save_count == 0


# --- Tests: Phase A approve ---


def test_single_reviewer_approve(tmp_path):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    ctx = make_ctx(
        tmp_path,
        reviewers_cfg=[
            {"name": "code-quality", "cmd": 'python -c "import sys; sys.exit(0)"'}
        ],
        suggest_extra_checks=False,
    )
    engine = FakeEngine()

    with mock.patch("draft.steps.review_implementation._invoke_script") as mock_invoke:
        from draft.steps.review_implementation import _Verdict

        mock_invoke.return_value = _Verdict("approve", "", "")

        with mock.patch.object(engine, "stage") as mock_stage:
            stage = FakeStage()
            mock_stage.return_value.__enter__ = lambda s: stage
            mock_stage.return_value.__exit__ = mock.Mock(return_value=False)

            step.run(ctx, engine, FakeLifecycle(), None)

    record = ctx.step_get("review-implementation", "reviewers", {}).get(
        "code-quality", {}
    )
    assert record["status"] == "approved"
    assert record["commit_sha"] is None
    assert ctx.step_get("review-implementation", "order") == ["code-quality"]
    assert ctx.step_get("review-implementation", "current") is None
    assert engine.run_llm_calls == []


# --- Tests: Phase A reject → Phase B success ---


def test_single_reviewer_reject_then_address(tmp_path):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    reviewers_cfg = [{"name": "docs", "cmd": "echo review", "max_retries": 5}]
    ctx = make_ctx(tmp_path, reviewers_cfg=reviewers_cfg, suggest_extra_checks=False)

    engine = FakeEngine(
        llm_responses=[
            {"final_text": "I made changes", "rc": 0},
            {"final_text": "fix commit message", "rc": 0},
        ]
    )

    with (
        mock.patch("draft.steps.review_implementation._invoke_script") as mock_invoke,
        mock.patch("draft.steps.review_implementation._has_changes", return_value=True),
        mock.patch(
            "draft.steps.review_implementation._run_git_capture", return_value=""
        ),
        mock.patch(
            "draft.steps.review_implementation._run_git_capture_allow_fail"
        ) as mock_commit,
    ):
        from draft.steps.review_implementation import _Verdict

        mock_invoke.return_value = _Verdict("reject", "please rename foo", "")
        commit_result = mock.MagicMock()
        commit_result.returncode = 0
        commit_result.stdout = b""
        commit_result.stderr = b""
        mock_commit.return_value = commit_result

        with mock.patch.object(engine, "stage") as mock_stage:
            stage = FakeStage()
            mock_stage.return_value.__enter__ = lambda s: stage
            mock_stage.return_value.__exit__ = mock.Mock(return_value=False)

            step.run(ctx, engine, FakeLifecycle(), None)

    record = ctx.step_get("review-implementation", "reviewers", {}).get("docs", {})
    assert record["status"] == "addressed"
    assert record["review_issues"] == "please rename foo"


# --- Tests: three reviewers all approve ---


def test_three_reviewers_all_approve(tmp_path):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    reviewers_cfg = [
        {"name": "a", "cmd": "echo a"},
        {"name": "b", "cmd": "echo b"},
        {"name": "c", "cmd": "echo c"},
    ]
    ctx = make_ctx(tmp_path, reviewers_cfg=reviewers_cfg, suggest_extra_checks=False)
    engine = FakeEngine()

    with (
        mock.patch("draft.steps.review_implementation._invoke_script") as mock_invoke,
        mock.patch.object(engine, "stage") as mock_stage,
    ):
        from draft.steps.review_implementation import _Verdict

        mock_invoke.return_value = _Verdict("approve", "", "")
        stage = FakeStage()
        mock_stage.return_value.__enter__ = lambda s: stage
        mock_stage.return_value.__exit__ = mock.Mock(return_value=False)

        step.run(ctx, engine, FakeLifecycle(), None)

    assert ctx.step_get("review-implementation", "order") == ["a", "b", "c"]
    records = ctx.step_get("review-implementation", "reviewers", {})
    assert records["a"]["status"] == "approved"
    assert records["b"]["status"] == "approved"
    assert records["c"]["status"] == "approved"
    assert engine.run_llm_calls == []


# --- Tests: three reviewers, b rejects ---


def test_three_reviewers_b_rejects(tmp_path):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    reviewers_cfg = [
        {"name": "a", "cmd": "echo a"},
        {"name": "b", "cmd": "echo b"},
        {"name": "c", "cmd": "echo c"},
    ]
    ctx = make_ctx(tmp_path, reviewers_cfg=reviewers_cfg, suggest_extra_checks=False)

    engine = FakeEngine(
        llm_responses=[
            {"final_text": "fixed", "rc": 0},
            {"final_text": "commit msg", "rc": 0},
        ]
    )

    from draft.steps.review_implementation import _Verdict

    approve = _Verdict("approve", "", "")
    reject = _Verdict("reject", "style issues", "")

    invoke_side_effects = [approve, reject, approve]

    with (
        mock.patch(
            "draft.steps.review_implementation._invoke_script",
            side_effect=invoke_side_effects,
        ),
        mock.patch("draft.steps.review_implementation._has_changes", return_value=True),
        mock.patch(
            "draft.steps.review_implementation._run_git_capture", return_value=""
        ),
        mock.patch(
            "draft.steps.review_implementation._run_git_capture_allow_fail"
        ) as mock_commit,
        mock.patch.object(engine, "stage") as mock_stage,
    ):
        commit_result = mock.MagicMock()
        commit_result.returncode = 0
        commit_result.stdout = b""
        commit_result.stderr = b""
        mock_commit.return_value = commit_result

        stage = FakeStage()
        mock_stage.return_value.__enter__ = lambda s: stage
        mock_stage.return_value.__exit__ = mock.Mock(return_value=False)

        step.run(ctx, engine, FakeLifecycle(), None)

    records = ctx.step_get("review-implementation", "reviewers", {})
    assert records["a"]["status"] == "approved"
    assert records["b"]["status"] == "addressed"
    assert records["c"]["status"] == "approved"

    # Check stage messages have proper prefixes
    msgs = stage.messages
    assert any("[a]" in m for m in msgs)
    assert any("[b]" in m for m in msgs)
    assert any("[c]" in m for m in msgs)


# --- Tests: Phase A infra_failure raises StepError ---


def test_phase_a_infra_failure_raises_step_error(tmp_path, capsys):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    ctx = make_ctx(
        tmp_path,
        reviewers_cfg=[{"name": "r1", "cmd": "echo r1"}],
        suggest_extra_checks=False,
    )
    engine = FakeEngine()

    from draft.steps.review_implementation import _Verdict

    with (
        mock.patch(
            "draft.steps.review_implementation._invoke_script",
            return_value=_Verdict("infra_failure", "", "exit 1"),
        ),
        mock.patch.object(engine, "stage") as mock_stage,
        pytest.raises(StepError),
    ):
        stage = FakeStage()
        mock_stage.return_value.__enter__ = lambda s: stage
        mock_stage.return_value.__exit__ = mock.Mock(return_value=False)

        step.run(ctx, engine, FakeLifecycle(), None)

    record = ctx.step_get("review-implementation", "reviewers", {}).get("r1", {})
    assert record.get("status") == "failed"

    captured = capsys.readouterr()
    assert "r1" in captured.err


# --- Tests: Phase B exhausts max_retries ---


def test_phase_b_exhausts_max_retries(tmp_path):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    ctx = make_ctx(
        tmp_path,
        reviewers_cfg=[{"name": "r1", "cmd": "echo r1", "max_retries": 2}],
        suggest_extra_checks=False,
    )

    engine = FakeEngine(
        llm_responses=[
            {"final_text": "change 1", "rc": 0},
            {"final_text": "change 2", "rc": 0},
        ]
    )

    from draft.steps.review_implementation import _Verdict

    # Phase A rejects; every attempt produces no changes → _NO_CHANGES_MSG sets verify_errors
    # but attempt == 1 returns early with approved. Let's make it produce changes but commit fails.
    with (
        mock.patch(
            "draft.steps.review_implementation._invoke_script",
            return_value=_Verdict("reject", "fix this", ""),
        ),
        mock.patch("draft.steps.review_implementation._has_changes", return_value=True),
        mock.patch(
            "draft.steps.review_implementation._run_git_capture", return_value=""
        ),
        mock.patch(
            "draft.steps.review_implementation._run_git_capture_allow_fail"
        ) as mock_commit,
        mock.patch.object(engine, "stage") as mock_stage,
        pytest.raises(StepError),
    ):
        # commit always fails with pre-commit hook error
        commit_result = mock.MagicMock()
        commit_result.returncode = 1
        commit_result.stdout = b"hook failed"
        commit_result.stderr = b""
        mock_commit.return_value = commit_result

        stage = FakeStage()
        mock_stage.return_value.__enter__ = lambda s: stage
        mock_stage.return_value.__exit__ = mock.Mock(return_value=False)

        step.run(ctx, engine, FakeLifecycle(), None)

    record = ctx.step_get("review-implementation", "reviewers", {}).get("r1", {})
    assert record.get("status") == "failed"


# --- Tests: LLM produces no changes on attempt 1 → approved ---


def test_llm_no_changes_attempt1_approved(tmp_path):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    ctx = make_ctx(
        tmp_path,
        reviewers_cfg=[{"name": "r1", "cmd": "echo r1"}],
        suggest_extra_checks=False,
    )
    engine = FakeEngine(llm_responses=[{"final_text": "nothing to do", "rc": 0}])

    from draft.steps.review_implementation import _Verdict

    with (
        mock.patch(
            "draft.steps.review_implementation._invoke_script",
            return_value=_Verdict("reject", "fix this", ""),
        ),
        mock.patch(
            "draft.steps.review_implementation._has_changes", return_value=False
        ),
        mock.patch.object(engine, "stage") as mock_stage,
    ):
        stage = FakeStage()
        mock_stage.return_value.__enter__ = lambda s: stage
        mock_stage.return_value.__exit__ = mock.Mock(return_value=False)

        step.run(ctx, engine, FakeLifecycle(), None)

    record = ctx.step_get("review-implementation", "reviewers", {}).get("r1", {})
    assert record.get("status") == "approved"
    assert record.get("commit_sha") is None


# --- Tests: DRAFT_REVIEWER_NAME in env ---


def test_draft_reviewer_name_in_env(tmp_path):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    ctx = make_ctx(
        tmp_path,
        reviewers_cfg=[{"name": "my-reviewer", "cmd": "echo hi"}],
        suggest_extra_checks=False,
    )
    engine = FakeEngine()

    captured_envs = []

    from draft.steps.review_implementation import _Verdict

    def fake_invoke(argv, cwd, env, timeout, log_path):
        captured_envs.append(dict(env))
        return _Verdict("approve", "", "")

    with (
        mock.patch(
            "draft.steps.review_implementation._invoke_script", side_effect=fake_invoke
        ),
        mock.patch.object(engine, "stage") as mock_stage,
    ):
        stage = FakeStage()
        mock_stage.return_value.__enter__ = lambda s: stage
        mock_stage.return_value.__exit__ = mock.Mock(return_value=False)

        step.run(ctx, engine, FakeLifecycle(), None)

    assert len(captured_envs) == 1
    assert captured_envs[0]["DRAFT_REVIEWER_NAME"] == "my-reviewer"


# --- Tests: per-reviewer log file paths ---


def test_per_reviewer_log_files_named_correctly(tmp_path):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    ctx = make_ctx(
        tmp_path,
        reviewers_cfg=[{"name": "code-quality", "cmd": "echo hi"}],
        suggest_extra_checks=False,
    )
    engine = FakeEngine()

    used_log_paths = []

    from draft.steps.review_implementation import _Verdict

    def fake_invoke(argv, cwd, env, timeout, log_path):
        used_log_paths.append(log_path)
        return _Verdict("approve", "", "")

    with (
        mock.patch(
            "draft.steps.review_implementation._invoke_script", side_effect=fake_invoke
        ),
        mock.patch.object(engine, "stage") as mock_stage,
    ):
        stage = FakeStage()
        mock_stage.return_value.__enter__ = lambda s: stage
        mock_stage.return_value.__exit__ = mock.Mock(return_value=False)

        step.run(ctx, engine, FakeLifecycle(), None)

    assert len(used_log_paths) == 1
    assert "code-quality" in used_log_paths[0].name
    assert used_log_paths[0].name == "review-implementation.code-quality.review.log"


# --- Tests: _generate_commit_message reviewer_name substitution ---


def test_generate_commit_message_reviewer_name_substituted(tmp_path):
    from draft.steps.review_implementation import _generate_commit_message

    log_path = tmp_path / "commit.log"

    engine = mock.MagicMock()
    result = mock.MagicMock()
    result.final_text = "fix: address code review"
    result.rc = 0
    engine.run_llm.return_value = result

    template_with_placeholder = (
        "Review by {{REVIEWER_NAME}}.\n{{REVIEW_ISSUES}}\n{{DIFF}}\n{{SPEC}}"
    )

    with (
        mock.patch(
            "draft.steps.review_implementation._run_git_capture", return_value=""
        ),
        mock.patch("draft.steps.review_implementation.files") as mock_files,
    ):
        mock_files.return_value.joinpath.return_value.read_text.return_value = (
            template_with_placeholder
        )

        msg, used_fallback = _generate_commit_message(
            review_issues="fix naming",
            spec="spec text",
            wt_dir=str(tmp_path),
            log_path=log_path,
            timeout=30,
            max_attempts=1,
            engine=engine,
            step_metrics=None,
            reviewer_name="code-quality",
        )

    assert msg == "fix: address code review"
    # Verify the prompt contained the reviewer name substituted
    call_kwargs = engine.run_llm.call_args[1]
    assert "code-quality" in call_kwargs["prompt"]
    assert "{{REVIEWER_NAME}}" not in call_kwargs["prompt"]


# --- Tests: resume skips completed reviewers ---


def test_resume_skips_approved_and_addressed_reviewers(tmp_path):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    reviewers_cfg = [
        {"name": "a", "cmd": "echo a"},
        {"name": "b", "cmd": "echo b"},
        {"name": "c", "cmd": "echo c"},
    ]
    # Pre-seed a and b as done
    step_data = {
        "reviewers": {
            "a": {"status": "approved"},
            "b": {"status": "addressed", "commit_sha": "abc"},
        }
    }
    ctx = make_ctx(
        tmp_path,
        reviewers_cfg=reviewers_cfg,
        step_data=step_data,
        suggest_extra_checks=False,
    )
    engine = FakeEngine()

    invoke_calls = []

    from draft.steps.review_implementation import _Verdict

    def fake_invoke(argv, cwd, env, timeout, log_path):
        invoke_calls.append(env.get("DRAFT_REVIEWER_NAME"))
        return _Verdict("approve", "", "")

    with (
        mock.patch(
            "draft.steps.review_implementation._invoke_script", side_effect=fake_invoke
        ),
        mock.patch.object(engine, "stage") as mock_stage,
    ):
        stage = FakeStage()
        mock_stage.return_value.__enter__ = lambda s: stage
        mock_stage.return_value.__exit__ = mock.Mock(return_value=False)

        step.run(ctx, engine, FakeLifecycle(), None)

    # Only c should have been invoked
    assert invoke_calls == ["c"]


def test_resume_restarts_reviewer_without_terminal_status(tmp_path):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    reviewers_cfg = [
        {"name": "a", "cmd": "echo a"},
        {"name": "b", "cmd": "echo b"},
    ]
    # b has no terminal status — should be restarted
    step_data = {
        "reviewers": {
            "a": {"status": "approved"},
            "b": {"review_done": False},
        }
    }
    ctx = make_ctx(
        tmp_path,
        reviewers_cfg=reviewers_cfg,
        step_data=step_data,
        suggest_extra_checks=False,
    )
    engine = FakeEngine()

    invoke_calls = []

    from draft.steps.review_implementation import _Verdict

    def fake_invoke(argv, cwd, env, timeout, log_path):
        invoke_calls.append(env.get("DRAFT_REVIEWER_NAME"))
        return _Verdict("approve", "", "")

    with (
        mock.patch(
            "draft.steps.review_implementation._invoke_script", side_effect=fake_invoke
        ),
        mock.patch.object(engine, "stage") as mock_stage,
    ):
        stage = FakeStage()
        mock_stage.return_value.__enter__ = lambda s: stage
        mock_stage.return_value.__exit__ = mock.Mock(return_value=False)

        step.run(ctx, engine, FakeLifecycle(), None)

    assert invoke_calls == ["b"]


# --- Tests: legacy state raises StepError ---


def test_legacy_state_raises_step_error(tmp_path, capsys):
    from draft.steps.review_implementation import ReviewImplementationStep

    step = ReviewImplementationStep()
    ctx = make_ctx(
        tmp_path,
        reviewers_cfg=[{"name": "r1", "cmd": "echo hi"}],
    )
    # Seed legacy flat keys
    ctx.step_set("review-implementation", "review_issues", "old issues")
    ctx.step_set("review-implementation", "commit_sha", "abc")
    # Do NOT set reviewers key

    with pytest.raises(StepError):
        step.run(ctx, FakeEngine(), FakeLifecycle(), None)

    captured = capsys.readouterr()
    assert "legacy state.json shape detected" in captured.err


# --- Tests: README contains reviewers: ---


def test_readme_contains_reviewers_section():
    readme = (Path(__file__).parent.parent.parent / "README.md").read_text()
    # Find the review-implementation section
    assert "reviewers:" in readme
    # Should NOT contain old top-level cmd: example for review-implementation
    import re

    section_match = re.search(
        r"### review-implementation.*?(?=###|\Z)", readme, re.DOTALL
    )
    assert section_match is not None
    section = section_match.group(0)
    assert "reviewers:" in section
    # Old style: top-level cmd: "" under review-implementation should be gone
    assert 'cmd: ""' not in section


# --- Tests: _shell_repro_line ---


class TestShellReproLine:
    def setup_method(self):
        from draft.steps.review_implementation import _shell_repro_line

        self.fn = _shell_repro_line

    def test_no_env(self):
        result = self.fn("/wt", {}, ["/abs/review.sh", "gpt5.4"])
        assert result == "$ cd /wt && /abs/review.sh gpt5.4"

    def test_env_alphabetical_order(self):
        result = self.fn(
            "/wt",
            {"DRAFT_BRANCH": "feat/x", "DRAFT_REPO_DIR": "/wt"},
            ["/abs/review.sh", "gpt5.4"],
        )
        assert (
            result
            == "$ cd /wt && DRAFT_BRANCH=feat/x DRAFT_REPO_DIR=/wt /abs/review.sh gpt5.4"
        )

    def test_empty_env_value_quoted(self):
        result = self.fn("/wt", {"DRAFT_BASE_BRANCH": ""}, ["/abs/review.sh"])
        assert "DRAFT_BASE_BRANCH=''" in result

    def test_env_value_with_space_quoted(self):
        result = self.fn("/wt", {"DRAFT_SPEC_FILE": "/path with space/spec.md"}, ["/x"])
        assert "DRAFT_SPEC_FILE='/path with space/spec.md'" in result

    def test_env_value_special_chars_quoted(self):
        import shlex

        result = self.fn("/wt", {"DRAFT_BRANCH": "weird'name\"x$y"}, ["/x"])
        # The quoted value must round-trip through shlex parsing
        assert result.startswith("$ cd ")
        # Extract env assignment and verify shlex can parse it
        assert "DRAFT_BRANCH=" in result
        tokens = shlex.split(result.split("&& ", 1)[1])
        branch_token = next(t for t in tokens if t.startswith("DRAFT_BRANCH="))
        assert branch_token == "DRAFT_BRANCH=weird'name\"x$y"

    def test_cwd_with_space_quoted(self):
        result = self.fn("/path with space/wt", {}, ["/x"])
        assert result == "$ cd '/path with space/wt' && /x"

    def test_argv_with_space_quoted(self):
        result = self.fn("/wt", {}, ["/abs/review.sh", "model with space"])
        assert "'/abs/review.sh'" in result or "/abs/review.sh" in result
        assert "'model with space'" in result

    def test_deterministic(self):
        args = ("/wt", {"DRAFT_B": "b", "DRAFT_A": "a"}, ["/x", "y"])
        assert self.fn(*args) == self.fn(*args)


# --- Integration test: _invoke_script writes shell repro line ---


def test_invoke_script_writes_shell_repro_line(tmp_path):
    import re
    import shutil

    from draft.steps.review_implementation import _invoke_script

    true_bin = shutil.which("true") or "/usr/bin/true"
    log_path = tmp_path / "r.log"
    verdict = _invoke_script(
        argv=[true_bin],
        cwd=str(tmp_path),
        env={"DRAFT_X": "y"},
        timeout=10,
        log_path=log_path,
    )

    content = log_path.read_text()
    lines = content.splitlines()

    assert any("=== review @" in line for line in lines)
    assert any(f"argv: ['{true_bin}']" in line for line in lines)
    assert any("CWD:" in line for line in lines)
    assert any("DRAFT env:" in line for line in lines)

    draft_env_idx = next(i for i, line in enumerate(lines) if "DRAFT env:" in line)
    remaining = lines[draft_env_idx + 1 :]
    assert any(
        re.match(rf"^\$ cd .+ && DRAFT_X=y {re.escape(true_bin)}$", line)
        for line in remaining
    )

    assert verdict.kind == "approve"
