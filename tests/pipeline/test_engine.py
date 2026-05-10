import sys
from unittest.mock import patch

from pipeline.runner import Runner


def test_stage_no_update_final_line_ends_with_ok(capsys):
    runner = Runner()
    with patch.object(sys.stdout, "isatty", return_value=False):
        with runner.stage("my-label"):
            pass
    captured = capsys.readouterr()
    assert "my-label" in captured.out
    assert captured.out.rstrip().endswith("ok")
    assert "\r" not in captured.out


def test_stage_update_final_line_ends_with_last_status(capsys):
    runner = Runner()
    with patch.object(sys.stdout, "isatty", return_value=False):
        with runner.stage("my-label") as s:
            s.update("3/10")
    captured = capsys.readouterr()
    assert captured.out.rstrip().endswith("3/10")


def test_stage_exception_final_line_ends_with_failed(capsys):
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


def test_run_command_zero_exit_returns_zero_and_writes_log(tmp_path):
    runner = Runner()
    log = tmp_path / "test.log"
    rc = runner.run_command(
        cmd=["echo", "hello"],
        cwd=None,
        log_path=log,
        attempt=1,
    )
    assert rc == 0
    assert "hello" in log.read_text()


def test_run_command_no_stdout(tmp_path, capsys):
    runner = Runner()
    log = tmp_path / "test.log"
    runner.run_command(
        cmd=["echo", "hello"],
        cwd=None,
        log_path=log,
    )
    captured = capsys.readouterr()
    assert "hello" not in captured.out


def test_run_command_nonzero_exit_returns_nonzero(tmp_path):
    runner = Runner()
    log = tmp_path / "test.log"
    rc = runner.run_command(
        cmd=["false"],
        cwd=None,
        log_path=log,
    )
    assert rc != 0


def test_sleep_outside_stage_writes_to_stdout(capsys):
    runner = Runner()
    with patch.object(sys.stdout, "isatty", return_value=False):
        runner.sleep(0.01, "waiting")
    captured = capsys.readouterr()
    assert len(captured.out) > 0


def test_sleep_inside_stage_updates_status_no_countdown(capsys):
    runner = Runner()
    with patch.object(sys.stdout, "isatty", return_value=False):
        with runner.stage("my-stage") as s:
            runner.sleep(0.01, "my-sleep-label")
            assert s._status == "ok"  # prev_status restored after sleep
    captured = capsys.readouterr()
    assert captured.out.rstrip().endswith("ok")
    assert "s..." not in captured.out
