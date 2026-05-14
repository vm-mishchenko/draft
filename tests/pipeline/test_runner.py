import io
from unittest.mock import MagicMock, patch

import pipeline.runner as _runner_mod
from draft.hooks import _status_text
from pipeline.runner import (
    LLMResult,
    Runner,
    StageHandle,
    _format_event,
    _summarize_tool_input,
)


def _make_runner():
    return Runner()


def _mock_popen(stdout_lines=None, returncode=0):
    if stdout_lines is None:
        stdout_lines = []
    proc = MagicMock()
    proc.stdout = iter([line.encode() for line in stdout_lines])
    proc.returncode = returncode
    proc.wait.return_value = returncode
    return proc


def test_run_llm_with_real_path_writes_log(tmp_path):
    log = tmp_path / "run.log"
    proc = _mock_popen()
    step_metrics = MagicMock()

    with patch("subprocess.Popen", return_value=proc):
        runner = _make_runner()
        result = runner.run_llm(
            prompt="hello",
            cwd=None,
            log_path=log,
            step_metrics=step_metrics,
        )

    assert isinstance(result, LLMResult)
    assert log.exists()
    step_metrics.add.assert_called()


def test_run_llm_with_log_path_none_no_file_created(tmp_path):
    proc = _mock_popen()
    step_metrics = MagicMock()

    with patch("subprocess.Popen", return_value=proc):
        runner = _make_runner()
        result = runner.run_llm(
            prompt="hello",
            cwd=None,
            log_path=None,
            step_metrics=step_metrics,
        )

    assert isinstance(result, LLMResult)
    assert list(tmp_path.iterdir()) == []
    step_metrics.add.assert_called()


def test_stage_handle_sleep_zero_is_noop():
    handle = StageHandle()
    handle.update("before")
    handle.sleep(0)
    assert handle._status == "before"
    assert handle._countdown_until is None


def test_stage_handle_sleep_with_label_updates_and_restores():
    handle = StageHandle()
    handle.update("initial")
    statuses_during = []

    def fake_sleep(s):
        statuses_during.append(handle._status)
        assert handle._countdown_until is not None

    with patch("pipeline.runner.time.sleep", side_effect=fake_sleep):
        handle.sleep(1, label="sleeping now")

    assert statuses_during == ["sleeping now"]
    assert handle._status == "initial"
    assert handle._countdown_until is None


def test_stage_handle_sleep_without_label_does_not_change_status():
    handle = StageHandle()
    handle.update("my status")
    statuses_during = []

    def fake_sleep(s):
        statuses_during.append(handle._status)

    with patch("pipeline.runner.time.sleep", side_effect=fake_sleep):
        handle.sleep(1)

    assert statuses_during == ["my status"]
    assert handle._status == "my status"


def test_stage_handle_stderr_buffers():
    handle = StageHandle()
    handle.stderr("line one")
    handle.stderr("line two")
    assert handle._stderr_lines == ["line one", "line two"]


def test_runner_stage_flushes_stderr_after_stdout(tmp_path):
    runner = Runner()
    captured_stderr = io.StringIO()
    captured_stdout = io.StringIO()

    with (
        patch("sys.stdout", captured_stdout),
        patch("sys.stderr", captured_stderr),
        runner.stage("test-stage") as s,
    ):
        s.stderr("error message")

    assert "error message\n" in captured_stderr.getvalue()
    stdout_val = captured_stdout.getvalue()
    assert "test-stage" in stdout_val


def test_runner_has_no_sleep_method():
    runner = Runner()
    assert getattr(runner, "sleep", None) is None


def test_status_text_ok():
    assert _status_text(0) == "ok"


def test_status_text_nonzero_is_failed():
    assert _status_text(1) == "failed"
    assert _status_text(124) == "failed"
    assert _status_text(2) == "failed"


def _assistant_event(blocks):
    return {"type": "assistant", "message": {"content": blocks}}


def _user_tool_result_event(content):
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": content}]},
    }


def test_format_event_text_full_multiline():
    event = _assistant_event([{"type": "text", "text": "line1\nline2\nline3"}])
    assert _format_event(event) == "\n[text] line1\nline2\nline3"


def test_format_event_think_full_multiline():
    event = _assistant_event([{"type": "thinking", "thinking": "a\nb"}])
    assert _format_event(event) == "\n[think] a\nb"


def test_format_event_ok_full_multiline():
    event = _user_tool_result_event("x\ny\nz")
    assert _format_event(event) == "\n[ok]   x\ny\nz"


def test_format_event_assistant_block_separators():
    event = _assistant_event(
        [
            {"type": "thinking", "thinking": "T"},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/p"}},
            {"type": "text", "text": "X"},
        ]
    )
    result = _format_event(event)
    assert result == "\n[think] T\n[tool] Read(/p)\n\n[text] X"


def test_format_event_text_whitespace_only_skipped():
    event = _assistant_event([{"type": "text", "text": "   \n  "}])
    assert _format_event(event) is None


def test_summarize_tool_input_bash_full_multiline_heredoc():
    cmd = "cat <<'EOF'\nline1\nline2\nEOF\n"
    assert _summarize_tool_input("Bash", {"command": cmd}) == cmd


def test_summarize_tool_input_bash_long_single_line():
    cmd = "x" * 250
    result = _summarize_tool_input("Bash", {"command": cmd})
    assert len(result) == 250
    assert result == cmd


def test_summarize_tool_input_default_branch_long_payload():
    inp = {"big": "x" * 5000}
    result = _summarize_tool_input("mcp_some_tool", inp)
    assert len(result) > 5000
    assert result.startswith('{"big":')


def test_summarize_tool_input_unchanged_branches():
    assert _summarize_tool_input("Read", {"file_path": "/f"}) == "/f"
    assert _summarize_tool_input("Write", {"file_path": "/f"}) == "/f"
    assert _summarize_tool_input("Edit", {"file_path": "/f"}) == "/f"
    assert _summarize_tool_input("Grep", {"pattern": "abc"}) == repr("abc")
    assert _summarize_tool_input("Glob", {"pattern": "*.py"}) == repr("*.py")
    assert _summarize_tool_input("TodoWrite", {"todos": [1, 2, 3]}) == "3 todos"


def test_first_line_helper_removed():
    assert not hasattr(_runner_mod, "_first_line")


def test_run_llm_log_path_none_step_metrics_updated():
    proc = _mock_popen(
        stdout_lines=[
            '{"type": "result", "total_cost_usd": 0.01, "duration_ms": 500, "usage": {"input_tokens": 100, "output_tokens": 50}}\n'
        ]
    )
    step_metrics = MagicMock()

    with patch("subprocess.Popen", return_value=proc):
        runner = _make_runner()
        runner.run_llm(
            prompt="hello",
            cwd=None,
            log_path=None,
            step_metrics=step_metrics,
        )

    assert step_metrics.add.call_count >= 4
