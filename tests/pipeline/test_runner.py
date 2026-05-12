from unittest.mock import MagicMock, patch

from pipeline.runner import LLMResult, Runner


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
