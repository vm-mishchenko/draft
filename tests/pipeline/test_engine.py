import sys
from unittest.mock import patch

from pipeline.engine import Engine


def test_tty_ticker_yields_set_status_and_prints_final_line(capsys):
    engine = Engine()
    with patch.object(sys.stdout, "isatty", return_value=False):
        with engine.tty_ticker("my-label") as set_status:
            set_status("ok")

    captured = capsys.readouterr()
    assert "my-label" in captured.out
    assert captured.out.rstrip().endswith("ok")
    assert "\r" not in captured.out


def test_tty_ticker_default_status_is_question_mark(capsys):
    engine = Engine()
    with patch.object(sys.stdout, "isatty", return_value=False):
        with engine.tty_ticker("noop"):
            pass

    captured = capsys.readouterr()
    assert captured.out.rstrip().endswith("?")


def test_tty_ticker_propagates_exceptions(capsys):
    engine = Engine()
    raised = False
    try:
        with patch.object(sys.stdout, "isatty", return_value=False):
            with engine.tty_ticker("boom"):
                raise RuntimeError("boom")
    except RuntimeError:
        raised = True

    assert raised
    captured = capsys.readouterr()
    assert "boom" in captured.out
