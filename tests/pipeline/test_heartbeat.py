import time

from pipeline.heartbeat import Heartbeat, HeartbeatPulse
from pipeline.metrics import parse_human

# --- Heartbeat file class ---


def test_heartbeat_path(tmp_path):
    assert Heartbeat(tmp_path).path == tmp_path / "heartbeat"


def test_heartbeat_write_now_creates_parseable_file(tmp_path):
    hb = Heartbeat(tmp_path)
    hb.write_now()
    content = hb.path.read_text().strip()
    parse_human(content)


def test_heartbeat_read_missing_returns_none(tmp_path):
    assert Heartbeat(tmp_path).read() is None


def test_heartbeat_read_malformed_returns_none(tmp_path):
    hb = Heartbeat(tmp_path)
    hb.path.write_text("not a date")
    assert hb.read() is None


def test_heartbeat_delete_missing_no_exception(tmp_path):
    Heartbeat(tmp_path).delete()


def test_heartbeat_delete_after_write_removes_file(tmp_path):
    hb = Heartbeat(tmp_path)
    hb.write_now()
    assert hb.path.exists()
    hb.delete()
    assert not hb.path.exists()


def test_heartbeat_write_now_bad_dir_swallows_error(tmp_path, capsys):
    bad_dir = tmp_path / "nonexistent_dir"
    hb = Heartbeat(bad_dir)
    hb.write_now()
    captured = capsys.readouterr()
    assert "heartbeat write error" in captured.err


# --- HeartbeatPulse lifecycle ---


def test_pulse_writes_file_within_timeout(tmp_path):
    hb = Heartbeat(tmp_path)
    pulse = HeartbeatPulse(hb, interval=0.05).start()
    time.sleep(0.15)
    assert hb.path.exists()
    parse_human(hb.path.read_text().strip())
    pulse.stop()


def test_pulse_stop_deletes_file(tmp_path):
    hb = Heartbeat(tmp_path)
    pulse = HeartbeatPulse(hb, interval=0.05).start()
    pulse.stop()
    assert not hb.path.exists()


def test_pulse_stop_is_idempotent(tmp_path):
    hb = Heartbeat(tmp_path)
    pulse = HeartbeatPulse(hb, interval=0.05).start()
    pulse.stop()
    pulse.stop()


def test_pulse_write_errors_do_not_kill_thread(tmp_path):
    bad_dir = tmp_path / "nonexistent_dir"
    hb = Heartbeat(bad_dir)
    pulse = HeartbeatPulse(hb, interval=0.05).start()
    time.sleep(0.15)
    pulse.stop()
