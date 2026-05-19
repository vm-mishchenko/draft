import os
from contextlib import contextmanager
from enum import Enum

import pytest

from draft.hooks import _SKIP, DraftLifecycle, HookError, HookRunner, _to_env_str
from pipeline.context import RunContext


class FakeEngine:
    """No-op engine for hook tests. Records every set_status call."""

    def __init__(self):
        self.statuses: list[str] = []

    @contextmanager
    def tty_ticker(self, label: str):
        recorded = []

        def set_status(text: str) -> None:
            recorded.append(text)

        try:
            yield set_status
        finally:
            self.statuses.extend(recorded)


def _runner(config, cwd, run_dir, engine=None) -> HookRunner:
    engine = engine or FakeEngine()
    return HookRunner(config, cwd=str(cwd), run_dir=run_dir, engine=engine)


def _runner_with_ctx(config, cwd, run_dir, ctx, engine=None) -> HookRunner:
    engine = engine or FakeEngine()
    return HookRunner(config, cwd=str(cwd), run_dir=run_dir, engine=engine, ctx=ctx)


def _branch_marker_config(step, event, marker):
    return {
        "steps": {
            step: {
                "hooks": {
                    event: [
                        {
                            "cmd": (
                                f'echo "${{DRAFT_BRANCH:-[unset]}}|'
                                f'${{DRAFT_BASE_BRANCH:-[unset]}}" > {marker}'
                            )
                        }
                    ]
                }
            }
        }
    }


# --- log file shape ---


def test_log_file_has_header_body_footer(tmp_path):
    out_file = tmp_path / "out.txt"
    config = {
        "steps": {
            "implement-spec": {
                "hooks": {
                    "pre": [
                        {"cmd": f"echo hello >> {out_file}"},
                    ]
                }
            }
        }
    }
    _runner(config, tmp_path, tmp_path).run("implement-spec", "pre")

    log = (tmp_path / "implement-spec.pre.log").read_text()
    assert "=== implement-spec.pre[0] @ " in log
    assert f"$ echo hello >> {out_file}" in log
    assert "--- exit 0 in" in log


def test_two_commands_share_one_log_file_in_order(tmp_path):
    out_file = tmp_path / "out.txt"
    config = {
        "steps": {
            "implement-spec": {
                "hooks": {
                    "pre": [
                        {"cmd": f"echo first >> {out_file}"},
                        {"cmd": f"echo second >> {out_file}"},
                    ]
                }
            }
        }
    }
    _runner(config, tmp_path, tmp_path).run("implement-spec", "pre")

    log = (tmp_path / "implement-spec.pre.log").read_text()
    assert log.index("implement-spec.pre[0]") < log.index("implement-spec.pre[1]")
    assert log.count("=== ") == 2


def test_log_file_truncated_on_re_invocation(tmp_path):
    config = {
        "steps": {
            "implement-spec": {
                "hooks": {
                    "pre": [{"cmd": "echo invocation"}],
                }
            }
        }
    }
    runner = _runner(config, tmp_path, tmp_path)
    runner.run("implement-spec", "pre")
    first = (tmp_path / "implement-spec.pre.log").read_text()
    assert first.count("=== implement-spec.pre[0]") == 1

    runner.run("implement-spec", "pre")
    second = (tmp_path / "implement-spec.pre.log").read_text()
    assert second.count("=== implement-spec.pre[0]") == 1


# --- empty / missing ---


def test_no_hooks_no_log_file(tmp_path):
    _runner({}, tmp_path, tmp_path).run("nonexistent", "pre")
    assert not (tmp_path / "nonexistent.pre.log").exists()


def test_empty_event_no_log_file(tmp_path):
    config = {"steps": {"implement-spec": {"hooks": {"pre": []}}}}
    _runner(config, tmp_path, tmp_path).run("implement-spec", "pre")
    assert not (tmp_path / "implement-spec.pre.log").exists()


# --- cwd missing ---


def test_missing_cwd_skips_with_status_no_log(tmp_path):
    config = {
        "steps": {
            "step": {
                "hooks": {
                    "pre": [
                        {"cmd": "echo a"},
                        {"cmd": "echo b"},
                    ]
                }
            }
        }
    }
    engine = FakeEngine()
    missing_cwd = tmp_path / "does-not-exist"
    runner = HookRunner(config, cwd=str(missing_cwd), run_dir=tmp_path, engine=engine)

    result = runner.run("step", "pre")

    assert result == []
    assert not (tmp_path / "step.pre.log").exists()
    assert engine.statuses == ["skipped (cwd missing)", "skipped (cwd missing)"]


# --- failure / fail-fast ---


def test_first_failure_stops_event_returns_results(tmp_path):
    marker = tmp_path / "ran-second"
    config = {
        "steps": {
            "step": {
                "hooks": {
                    "pre": [
                        {"cmd": "exit 1"},
                        {"cmd": f"touch {marker}"},
                    ]
                }
            }
        }
    }
    results = _runner(config, tmp_path, tmp_path).run("step", "pre")

    assert not marker.exists()
    assert len(results) == 1
    assert results[0].rc == 1
    log = (tmp_path / "step.pre.log").read_text()
    assert "=== step.pre[0]" in log
    assert "=== step.pre[1]" not in log
    assert "--- exit 1 in" in log


def test_timeout_returns_failed_result(tmp_path):
    config = {"steps": {"step": {"hooks": {"pre": [{"cmd": "sleep 5", "timeout": 1}]}}}}
    [result] = _runner(config, tmp_path, tmp_path).run("step", "pre")

    assert result.rc == 124
    log = (tmp_path / "step.pre.log").read_text()
    assert "--- timed out after 1s ---" in log


# --- duration ---


def test_hook_result_carries_duration(tmp_path):
    config = {"steps": {"s": {"hooks": {"pre": [{"cmd": "true"}]}}}}
    [result] = _runner(config, tmp_path, tmp_path).run("s", "pre")
    assert result.duration >= 0
    assert result.rc == 0


# --- env injection ---


def test_env_both_vars_present(tmp_path):
    marker = tmp_path / "marker.txt"
    ctx = RunContext("rid", tmp_path)
    ctx.set("branch", "feature-x")
    ctx.set("base_branch", "main")
    config = _branch_marker_config("step", "pre", marker)
    _runner_with_ctx(config, tmp_path, tmp_path, ctx).run("step", "pre")
    assert marker.read_text().strip() == "feature-x|main"


def test_env_only_branch(tmp_path):
    marker = tmp_path / "marker.txt"
    ctx = RunContext("rid", tmp_path)
    ctx.set("branch", "feature-x")
    config = _branch_marker_config("step", "pre", marker)
    _runner_with_ctx(config, tmp_path, tmp_path, ctx).run("step", "pre")
    assert marker.read_text().strip() == "feature-x|[unset]"


def test_env_none_values(tmp_path):
    marker = tmp_path / "marker.txt"
    ctx = RunContext("rid", tmp_path)
    ctx.set("branch", None)
    ctx.set("base_branch", None)
    config = _branch_marker_config("step", "pre", marker)
    _runner_with_ctx(config, tmp_path, tmp_path, ctx).run("step", "pre")
    assert marker.read_text().strip() == "[unset]|[unset]"


def test_env_no_ctx_backward_compat(tmp_path):
    marker = tmp_path / "marker.txt"
    config = _branch_marker_config("step", "pre", marker)
    [result] = _runner(config, tmp_path, tmp_path).run("step", "pre")
    assert result.rc == 0
    assert marker.read_text().strip() == "[unset]|[unset]"


def test_env_preserves_os_environ(tmp_path):
    marker = tmp_path / "marker.txt"
    ctx = RunContext("rid", tmp_path)
    ctx.set("branch", "b")
    ctx.set("base_branch", "m")
    config = {
        "steps": {"step": {"hooks": {"pre": [{"cmd": f'echo "$PATH" > {marker}'}]}}}
    }
    _runner_with_ctx(config, tmp_path, tmp_path, ctx).run("step", "pre")
    assert marker.read_text().strip() == os.environ["PATH"]


def test_env_live_ctx_reference(tmp_path):
    marker1 = tmp_path / "m1.txt"
    marker2 = tmp_path / "m2.txt"
    ctx = RunContext("rid", tmp_path)
    ctx.set("branch", "a")
    config1 = {
        "steps": {
            "step": {"hooks": {"pre": [{"cmd": f'echo "$DRAFT_BRANCH" > {marker1}'}]}}
        }
    }
    runner = _runner_with_ctx(config1, tmp_path, tmp_path, ctx)
    runner.run("step", "pre")
    assert marker1.read_text().strip() == "a"

    ctx.set("branch", "b")
    runner._steps_config = {
        "step": {"hooks": {"pre": [{"cmd": f'echo "$DRAFT_BRANCH" > {marker2}'}]}}
    }
    runner.run("step", "pre")
    assert marker2.read_text().strip() == "b"


def test_env_reaches_verify_event(tmp_path):
    marker = tmp_path / "marker.txt"
    ctx = RunContext("rid", tmp_path)
    ctx.set("branch", "feature-x")
    ctx.set("base_branch", "main")
    config = _branch_marker_config("implement-spec", "verify", marker)
    runner = _runner_with_ctx(config, tmp_path, tmp_path, ctx)
    lifecycle = DraftLifecycle(runner)
    lifecycle.run_hooks("implement-spec", "verify")
    assert marker.read_text().strip() == "feature-x|main"


def test_env_failure_aborts_chain(tmp_path):
    marker = tmp_path / "marker.txt"
    second = tmp_path / "second.txt"
    ctx = RunContext("rid", tmp_path)
    ctx.set("branch", "feature-x")
    config = {
        "steps": {
            "step": {
                "hooks": {
                    "pre": [
                        {"cmd": (f'echo "$DRAFT_BRANCH" > {marker}; exit 1')},
                        {"cmd": f"touch {second}"},
                    ]
                }
            }
        }
    }
    runner = _runner_with_ctx(config, tmp_path, tmp_path, ctx)
    lifecycle = DraftLifecycle(runner)
    with pytest.raises(HookError):
        lifecycle.before_step(type("S", (), {"name": "step"})(), object())
    assert marker.read_text().strip() == "feature-x"
    assert not second.exists()


def test_to_env_str_none():
    assert _to_env_str(None) is _SKIP


def test_to_env_str_bool():
    assert _to_env_str(True) == "true"
    assert _to_env_str(False) == "false"


def test_to_env_str_enum():
    class E(Enum):
        NEW = "new"

    assert _to_env_str(E.NEW) == "new"


def test_to_env_str_other():
    assert _to_env_str("plain") == "plain"
    assert _to_env_str(42) == "42"


# --- DraftLifecycle wraps + raises ---


def test_lifecycle_before_step_raises_on_failure(tmp_path):
    config = {"steps": {"s": {"hooks": {"pre": [{"cmd": "exit 5"}]}}}}
    runner = _runner(config, tmp_path, tmp_path)
    lifecycle = DraftLifecycle(runner)

    class FakeStep:
        name = "s"

    with pytest.raises(HookError):
        lifecycle.before_step(FakeStep(), object())


def test_lifecycle_after_step_raises_on_failure(tmp_path):
    config = {"steps": {"s": {"hooks": {"post": [{"cmd": "exit 5"}]}}}}
    runner = _runner(config, tmp_path, tmp_path)
    lifecycle = DraftLifecycle(runner)

    class FakeStep:
        name = "s"

    with pytest.raises(HookError):
        lifecycle.after_step(FakeStep(), object())


def test_lifecycle_run_hooks_returns_failed_results_without_raising(tmp_path):
    config = {
        "steps": {
            "implement-spec": {
                "hooks": {"verify": [{"cmd": "exit 2"}, {"cmd": "true"}]}
            }
        }
    }
    runner = _runner(config, tmp_path, tmp_path)
    lifecycle = DraftLifecycle(runner)

    results = lifecycle.run_hooks("implement-spec", "verify")

    assert len(results) == 1
    assert results[0].rc == 2


# --- HookRunner.get_hooks ---


def test_get_hooks_returns_configured_entries(tmp_path):
    config = {
        "steps": {
            "implement-spec": {
                "hooks": {"verify": [{"cmd": "make test"}, {"cmd": "make lint"}]}
            }
        }
    }
    runner = _runner(config, tmp_path, tmp_path)
    result = runner.get_hooks("implement-spec", "verify")
    assert result == [{"cmd": "make test"}, {"cmd": "make lint"}]


def test_get_hooks_returns_empty_for_unknown_step(tmp_path):
    config = {
        "steps": {"implement-spec": {"hooks": {"verify": [{"cmd": "make test"}]}}}
    }
    runner = _runner(config, tmp_path, tmp_path)
    assert runner.get_hooks("unknown-step", "verify") == []


def test_get_hooks_returns_empty_for_known_step_no_hooks_key(tmp_path):
    config = {"steps": {"implement-spec": {"timeout": 60}}}
    runner = _runner(config, tmp_path, tmp_path)
    assert runner.get_hooks("implement-spec", "verify") == []


def test_get_hooks_returns_empty_for_known_step_missing_event(tmp_path):
    config = {"steps": {"implement-spec": {"hooks": {"pre": [{"cmd": "echo hi"}]}}}}
    runner = _runner(config, tmp_path, tmp_path)
    assert runner.get_hooks("implement-spec", "verify") == []


def test_get_hooks_does_not_invoke_subprocess(tmp_path):
    import subprocess as sp

    config = {
        "steps": {"implement-spec": {"hooks": {"verify": [{"cmd": "make test"}]}}}
    }
    runner = _runner(config, tmp_path, tmp_path)
    original_run = sp.run
    called = []

    def spy(*args, **kwargs):
        called.append(args)
        return original_run(*args, **kwargs)

    import unittest.mock as mock

    with mock.patch("subprocess.run", side_effect=spy):
        runner.get_hooks("implement-spec", "verify")

    assert called == []


# --- DraftLifecycle.get_hooks ---


def test_lifecycle_get_hooks_delegates_to_runner(tmp_path):
    config = {
        "steps": {"implement-spec": {"hooks": {"verify": [{"cmd": "make test"}]}}}
    }
    runner = _runner(config, tmp_path, tmp_path)
    lifecycle = DraftLifecycle(runner)
    result = lifecycle.get_hooks("implement-spec", "verify")
    assert result == [{"cmd": "make test"}]
