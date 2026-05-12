from datetime import UTC, datetime

import pytest

import pipeline.metrics as metrics_module
from pipeline.metrics import (
    RunMetrics,
    SessionMetrics,
    _resolve_name,
    now_human,
    parse_human,
)


def test_now_human_round_trip():
    s = now_human()
    dt = parse_human(s)
    assert dt.tzinfo == UTC
    delta = abs((datetime.now(UTC) - dt).total_seconds())
    assert delta < 1


def test_step_handle_set_and_add(tmp_path):
    session_dict = {"steps": []}
    session_metrics = SessionMetrics(session_dict)
    step_metrics = session_metrics.step_begin("my-step")

    step_metrics.set("k", 1)
    assert step_metrics._dict["data"]["k"] == 1

    step_metrics.add("k", 2)
    assert step_metrics._dict["data"]["k"] == 3

    step_metrics.set("k", "string")
    with pytest.raises(TypeError):
        step_metrics.add("k", 1)


def test_step_handle_validates_name(tmp_path):
    session_dict = {"steps": []}
    session_metrics = SessionMetrics(session_dict)
    step_metrics = session_metrics.step_begin("my-step")

    with pytest.raises(ValueError):
        step_metrics.set("BAD-name", 1)

    step_metrics.set("good_name_1", 1)
    assert step_metrics._dict["data"]["good_name_1"] == 1


def test_step_handle_rejects_string_shadowing_known(monkeypatch):
    import enum

    class FakeKnownMetric(enum.StrEnum):
        FAKE = "fake_metric"

    monkeypatch.setattr(metrics_module, "KnownMetric", FakeKnownMetric)

    with pytest.raises(ValueError):
        _resolve_name("fake_metric")


def test_handle_closed_raises_after_end(tmp_path):
    session_dict = {"steps": []}
    session_metrics = SessionMetrics(session_dict)
    step_metrics = session_metrics.step_begin("my-step")
    step_metrics.end(0)

    with pytest.raises(RuntimeError, match="handle closed"):
        step_metrics.set("k", 1)


def test_session_begin_reconciles_open_prior(tmp_path):
    sessions = [
        {
            "command": "create",
            "started_at": "2025-01-01 10:00:00 UTC",
            "finished_at": None,
            "exit_code": None,
            "steps": [
                {
                    "name": "my-step",
                    "started_at": "2025-01-01 10:00:01 UTC",
                    "finished_at": None,
                    "exit_code": None,
                    "data": {},
                }
            ],
        }
    ]
    rm = RunMetrics(sessions, tmp_path)
    rm.session_begin("continue")

    assert sessions[0]["exit_code"] == -1
    assert sessions[0]["finished_at"] is not None
    assert sessions[0]["steps"][0]["exit_code"] == -1
    assert sessions[0]["steps"][0]["finished_at"] is not None
    assert len(sessions) == 2


def test_reconciliation_uses_heartbeat_when_present(tmp_path):
    hb_ts = "2025-01-01 10:05:00 UTC"
    (tmp_path / "heartbeat").write_text(hb_ts)

    sessions = [
        {
            "command": "create",
            "started_at": "2025-01-01 10:00:00 UTC",
            "finished_at": None,
            "exit_code": None,
            "steps": [],
        }
    ]
    rm = RunMetrics(sessions, tmp_path)
    rm.session_begin("continue")

    assert sessions[0]["finished_at"] == hb_ts
    assert not (tmp_path / "heartbeat").exists()


def test_reconciliation_falls_back_to_step_finish(tmp_path):
    step_ts = "2025-01-01 10:03:00 UTC"
    sessions = [
        {
            "command": "create",
            "started_at": "2025-01-01 10:00:00 UTC",
            "finished_at": None,
            "exit_code": None,
            "steps": [
                {
                    "name": "my-step",
                    "started_at": "2025-01-01 10:00:01 UTC",
                    "finished_at": step_ts,
                    "exit_code": 0,
                    "data": {},
                }
            ],
        }
    ]
    rm = RunMetrics(sessions, tmp_path)
    rm.session_begin("continue")

    assert sessions[0]["finished_at"] == step_ts


def test_reconciliation_falls_back_to_session_started_at(tmp_path):
    started = "2025-01-01 10:00:00 UTC"
    sessions = [
        {
            "command": "create",
            "started_at": started,
            "finished_at": None,
            "exit_code": None,
            "steps": [],
        }
    ]
    rm = RunMetrics(sessions, tmp_path)
    rm.session_begin("continue")

    assert sessions[0]["finished_at"] == started


def test_reconciliation_deletes_stale_heartbeat_when_last_already_closed(tmp_path):
    hb_path = tmp_path / "heartbeat"
    hb_path.write_text("2025-01-01 10:05:00 UTC")

    sessions = [
        {
            "command": "create",
            "started_at": "2025-01-01 10:00:00 UTC",
            "finished_at": "2025-01-01 10:04:00 UTC",
            "exit_code": 0,
            "steps": [],
        }
    ]
    rm = RunMetrics(sessions, tmp_path)
    rm.session_begin("continue")

    assert not hb_path.exists()
    assert sessions[0]["finished_at"] == "2025-01-01 10:04:00 UTC"
