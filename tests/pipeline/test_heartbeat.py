import time

from pipeline.heartbeat import Heartbeat
from pipeline.metrics import parse_human


def test_heartbeat_writes_and_updates(tmp_path):
    hb_path = tmp_path / "heartbeat"
    hb = Heartbeat(hb_path, interval=0.05).start()
    time.sleep(0.15)

    assert hb_path.exists()
    content = hb_path.read_text().strip()
    parse_human(content)

    hb.stop()
    assert not hb_path.exists()


def test_heartbeat_writes_at_least_once_even_on_immediate_stop(tmp_path):
    hb_path = tmp_path / "heartbeat"
    hb = Heartbeat(hb_path, interval=0.05).start()
    hb.stop()
    assert not hb_path.exists()


def test_heartbeat_stop_is_idempotent(tmp_path):
    hb_path = tmp_path / "heartbeat"
    hb = Heartbeat(hb_path, interval=0.05).start()
    hb.stop()
    hb.stop()


def test_heartbeat_write_errors_do_not_kill_thread(tmp_path):
    bad_path = tmp_path / "nonexistent_dir" / "heartbeat"
    hb = Heartbeat(bad_path, interval=0.05).start()
    time.sleep(0.15)
    hb.stop()
