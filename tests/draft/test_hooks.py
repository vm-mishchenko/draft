from contextlib import contextmanager

import pytest

from draft.hooks import DraftLifecycle, HookError, HookRunner


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


# --- log file shape ---

def test_log_file_has_header_body_footer(tmp_path):
    out_file = tmp_path / "out.txt"
    config = {
        "steps": {
            "code-spec": {
                "hooks": {
                    "pre": [
                        {"cmd": f"echo hello >> {out_file}"},
                    ]
                }
            }
        }
    }
    _runner(config, tmp_path, tmp_path).run("code-spec", "pre")

    log = (tmp_path / "code-spec.pre.log").read_text()
    assert "=== code-spec.pre[0] @ " in log
    assert f"$ echo hello >> {out_file}" in log
    assert "--- exit 0 in" in log


def test_two_commands_share_one_log_file_in_order(tmp_path):
    out_file = tmp_path / "out.txt"
    config = {
        "steps": {
            "code-spec": {
                "hooks": {
                    "pre": [
                        {"cmd": f"echo first >> {out_file}"},
                        {"cmd": f"echo second >> {out_file}"},
                    ]
                }
            }
        }
    }
    _runner(config, tmp_path, tmp_path).run("code-spec", "pre")

    log = (tmp_path / "code-spec.pre.log").read_text()
    assert log.index("code-spec.pre[0]") < log.index("code-spec.pre[1]")
    assert log.count("=== ") == 2


def test_log_file_truncated_on_re_invocation(tmp_path):
    config = {
        "steps": {
            "code-spec": {
                "hooks": {
                    "pre": [{"cmd": "echo invocation"}],
                }
            }
        }
    }
    runner = _runner(config, tmp_path, tmp_path)
    runner.run("code-spec", "pre")
    first = (tmp_path / "code-spec.pre.log").read_text()
    assert first.count("=== code-spec.pre[0]") == 1

    runner.run("code-spec", "pre")
    second = (tmp_path / "code-spec.pre.log").read_text()
    assert second.count("=== code-spec.pre[0]") == 1


# --- empty / missing ---

def test_no_hooks_no_log_file(tmp_path):
    _runner({}, tmp_path, tmp_path).run("nonexistent", "pre")
    assert not (tmp_path / "nonexistent.pre.log").exists()


def test_empty_event_no_log_file(tmp_path):
    config = {"steps": {"code-spec": {"hooks": {"pre": []}}}}
    _runner(config, tmp_path, tmp_path).run("code-spec", "pre")
    assert not (tmp_path / "code-spec.pre.log").exists()


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
    config = {
        "steps": {
            "step": {
                "hooks": {
                    "pre": [{"cmd": "sleep 5", "timeout": 1}]
                }
            }
        }
    }
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
            "code-spec": {
                "hooks": {
                    "verify": [{"cmd": "exit 2"}, {"cmd": "true"}]
                }
            }
        }
    }
    runner = _runner(config, tmp_path, tmp_path)
    lifecycle = DraftLifecycle(runner)

    results = lifecycle.run_hooks("code-spec", "verify")

    assert len(results) == 1
    assert results[0].rc == 2
