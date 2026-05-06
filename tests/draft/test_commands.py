import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


# --- command_list ---

def test_command_list_no_runs(tmp_path, capsys):
    import draft.command_list as clm

    with patch("draft.command_list.runs_base", return_value=tmp_path / "nonexistent"):
        result = clm.run(object())
    captured = capsys.readouterr()
    assert "no runs" in captured.out
    assert result == 0


def test_command_list_no_runs_real(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    base.mkdir()

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(object())
    captured = capsys.readouterr()
    assert "no runs" in captured.out
    assert result == 0


# --- command_delete ---

def test_command_delete_active_pid_refuses(tmp_path, capsys):
    import draft.command_delete as cmd_delete

    project_dir = tmp_path / "myproject"
    run_dir = project_dir / "260505-120000"
    run_dir.mkdir(parents=True)
    (run_dir / "draft.pid").write_text(str(os.getpid()))
    (run_dir / "state.json").write_text(json.dumps({"data": {}}))

    class FakeArgs:
        run_id = "260505-120000"

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cmd_delete.run(FakeArgs())

    assert result == 3
    captured = capsys.readouterr()
    assert "active" in captured.err


# --- command_continue ---

def test_command_continue_active_pid_refuses(tmp_path, capsys):
    import draft.command_continue as cmd_continue

    project_dir = tmp_path / "myproject"
    run_dir = project_dir / "260505-120000"
    run_dir.mkdir(parents=True)
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

    class FakeArgs:
        run_id = "260505-120000"

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cmd_continue.run(FakeArgs())

    assert result == 3
    captured = capsys.readouterr()
    assert "active" in captured.err


def test_command_continue_deleted_worktree_removes_from_completed(tmp_path, capsys):
    import draft.command_continue as cmd_continue

    project_dir = tmp_path / "myproject"
    run_dir = project_dir / "260505-120000"
    run_dir.mkdir(parents=True)

    wt_path = tmp_path / "nonexistent-worktree"

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

    class FakeArgs:
        run_id = "260505-120000"

    with patch("draft.runs.find_run_dir", return_value=run_dir), \
         patch("draft.command_continue.load_config", return_value={}), \
         patch("draft.command_continue.Pipeline") as MockPipeline:

        MockPipeline.return_value.run.return_value = None
        cmd_continue.run(FakeArgs())

    saved = json.loads((run_dir / "state.json").read_text())
    assert "worktree-create" not in saved["completed"]
