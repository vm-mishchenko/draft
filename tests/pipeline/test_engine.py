import sys
from unittest.mock import patch

from pipeline.runner import Runner, StageHandle


def test_stage_exits_normally_no_update_prints_ok(capsys):
    runner = Runner()
    with patch.object(sys.stdout, "isatty", return_value=False):
        with runner.stage("my-label"):
            pass

    captured = capsys.readouterr()
    assert "my-label" in captured.out
    assert captured.out.rstrip().endswith("ok")
    assert "\r" not in captured.out


def test_stage_exits_normally_after_update_prints_last_status(capsys):
    runner = Runner()
    with patch.object(sys.stdout, "isatty", return_value=False):
        with runner.stage("my-label") as s:
            s.update("3/10")

    captured = capsys.readouterr()
    assert "my-label" in captured.out
    assert captured.out.rstrip().endswith("3/10")


def test_stage_on_exception_prints_failed_and_propagates(capsys):
    runner = Runner()
    raised = False
    try:
        with patch.object(sys.stdout, "isatty", return_value=False):
            with runner.stage("boom"):
                raise RuntimeError("boom")
    except RuntimeError:
        raised = True

    assert raised
    captured = capsys.readouterr()
    assert "boom" in captured.out
    assert captured.out.rstrip().endswith("failed")


def test_run_command_zero_exit_writes_log_no_stdout(tmp_path, capsys):
    runner = Runner()
    log_path = tmp_path / "test.log"
    rc = runner.run_command(
        cmd=["echo", "hello"],
        cwd=None,
        log_path=log_path,
        attempt=1,
    )

    assert rc == 0
    assert log_path.exists()
    assert "hello" in log_path.read_text()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_run_command_nonzero_exit_no_stdout(tmp_path, capsys):
    runner = Runner()
    log_path = tmp_path / "test.log"
    rc = runner.run_command(
        cmd=["false"],
        cwd=None,
        log_path=log_path,
        attempt=1,
    )

    assert rc != 0
    captured = capsys.readouterr()
    assert captured.out == ""


def test_sleep_outside_stage_prints_output(capsys):
    runner = Runner()
    assert runner._active_stage is None
    with patch.object(sys.stdout, "isatty", return_value=False):
        runner.sleep(0, "waiting")
    # sleep(0) returns immediately — no active stage modified
    assert runner._active_stage is None


def test_sleep_inside_stage_updates_status_silently(capsys):
    runner = Runner()
    with patch.object(sys.stdout, "isatty", return_value=False):
        with runner.stage("my-step") as s:
            runner.sleep(0.01, "sleeping")

    captured = capsys.readouterr()
    # No countdown characters; only the final stage line is printed
    assert "s..." not in captured.out
    assert captured.out.rstrip().endswith("sleeping")
