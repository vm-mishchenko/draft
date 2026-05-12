import time
from unittest.mock import MagicMock, patch

from draft.steps.implement_spec._live_status import (
    MAX_CHARS,
    MIN_BYTES,
    LiveStatusSummarizer,
    _normalize,
    _read_tail,
)
from pipeline.runner import LLMResult

# --- _read_tail ---


def test_read_tail_full_file(tmp_path):
    f = tmp_path / "log.txt"
    content = b"hello world"
    f.write_bytes(content)
    result = _read_tail(f, len(content), 4096)
    assert result == "hello world"


def test_read_tail_truncates_to_max_bytes(tmp_path):
    f = tmp_path / "log.txt"
    content = b"abcde12345"
    f.write_bytes(content)
    result = _read_tail(f, len(content), 5)
    assert result == "12345"


def test_read_tail_invalid_utf8(tmp_path):
    f = tmp_path / "log.txt"
    content = b"valid\xff\xfebytes"
    f.write_bytes(content)
    result = _read_tail(f, len(content), 4096)
    assert "valid" in result
    assert "�" in result


# --- _normalize ---


def test_normalize_simple():
    assert _normalize("editing runner", 30) == "editing runner"


def test_normalize_strips_whitespace():
    assert _normalize("  editing runner  ", 30) == "editing runner"


def test_normalize_first_line_only():
    assert _normalize("editing\nrunner", 30) == "editing"


def test_normalize_empty():
    assert _normalize("", 30) == ""


def test_normalize_only_whitespace_lines():
    assert _normalize("\n\n  \n", 30) == ""


def test_normalize_truncates_long_line():
    text = "x" * 50
    result = _normalize(text, 30)
    assert len(result) == 30
    assert result.endswith("…")


def test_normalize_first_non_blank_line():
    assert _normalize("\n\nediting runner", 30) == "editing runner"


def test_normalize_at_max_chars_no_truncation():
    text = "a" * 30
    result = _normalize(text, 30)
    assert result == text
    assert not result.endswith("…")


def test_normalize_one_over_max_chars():
    text = "a" * 31
    result = _normalize(text, 30)
    assert len(result) == 30
    assert result.endswith("…")


# --- LiveStatusSummarizer ---


def _make_summarizer(tmp_path, engine=None, handle=None, prefix="attempt 1/10 — "):
    log = tmp_path / "implement-spec.log"
    if handle is None:
        handle = MagicMock()
    if engine is None:
        engine = MagicMock()
        engine.run_llm.return_value = LLMResult(rc=0, final_text="editing foo")
    s = LiveStatusSummarizer(
        handle=handle,
        engine=engine,
        step_metrics=MagicMock(),
        log_path=log,
        prefix=prefix,
    )
    return s, handle, engine, log


def test_tick_file_missing_pushes_fallback(tmp_path):
    s, handle, engine, log = _make_summarizer(tmp_path)
    s._tick()
    handle.update.assert_called_once_with("attempt 1/10 — implementing")
    engine.run_llm.assert_not_called()


def test_tick_file_too_small_no_update(tmp_path):
    s, handle, engine, log = _make_summarizer(tmp_path)
    log.write_bytes(b"x" * (MIN_BYTES - 1))
    s._tick()
    handle.update.assert_not_called()
    engine.run_llm.assert_not_called()


def test_tick_size_unchanged_no_second_call(tmp_path):
    s, handle, engine, log = _make_summarizer(tmp_path)
    log.write_bytes(b"x" * MIN_BYTES)
    s._tick()
    assert engine.run_llm.call_count == 1
    handle.update.reset_mock()
    engine.run_llm.reset_mock()
    s._tick()
    engine.run_llm.assert_not_called()
    handle.update.assert_not_called()


def test_tick_file_grows_calls_run_llm(tmp_path):
    s, handle, engine, log = _make_summarizer(tmp_path)
    log.write_bytes(b"x" * MIN_BYTES)
    s._tick()
    call_kwargs = engine.run_llm.call_args[1]
    assert call_kwargs["log_path"] is None
    assert call_kwargs["extra_args"] == ["--model", "claude-3-5-haiku-latest"]
    assert call_kwargs["allowed_tools"] == []
    assert call_kwargs["timeout"] == 10
    assert "x" * 10 in call_kwargs["prompt"]


def test_tick_run_llm_raises_pushes_fallback(tmp_path):
    engine = MagicMock()
    engine.run_llm.side_effect = RuntimeError("network error")
    s, handle, _, log = _make_summarizer(tmp_path, engine=engine)
    log.write_bytes(b"x" * MIN_BYTES)
    s._tick()
    handle.update.assert_called_once_with("attempt 1/10 — implementing")


def test_tick_run_llm_returns_rc1_pushes_fallback(tmp_path):
    engine = MagicMock()
    engine.run_llm.return_value = LLMResult(rc=1, final_text="anything")
    s, handle, _, log = _make_summarizer(tmp_path, engine=engine)
    log.write_bytes(b"x" * MIN_BYTES)
    s._tick()
    handle.update.assert_called_once_with("attempt 1/10 — implementing")


def test_tick_empty_final_text_pushes_fallback(tmp_path):
    engine = MagicMock()
    engine.run_llm.return_value = LLMResult(rc=0, final_text="")
    s, handle, _, log = _make_summarizer(tmp_path, engine=engine)
    log.write_bytes(b"x" * MIN_BYTES)
    s._tick()
    handle.update.assert_called_once_with("attempt 1/10 — implementing")


def test_tick_whitespace_only_final_text_pushes_fallback(tmp_path):
    engine = MagicMock()
    engine.run_llm.return_value = LLMResult(rc=0, final_text="\n  \n")
    s, handle, _, log = _make_summarizer(tmp_path, engine=engine)
    log.write_bytes(b"x" * MIN_BYTES)
    s._tick()
    handle.update.assert_called_once_with("attempt 1/10 — implementing")


def test_tick_no_case_folding(tmp_path):
    engine = MagicMock()
    engine.run_llm.return_value = LLMResult(rc=0, final_text="EDITING runner")
    s, handle, _, log = _make_summarizer(tmp_path, engine=engine)
    log.write_bytes(b"x" * MIN_BYTES)
    s._tick()
    handle.update.assert_called_once_with("attempt 1/10 — EDITING runner")


def test_tick_long_final_text_truncated(tmp_path):
    engine = MagicMock()
    engine.run_llm.return_value = LLMResult(rc=0, final_text="x" * 100)
    s, handle, _, log = _make_summarizer(tmp_path, engine=engine)
    log.write_bytes(b"x" * MIN_BYTES)
    s._tick()
    update_arg = handle.update.call_args[0][0]
    suffix = update_arg.replace("attempt 1/10 — ", "")
    assert len(suffix) == MAX_CHARS
    assert suffix.endswith("…")


def test_tick_two_consecutive_different_content(tmp_path):
    call_count = 0

    def run_llm_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        text = "editing foo" if call_count == 1 else "writing tests"
        return LLMResult(rc=0, final_text=text)

    engine = MagicMock()
    engine.run_llm.side_effect = run_llm_side_effect
    s, handle, _, log = _make_summarizer(tmp_path, engine=engine)

    log.write_bytes(b"x" * MIN_BYTES)
    s._tick()
    log.write_bytes(b"x" * (MIN_BYTES + 1))
    s._tick()

    calls = [c[0][0] for c in handle.update.call_args_list]
    assert "attempt 1/10 — editing foo" in calls
    assert "attempt 1/10 — writing tests" in calls


def test_start_stop_daemon_thread(tmp_path):
    s, _, _, _ = _make_summarizer(tmp_path)
    with patch.object(s, "_loop"):
        s.start()
        assert s._thread.is_alive()
        assert s._thread.daemon
        s.stop()
        s._thread.join(timeout=1)
        assert not s._thread.is_alive()


def test_start_stop_joins_promptly(tmp_path):
    s, _, _, _ = _make_summarizer(tmp_path)
    s.start()
    t0 = time.monotonic()
    s.stop()
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0
