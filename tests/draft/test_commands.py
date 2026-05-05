import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# --- command_list ---

def test_command_list_no_runs(tmp_path, capsys):
    from draft import command_list

    with patch("draft.command_list.Path", side_effect=lambda p: tmp_path if p == "/tmp/draft" else Path(p)):
        pass  # can't easily patch Path("/tmp/draft") — use monkeypatch instead


def test_command_list_no_runs_real(tmp_path, capsys, monkeypatch):
    # Redirect /tmp/draft to tmp_path
    import draft.command_list as cmd_list_mod
    monkeypatch.setattr(cmd_list_mod, "__builtins__", __builtins__)

    base = tmp_path / "draft"
    base.mkdir()

    import argparse

    class FakeArgs:
        pass

    original_path = cmd_list_mod.__builtins__  # save for cleanup

    # Monkeypatch the Path("/tmp/draft") call
    from unittest.mock import MagicMock
    import draft.command_list as clm

    with patch.object(clm, "Path") as MockPath:
        mock_base = MagicMock()
        mock_base.exists.return_value = False
        MockPath.return_value = mock_base

        result = clm.run(FakeArgs())
        captured = capsys.readouterr()
        assert "no runs" in captured.out
        assert result == 0


# --- command_delete ---

def test_command_delete_active_pid_refuses(tmp_path, capsys):
    import draft.command_delete as cmd_delete

    run_dir = tmp_path / "260505-120000"
    run_dir.mkdir()
    # Write our own PID so process is alive
    (run_dir / "draft.pid").write_text(str(os.getpid()))
    (run_dir / "state.json").write_text(json.dumps({"data": {}}))

    class FakeArgs:
        run_id = "260505-120000"

    with patch.object(cmd_delete, "Path") as MockPath:
        def path_side(p):
            if p == "/tmp/draft":
                return tmp_path
            return Path(p)

        # Let's just test the PID check logic directly
        pass

    # Direct approach: patch only the base path lookup
    original_path = cmd_delete.Path

    def patched_path(*args, **kwargs):
        p = str(args[0]) if args else ""
        if p == "/tmp/draft":
            return tmp_path
        return original_path(*args, **kwargs)

    with patch("draft.command_delete.Path", side_effect=patched_path):
        args = FakeArgs()
        result = cmd_delete.run(args)

    assert result == 3
    captured = capsys.readouterr()
    assert "active" in captured.err


# --- command_continue ---

def test_command_continue_active_pid_refuses(tmp_path, capsys):
    import draft.command_continue as cmd_continue

    run_dir = tmp_path / "260505-120000"
    run_dir.mkdir()
    (run_dir / "draft.pid").write_text(str(os.getpid()))

    state = {
        "run_id": "260505-120000",
        "run_dir": str(run_dir),
        "completed": [],
        "data": {"branch": "fix", "wt_dir": str(tmp_path / "fix")},
        "step_data": {},
        "step_configs": {},
        "started_at": "2026-05-05T00:00:00",
    }
    (run_dir / "state.json").write_text(json.dumps(state))

    original_path = cmd_continue.Path

    def patched_path(*args, **kwargs):
        p = str(args[0]) if args else ""
        if p == "/tmp/draft":
            return tmp_path
        return original_path(*args, **kwargs)

    class FakeArgs:
        run_id = "260505-120000"

    with patch("draft.command_continue.Path", side_effect=patched_path):
        result = cmd_continue.run(FakeArgs())

    assert result == 3
    captured = capsys.readouterr()
    assert "active" in captured.err


def test_command_continue_deleted_worktree_removes_from_completed(tmp_path, capsys):
    import draft.command_continue as cmd_continue

    run_dir = tmp_path / "260505-120000"
    run_dir.mkdir()

    wt_path = tmp_path / "nonexistent-worktree"  # does not exist

    state = {
        "run_id": "260505-120000",
        "run_dir": str(run_dir),
        "completed": ["worktree-create"],
        "data": {"branch": "fix", "wt_dir": str(wt_path), "repo": str(tmp_path)},
        "step_data": {},
        "step_configs": {},
        "started_at": "2026-05-05T00:00:00",
    }
    (run_dir / "state.json").write_text(json.dumps(state))

    original_path = cmd_continue.Path

    def patched_path(*args, **kwargs):
        p = str(args[0]) if args else ""
        if p == "/tmp/draft":
            return tmp_path
        return original_path(*args, **kwargs)

    class FakeArgs:
        run_id = "260505-120000"

    with patch("draft.command_continue.Path", side_effect=patched_path), \
         patch("draft.command_continue.load_config", return_value={}), \
         patch("draft.command_continue.Pipeline") as MockPipeline:

        MockPipeline.return_value.run.return_value = None

        cmd_continue.run(FakeArgs())

    # After run, worktree-create should have been removed from completed in saved state
    saved = json.loads((run_dir / "state.json").read_text())
    assert "worktree-create" not in saved["completed"]
