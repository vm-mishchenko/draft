import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from pipeline.metrics import KnownMetric
from pipeline.runner import TIMEOUT_EXIT, LLMResult, Runner


def test_stage_no_update_final_line_ends_with_ok(capsys):
    runner = Runner()
    with (
        patch.object(sys.stdout, "isatty", return_value=False),
        runner.stage("my-label"),
    ):
        pass
    captured = capsys.readouterr()
    assert "my-label" in captured.out
    assert captured.out.rstrip().endswith("ok")
    assert "\r" not in captured.out


def test_stage_update_final_line_ends_with_last_status(capsys):
    runner = Runner()
    with (
        patch.object(sys.stdout, "isatty", return_value=False),
        runner.stage("my-label") as s,
    ):
        s.update("3/10")
    captured = capsys.readouterr()
    assert captured.out.rstrip().endswith("3/10")


def test_stage_exception_final_line_ends_with_failed(capsys):
    runner = Runner()
    raised = False
    try:
        with (
            patch.object(sys.stdout, "isatty", return_value=False),
            runner.stage("boom"),
        ):
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
    with (
        patch.object(sys.stdout, "isatty", return_value=False),
        runner.stage("my-stage") as s,
    ):
        runner.sleep(0.01, "my-sleep-label")
        assert s._status == "ok"  # prev_status restored after sleep
    captured = capsys.readouterr()
    assert captured.out.rstrip().endswith("ok")
    assert "s..." not in captured.out


# --- FakeStepMetrics helper ---


class FakeStepMetrics:
    def __init__(self):
        self.calls: list[tuple] = []

    def add(self, name, value):
        self.calls.append((name, value))


def _emit_script(lines: list[str]) -> str:
    """Build a Python one-liner that writes lines to stdout."""
    body = "import sys\n"
    for line in lines:
        body += f"sys.stdout.write({line!r})\nsys.stdout.flush()\n"
    return body


def _patched_popen(lines: list[str]):
    """Context manager patching subprocess.Popen in runner to emit lines."""
    script = _emit_script(lines)
    orig = subprocess.Popen

    def _fake_popen(argv, **kw):
        return orig(["python3", "-c", script], **kw)

    return patch("subprocess.Popen", side_effect=_fake_popen)


# --- run_llm tests ---


def test_run_llm_success_populates_all_metrics(tmp_path):
    runner = Runner()
    log = tmp_path / "out.log"
    metrics = FakeStepMetrics()

    result_event = json.dumps(
        {
            "type": "result",
            "total_cost_usd": 0.42,
            "duration_ms": 12000,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
    )
    assistant_event = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        }
    )

    with _patched_popen([assistant_event + "\n", result_event + "\n"]):
        result = runner.run_llm(
            prompt="p",
            cwd=None,
            log_path=log,
            step_metrics=metrics,
        )

    assert result.rc == 0
    assert result.final_text == "hello"
    calls = dict(metrics.calls)
    assert calls[KnownMetric.LLM_COST_USD] == pytest.approx(0.42)
    assert calls[KnownMetric.LLM_INPUT_TOKENS] == 100
    assert calls[KnownMetric.LLM_OUTPUT_TOKENS] == 50
    assert calls[KnownMetric.LLM_DURATION_MS] == 12000


def test_run_llm_missing_usage_keys_default_to_zero(tmp_path):
    runner = Runner()
    log = tmp_path / "out.log"
    metrics = FakeStepMetrics()

    result_event = json.dumps(
        {"type": "result", "total_cost_usd": 0.1, "duration_ms": 500}
    )

    with _patched_popen([result_event + "\n"]):
        runner.run_llm(
            prompt="p",
            cwd=None,
            log_path=log,
            step_metrics=metrics,
        )

    calls = dict(metrics.calls)
    assert calls[KnownMetric.LLM_INPUT_TOKENS] == 0
    assert calls[KnownMetric.LLM_OUTPUT_TOKENS] == 0
    assert calls[KnownMetric.LLM_COST_USD] == pytest.approx(0.1)


def test_run_llm_last_assistant_text_block_wins(tmp_path):
    runner = Runner()
    log = tmp_path / "out.log"
    metrics = FakeStepMetrics()

    first = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "first"}]},
        }
    )
    second = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "second"}]},
        }
    )

    with _patched_popen([first + "\n", second + "\n"]):
        result = runner.run_llm(
            prompt="p",
            cwd=None,
            log_path=log,
            step_metrics=metrics,
        )

    assert result.final_text == "second"


def test_run_llm_tool_use_only_final_text_empty(tmp_path):
    runner = Runner()
    log = tmp_path / "out.log"
    metrics = FakeStepMetrics()

    tool_event = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
                ]
            },
        }
    )

    with _patched_popen([tool_event + "\n"]):
        result = runner.run_llm(
            prompt="p",
            cwd=None,
            log_path=log,
            step_metrics=metrics,
        )

    assert result.final_text == ""


def test_run_llm_empty_stream_returns_empty_final_text(tmp_path):
    runner = Runner()
    log = tmp_path / "out.log"
    metrics = FakeStepMetrics()

    with _patched_popen([]):
        result = runner.run_llm(
            prompt="p",
            cwd=None,
            log_path=log,
            step_metrics=metrics,
        )

    assert result.rc == 0
    assert result.final_text == ""


def test_run_llm_non_json_line_written_verbatim_parsing_continues(tmp_path):
    runner = Runner()
    log = tmp_path / "out.log"
    metrics = FakeStepMetrics()

    result_event = json.dumps(
        {"type": "result", "total_cost_usd": 0.0, "duration_ms": 1, "usage": {}}
    )

    with _patched_popen(["not json\n", result_event + "\n"]):
        runner.run_llm(
            prompt="p",
            cwd=None,
            log_path=log,
            step_metrics=metrics,
        )

    content = log.read_text()
    assert "not json" in content
    assert calls_duration_ms(metrics) == 1


def calls_duration_ms(metrics: FakeStepMetrics) -> int:
    return dict(metrics.calls)[KnownMetric.LLM_DURATION_MS]


def test_run_llm_timeout_records_wall_clock_duration(tmp_path):
    runner = Runner()
    log = tmp_path / "out.log"
    metrics = FakeStepMetrics()

    # sleep script so it times out
    script = "import time\ntime.sleep(10)\n"
    orig = subprocess.Popen

    def _slow_popen(argv, **kw):
        return orig(["python3", "-c", script], **kw)

    with patch("subprocess.Popen", side_effect=_slow_popen):
        result = runner.run_llm(
            prompt="p",
            cwd=None,
            log_path=log,
            step_metrics=metrics,
            timeout=0.1,
        )

    assert result.rc == TIMEOUT_EXIT
    calls = dict(metrics.calls)
    assert calls[KnownMetric.LLM_DURATION_MS] > 0
    assert calls[KnownMetric.LLM_COST_USD] == 0.0
    assert calls[KnownMetric.LLM_INPUT_TOKENS] == 0
    assert calls[KnownMetric.LLM_OUTPUT_TOKENS] == 0


def test_run_llm_missing_binary_raises_runtime_error(tmp_path):
    runner = Runner()
    log = tmp_path / "out.log"

    with patch("subprocess.Popen", side_effect=FileNotFoundError("claude")):
        with pytest.raises(RuntimeError, match="claude"):
            runner.run_llm(
                prompt="p",
                cwd=None,
                log_path=log,
                step_metrics=FakeStepMetrics(),
            )


def test_run_llm_allowed_tools_builds_argv(tmp_path):
    runner = Runner()
    log = tmp_path / "out.log"
    captured_argv = []
    orig = subprocess.Popen

    def _capture_popen(argv, **kw):
        captured_argv.extend(argv)
        return orig(["python3", "-c", ""], **kw)

    with patch("subprocess.Popen", side_effect=_capture_popen):
        runner.run_llm(
            prompt="p",
            cwd=None,
            log_path=log,
            step_metrics=FakeStepMetrics(),
            allowed_tools=["A", "B"],
        )

    assert "--allowedTools" in captured_argv
    idx = captured_argv.index("--allowedTools")
    assert captured_argv[idx + 1] == "A,B"


def test_run_llm_empty_allowed_tools_omits_flag(tmp_path):
    runner = Runner()
    log = tmp_path / "out.log"
    captured_argv = []
    orig = subprocess.Popen

    def _capture_popen(argv, **kw):
        captured_argv.extend(argv)
        return orig(["python3", "-c", ""], **kw)

    with patch("subprocess.Popen", side_effect=_capture_popen):
        runner.run_llm(
            prompt="p",
            cwd=None,
            log_path=log,
            step_metrics=FakeStepMetrics(),
            allowed_tools=[],
        )

    assert "--allowedTools" not in captured_argv


def test_run_llm_extra_args_appended_to_argv(tmp_path):
    runner = Runner()
    log = tmp_path / "out.log"
    captured_argv = []
    orig = subprocess.Popen

    def _capture_popen(argv, **kw):
        captured_argv.extend(argv)
        return orig(["python3", "-c", ""], **kw)

    with patch("subprocess.Popen", side_effect=_capture_popen):
        runner.run_llm(
            prompt="p",
            cwd=None,
            log_path=log,
            step_metrics=FakeStepMetrics(),
            extra_args=["--permission-mode", "acceptEdits"],
        )

    assert "--permission-mode" in captured_argv
    idx = captured_argv.index("--permission-mode")
    assert captured_argv[idx + 1] == "acceptEdits"
