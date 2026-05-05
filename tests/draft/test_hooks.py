import pytest

from draft.hooks import HookError, HookRunner


def test_hook_runner_runs_commands_in_order(tmp_path):
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
    runner = HookRunner(config, cwd=str(tmp_path))
    runner.run("code-spec", "pre")

    lines = out_file.read_text().splitlines()
    assert lines[0].strip() == "first"
    assert lines[1].strip() == "second"


def test_hook_runner_first_failure_raises(tmp_path):
    config = {
        "steps": {
            "code-spec": {
                "hooks": {
                    "pre": [
                        {"cmd": "exit 1", "retry": 1},
                    ]
                }
            }
        }
    }
    runner = HookRunner(config, cwd=str(tmp_path))
    with pytest.raises(HookError):
        runner.run("code-spec", "pre")


def test_hook_runner_retry_on_failure(tmp_path):
    count_file = tmp_path / "count.txt"
    count_file.write_text("0")
    # Script increments count then fails; with retry=2 it runs twice
    script = (
        f"COUNT=$(cat {count_file}); "
        f"echo $((COUNT+1)) > {count_file}; "
        f"exit 1"
    )
    config = {
        "steps": {
            "step": {
                "hooks": {
                    "pre": [{"cmd": script, "retry": 2}]
                }
            }
        }
    }
    runner = HookRunner(config, cwd=str(tmp_path))
    with pytest.raises(HookError):
        runner.run("step", "pre")

    assert int(count_file.read_text().strip()) == 2


def test_hook_runner_no_hooks_is_noop(tmp_path):
    runner = HookRunner({}, cwd=str(tmp_path))
    runner.run("nonexistent-step", "pre")  # should not raise


def test_hook_runner_missing_event_is_noop(tmp_path):
    config = {"steps": {"code-spec": {"hooks": {"pre": []}}}}
    runner = HookRunner(config, cwd=str(tmp_path))
    runner.run("code-spec", "on_error")  # no on_error defined — should not raise
