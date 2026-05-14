import io
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


def _make_list_run(base, run_id, state):
    run_dir = base / "myproject" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(state))
    return run_dir


def test_command_list_full_pipeline_shows_5(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": ["worktree-create", "code-spec"],
        "data": {
            "worktree_mode": "worktree",
            "pr_mode": "open",
            "skip_pr": False,
            "pipeline": "create",
        },
    }
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(object())
    assert "2/5" in capsys.readouterr().out


def test_command_list_skip_pr_shows_2(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": ["worktree-create"],
        "data": {"skip_pr": True, "pipeline": "create"},
    }
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(object())
    assert "1/2" in capsys.readouterr().out


def test_command_list_reuse_existing_shows_4(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": ["code-spec"],
        "data": {
            "worktree_mode": "reuse-existing",
            "pr_mode": "open",
            "pipeline": "create",
        },
    }
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(object())
    assert "1/4" in capsys.readouterr().out


def test_command_list_reuse_pr_shows_4(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": ["worktree-create", "code-spec"],
        "data": {"worktree_mode": "worktree", "pr_mode": "reuse", "pipeline": "create"},
    }
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(object())
    assert "2/4" in capsys.readouterr().out


def test_command_list_no_pipeline_shows_corrupt(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": ["worktree-create", "code-spec"],
        "data": {},
    }
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(object())
    assert "corrupt" in capsys.readouterr().out


def test_command_list_missing_state_shows_dash(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(object())
    out = capsys.readouterr().out
    assert result == 0
    lines = [line for line in out.splitlines() if "260508-100000" in line]
    assert lines and "-" in lines[0]


def test_command_list_corrupt_state_shows_corrupt(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("not json{{{")

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(object())
    out = capsys.readouterr().out
    assert result == 0
    assert "corrupt" in out


# --- _workspace_status ---


def test_workspace_status_existing_dir(tmp_path):
    from draft.command_list import _workspace_status

    assert _workspace_status(str(tmp_path)) == "yes"


def test_workspace_status_missing_dir(tmp_path):
    from draft.command_list import _workspace_status

    assert _workspace_status(str(tmp_path / "nonexistent")) == "no"


def test_workspace_status_empty_string():
    from draft.command_list import _workspace_status

    assert _workspace_status("") == "-"


def test_workspace_status_none():
    from draft.command_list import _workspace_status

    assert _workspace_status(None) == "-"


def test_workspace_status_oserror(monkeypatch):
    from unittest.mock import patch

    from draft.command_list import _workspace_status

    with patch("draft.command_list.Path") as MockPath:
        MockPath.return_value.is_dir.side_effect = OSError("permission denied")
        result = _workspace_status("/some/path")

    assert result == "-"


def test_command_list_workspace_column_yes(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    wt = tmp_path / "my-worktree"
    wt.mkdir()
    state = {
        "completed": [],
        "data": {"wt_dir": str(wt), "pipeline": "create"},
    }
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(object())
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if "260508-100000" in line]
    assert lines and "yes" in lines[0]


def test_command_list_workspace_column_no(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": [],
        "data": {"wt_dir": str(tmp_path / "nonexistent"), "pipeline": "create"},
    }
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(object())
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if "260508-100000" in line]
    assert lines and "no" in lines[0]


def test_command_list_workspace_column_absent_wt_dir(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {}}
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(object())
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if "260508-100000" in line]
    assert lines
    # The WORKSPACE column should show '-'
    # Split by multiple spaces to check the workspace field
    assert "-" in lines[0]


def test_command_list_missing_state_workspace_dash(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260508-100001"
    run_dir.mkdir(parents=True)

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(object())
    out = capsys.readouterr().out
    assert result == 0
    lines = [line for line in out.splitlines() if "260508-100001" in line]
    assert lines and "WORKSPACE" not in lines[0]
    assert "WORKSPACE" in out.splitlines()[0]


def test_command_list_corrupt_state_workspace_dash(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260508-100002"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("not json{{{")

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(object())
    out = capsys.readouterr().out
    assert result == 0
    assert "WORKSPACE" in out.splitlines()[0]
    lines = [line for line in out.splitlines() if "260508-100002" in line]
    assert lines


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
        delete_branch = False

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cmd_delete.run(FakeArgs())

    assert result == 3
    captured = capsys.readouterr()
    assert "active" in captured.err


def test_command_delete_with_delete_branch_flag(tmp_path, capsys):
    import draft.command_delete as cmd_delete

    project_dir = tmp_path / "myproject"
    run_dir = project_dir / "260505-120000"
    run_dir.mkdir(parents=True)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    state = {
        "data": {
            "branch": "draft/feature-x",
            "repo": str(repo_dir),
            "wt_dir": str(tmp_path / "wt-already-gone"),
        }
    }
    (run_dir / "state.json").write_text(json.dumps(state))

    class FakeArgs:
        run_id = "260505-120000"
        delete_branch = True

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        mock_run.return_value.stdout = ""
        result = cmd_delete.run(FakeArgs())

    assert result == 0
    assert not run_dir.exists()
    branch_calls = [
        call
        for call in mock_run.call_args_list
        if call.args and call.args[0][:3] == ["git", "branch", "-D"]
    ]
    assert len(branch_calls) == 1
    assert branch_calls[0].args[0] == ["git", "branch", "-D", "draft/feature-x"]
    assert branch_calls[0].kwargs.get("cwd") == str(repo_dir)
    captured = capsys.readouterr()
    assert "deleted branch draft/feature-x" in captured.out


def test_command_delete_without_flag_skips_branch_deletion(tmp_path, capsys):
    import draft.command_delete as cmd_delete

    project_dir = tmp_path / "myproject"
    run_dir = project_dir / "260505-120000"
    run_dir.mkdir(parents=True)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    state = {
        "data": {
            "branch": "draft/feature-x",
            "repo": str(repo_dir),
            "wt_dir": str(tmp_path / "wt-already-gone"),
        }
    }
    (run_dir / "state.json").write_text(json.dumps(state))

    class FakeArgs:
        run_id = "260505-120000"
        delete_branch = False

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.subprocess.run") as mock_run,
    ):
        result = cmd_delete.run(FakeArgs())

    assert result == 0
    branch_calls = [
        call
        for call in mock_run.call_args_list
        if call.args and call.args[0][:3] == ["git", "branch", "-D"]
    ]
    assert branch_calls == []


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
        "completed": ["create-worktree"],
        "data": {
            "branch": "fix",
            "wt_dir": str(wt_path),
            "repo": str(tmp_path),
            "pipeline": "create",
        },
        "step_data": {},
        "step_configs": {},
        "started_at": "2026-05-05T00:00:00",
    }
    (run_dir / "state.json").write_text(json.dumps(state))

    class FakeArgs:
        run_id = "260505-120000"

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.command_continue.load_config", return_value={}),
        patch("draft.command_continue.Pipeline") as MockPipeline,
    ):
        MockPipeline.return_value.run.return_value = None
        cmd_continue.run(FakeArgs())

    saved = json.loads((run_dir / "state.json").read_text())
    assert "create-worktree" not in saved["completed"]


# --- runs.delete_run ---


def _make_run_dir(tmp_path, run_id="260505-120000", project="myproject", state=None):
    run_dir = tmp_path / project / run_id
    run_dir.mkdir(parents=True)
    if state is not None:
        (run_dir / "state.json").write_text(json.dumps(state))
    return run_dir


def test_delete_run_active_pid_returns_active(tmp_path):
    import draft.runs as r

    run_dir = _make_run_dir(tmp_path)
    (run_dir / "draft.pid").write_text(str(os.getpid()))

    result = r.delete_run(run_dir)

    assert result["status"] == "active"
    assert run_dir.exists()


def test_delete_run_no_state_no_pid(tmp_path):
    import draft.runs as r

    run_dir = _make_run_dir(tmp_path)

    result = r.delete_run(run_dir)

    assert result["status"] == "deleted"
    assert not run_dir.exists()


def test_delete_run_wt_dir_not_on_disk_no_git_call(tmp_path):
    import draft.runs as r

    state = {
        "data": {"wt_dir": str(tmp_path / "gone"), "branch": "b", "repo": str(tmp_path)}
    }
    run_dir = _make_run_dir(tmp_path, state=state)

    with patch("draft.runs.subprocess.run") as mock_run:
        result = r.delete_run(run_dir)

    worktree_calls = [
        c
        for c in mock_run.call_args_list
        if c.args and c.args[0][:3] == ["git", "worktree", "remove"]
    ]
    assert worktree_calls == []
    assert result["status"] == "deleted"
    assert not run_dir.exists()


def test_delete_run_delete_branch_true(tmp_path):
    import draft.runs as r

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    state = {"data": {"branch": "my-branch", "repo": str(repo_dir)}}
    run_dir = _make_run_dir(tmp_path, state=state)

    with patch("draft.runs.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        mock_run.return_value.stdout = ""
        result = r.delete_run(run_dir, delete_branch=True)

    assert result["branch_deleted"] is True
    branch_calls = [
        c
        for c in mock_run.call_args_list
        if c.args and c.args[0][:3] == ["git", "branch", "-D"]
    ]
    assert len(branch_calls) == 1


def test_delete_run_delete_branch_missing_branch(tmp_path):
    import draft.runs as r

    state = {"data": {}}
    run_dir = _make_run_dir(tmp_path, state=state)

    result = r.delete_run(run_dir, delete_branch=True)

    assert (
        "--delete-branch requested but branch or repo missing from state"
        in result["warnings"]
    )
    assert result["status"] == "deleted"


def test_delete_run_delete_branch_git_failure(tmp_path):
    import draft.runs as r

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    state = {"data": {"branch": "my-branch", "repo": str(repo_dir)}}
    run_dir = _make_run_dir(tmp_path, state=state)

    with patch("draft.runs.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "branch not found"
        mock_run.return_value.stdout = ""
        result = r.delete_run(run_dir, delete_branch=True)

    assert any("failed to delete branch" in w for w in result["warnings"])
    assert result["status"] == "deleted"
    assert not run_dir.exists()


# --- runs.is_run_finished ---


def test_is_run_finished_full_pipeline(tmp_path):
    import draft.runs as r

    state = {
        "completed": [
            "create-worktree",
            "implement-spec",
            "push-commits",
            "open-pr",
            "babysit-pr",
        ],
        "data": {"pipeline": "create"},
    }
    assert r.is_run_finished(state) is True


def test_is_run_finished_missing_pr_babysit(tmp_path):
    import draft.runs as r

    state = {
        "completed": ["create-worktree", "implement-spec", "push-commits", "open-pr"],
        "data": {"pipeline": "create"},
    }
    assert r.is_run_finished(state) is False


def test_is_run_finished_skip_pr_true(tmp_path):
    import draft.runs as r

    state = {
        "completed": ["create-worktree", "implement-spec"],
        "data": {"skip_pr": True, "pipeline": "create"},
    }
    assert r.is_run_finished(state) is True


def test_is_run_finished_skip_pr_false(tmp_path):
    import draft.runs as r

    state = {
        "completed": ["create-worktree", "implement-spec"],
        "data": {"skip_pr": False, "pipeline": "create"},
    }
    assert r.is_run_finished(state) is False


# --- command_prune ---


def _make_prune_args(**kwargs):
    class FakeArgs:
        yes = False
        dry_run = False
        include_all = False
        project = None
        all_projects = False
        delete_branch = False

    for k, v in kwargs.items():
        setattr(FakeArgs, k, v)
    return FakeArgs()


def _full_state(branch="draft/feat"):
    return {
        "completed": [
            "create-worktree",
            "implement-spec",
            "push-commits",
            "open-pr",
            "babysit-pr",
        ],
        "data": {"branch": branch, "pipeline": "create"},
    }


def _partial_state(branch="draft/feat"):
    return {
        "completed": ["create-worktree"],
        "data": {"branch": branch, "pipeline": "create"},
    }


def test_prune_no_runs(tmp_path, capsys):
    import draft.command_prune as cp

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[]),
    ):
        result = cp.run(_make_prune_args())

    assert result == 0
    assert "no runs to prune" in capsys.readouterr().out


def test_prune_one_finished_one_in_progress_default_selection(
    tmp_path, capsys, monkeypatch
):
    import draft.command_prune as cp

    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=_full_state())
    run_partial = _make_run_dir(
        tmp_path, run_id="260505-120000", state=_partial_state()
    )

    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[run_done, run_partial]),
    ):
        cp.run(_make_prune_args())

    captured = capsys.readouterr()
    assert "260505-130000" in captured.out
    assert "260505-120000" not in captured.out


def test_prune_confirmation_declined(tmp_path, capsys, monkeypatch):
    import draft.command_prune as cp

    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=_full_state())

    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[run_done]),
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value="n"),
    ):
        result = cp.run(_make_prune_args())

    assert result == 0
    assert "aborted" in capsys.readouterr().out
    assert run_done.exists()


def test_prune_yes_deletes_run(tmp_path, capsys):
    import draft.command_prune as cp

    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=_full_state())

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[run_done]),
    ):
        result = cp.run(_make_prune_args(yes=True))

    assert result == 0
    assert not run_done.exists()
    captured = capsys.readouterr()
    assert "deleted 1" in captured.out


def test_prune_dry_run_no_deletion(tmp_path, capsys):
    import draft.command_prune as cp

    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=_full_state())

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[run_done]),
    ):
        result = cp.run(_make_prune_args(dry_run=True, yes=True))

    assert result == 0
    assert run_done.exists()
    captured = capsys.readouterr()
    assert "would" in captured.out


def test_prune_all_includes_incomplete(tmp_path, capsys):
    import draft.command_prune as cp

    run_partial = _make_run_dir(
        tmp_path, run_id="260505-120000", state=_partial_state()
    )

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[run_partial]),
    ):
        result = cp.run(_make_prune_args(include_all=True, dry_run=True))

    assert result == 0
    captured = capsys.readouterr()
    assert "260505-120000" in captured.out


def test_prune_all_skips_active(tmp_path, capsys):
    import draft.command_prune as cp

    run_active = _make_run_dir(tmp_path, run_id="260505-120000", state=_partial_state())
    (run_active / "draft.pid").write_text(str(os.getpid()))

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[run_active]),
    ):
        result = cp.run(_make_prune_args(include_all=True, yes=True))

    assert result == 0
    assert run_active.exists()
    captured = capsys.readouterr()
    assert "skipped 1 active" in captured.out


def test_prune_project_not_found(tmp_path, capsys):
    import draft.command_prune as cp

    with patch("draft.runs.runs_base", return_value=tmp_path):
        result = cp.run(_make_prune_args(project="nonexistent"))

    assert result != 0
    assert "not found" in capsys.readouterr().err


def test_prune_project_and_all_projects_mutual_exclusion(tmp_path, capsys):
    import draft.command_prune as cp

    result = cp.run(_make_prune_args(project="foo", all_projects=True))
    assert result != 0
    assert "mutually exclusive" in capsys.readouterr().err


def test_prune_not_in_git_repo(tmp_path, capsys):
    import draft.command_prune as cp

    with patch("draft.runs.current_project_name", return_value=None):
        result = cp.run(_make_prune_args())

    assert result != 0
    assert "not in a git repo" in capsys.readouterr().err


def test_prune_all_projects_aggregates(tmp_path, capsys):
    import draft.command_prune as cp

    run_a = _make_run_dir(
        tmp_path, run_id="260505-130000", project="proj_a", state=_full_state()
    )
    run_b = _make_run_dir(
        tmp_path, run_id="260505-120000", project="proj_b", state=_full_state()
    )

    with (
        patch("draft.runs.all_project_names", return_value=["proj_a", "proj_b"]),
        patch(
            "draft.runs.project_runs",
            side_effect=lambda name: [run_a] if name == "proj_a" else [run_b],
        ),
    ):
        result = cp.run(_make_prune_args(all_projects=True, dry_run=True))

    assert result == 0
    captured = capsys.readouterr()
    assert "260505-130000" in captured.out
    assert "260505-120000" in captured.out


def test_prune_yes_delete_branch(tmp_path, capsys):
    import draft.command_prune as cp

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    state = {
        "completed": [
            "create-worktree",
            "implement-spec",
            "push-commits",
            "open-pr",
            "babysit-pr",
        ],
        "data": {"branch": "draft/feat", "repo": str(repo_dir)},
    }
    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=state)

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[run_done]),
        patch("draft.runs.subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        mock_run.return_value.stdout = ""
        result = cp.run(_make_prune_args(yes=True, delete_branch=True))

    assert result == 0
    branch_calls = [
        c
        for c in mock_run.call_args_list
        if c.args and c.args[0][:3] == ["git", "branch", "-D"]
    ]
    assert len(branch_calls) == 1


def test_prune_non_tty_stdin_without_yes(tmp_path, capsys, monkeypatch):
    import draft.command_prune as cp

    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=_full_state())

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[run_done]),
        patch("sys.stdin.isatty", return_value=False),
    ):
        result = cp.run(_make_prune_args())

    assert result != 0
    assert "non-interactively" in capsys.readouterr().err


def test_prune_skip_pr_finished_included(tmp_path, capsys):
    import draft.command_prune as cp

    state = {
        "completed": ["create-worktree", "implement-spec"],
        "data": {"branch": "draft/feat", "skip_pr": True},
    }
    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=state)

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[run_done]),
    ):
        result = cp.run(_make_prune_args(dry_run=True))

    assert result == 0
    captured = capsys.readouterr()
    assert "260505-130000" in captured.out


# --- create-modes: flag conflicts ---


def _make_create_args(**kwargs):
    class FakeArgs:
        spec_path = "spec.md"
        prompt = None
        overrides = []
        skip_pr = False
        from_branch = None
        branch = None
        no_worktree = False
        delete_worktree = False

    for k, v in kwargs.items():
        setattr(FakeArgs, k, v)
    return FakeArgs()


def test_reject_branch_and_from_together(capsys):
    import draft.command_create as cmd

    with pytest.raises(SystemExit) as exc:
        cmd._reject_flag_conflicts(_make_create_args(branch="foo", from_branch="main"))
    assert exc.value.code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_reject_delete_worktree_with_no_worktree(capsys):
    import draft.command_create as cmd

    with pytest.raises(SystemExit) as exc:
        cmd._reject_flag_conflicts(
            _make_create_args(delete_worktree=True, no_worktree=True)
        )
    assert exc.value.code == 2
    assert "--delete-worktree" in capsys.readouterr().err


def test_reject_flag_conflicts_no_op_when_clean():
    import draft.command_create as cmd

    cmd._reject_flag_conflicts(_make_create_args())  # must not raise


# --- create-modes: spec precheck ---


def test_assert_spec_readable_accepts_existing_file(tmp_path):
    import draft.command_create as cmd

    spec = tmp_path / "spec.md"
    spec.write_text("content")
    cmd._assert_spec_readable(str(spec))  # must not raise


def test_assert_spec_readable_missing_file(tmp_path, capsys):
    import draft.command_create as cmd

    with pytest.raises(SystemExit) as exc:
        cmd._assert_spec_readable(str(tmp_path / "missing.md"))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "spec file not found" in err
    assert str(tmp_path / "missing.md") in err


def test_assert_spec_readable_directory(tmp_path, capsys):
    import draft.command_create as cmd

    with pytest.raises(SystemExit) as exc:
        cmd._assert_spec_readable(str(tmp_path))
    assert exc.value.code == 2
    assert "not a regular file" in capsys.readouterr().err


def test_assert_spec_readable_broken_symlink(tmp_path, capsys):
    import draft.command_create as cmd

    link = tmp_path / "link"
    link.symlink_to(tmp_path / "ghost")
    with pytest.raises(SystemExit) as exc:
        cmd._assert_spec_readable(str(link))
    assert exc.value.code == 2
    assert "spec file not found" in capsys.readouterr().err


def test_assert_spec_readable_unreadable_file(tmp_path, capsys):
    import os

    import draft.command_create as cmd

    if os.geteuid() == 0:
        pytest.skip("root bypasses mode bits")
    spec = tmp_path / "spec.md"
    spec.write_text("content")
    os.chmod(spec, 0o000)
    try:
        with pytest.raises(SystemExit) as exc:
            cmd._assert_spec_readable(str(spec))
        assert exc.value.code == 2
        assert "cannot read spec file" in capsys.readouterr().err
    finally:
        os.chmod(spec, 0o600)


def test_assert_spec_readable_relative_path(tmp_path, monkeypatch, capsys):
    import draft.command_create as cmd

    spec = tmp_path / "spec.md"
    spec.write_text("content")
    monkeypatch.chdir(tmp_path)
    cmd._assert_spec_readable("spec.md")  # must not raise


def test_assert_spec_readable_tilde_expansion(tmp_path, monkeypatch):
    import draft.command_create as cmd

    monkeypatch.setenv("HOME", str(tmp_path))
    spec = tmp_path / "spec.md"
    spec.write_text("content")
    cmd._assert_spec_readable("~/spec.md")  # must not raise


def test_run_exits_before_creating_run_dir(tmp_path, monkeypatch, capsys):
    import draft.command_create as cmd

    monkeypatch.setenv("HOME", str(tmp_path))
    args = _make_create_args(spec_path=str(tmp_path / "missing.md"))
    with pytest.raises(SystemExit) as exc:
        cmd.run(args)
    assert exc.value.code == 2
    assert not (tmp_path / ".draft" / "runs").exists()


# --- create-modes: _compose_active_steps ---


def test_compose_active_steps_default():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps("worktree", "open", False)
    assert [s.name for s in active] == [
        "create-worktree",
        "implement-spec",
        "push-commits",
        "open-pr",
        "babysit-pr",
    ]
    assert skipped == {"delete-worktree", "review-implementation"}


def test_compose_active_steps_no_worktree():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps("no-worktree", "open", False)
    assert "create-worktree" not in [s.name for s in active]
    assert "create-worktree" in skipped


def test_compose_active_steps_skip_pr():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps("worktree", "skip", True)
    assert [s.name for s in active] == ["create-worktree", "implement-spec"]
    assert skipped == {
        "push-commits",
        "open-pr",
        "babysit-pr",
        "delete-worktree",
        "review-implementation",
    }


def test_compose_active_steps_pr_reuse_skips_pr_open():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps("worktree", "reuse", False)
    names = [s.name for s in active]
    assert "open-pr" not in names
    assert "babysit-pr" in names
    assert "open-pr" in skipped
    assert "delete-worktree" in skipped


# --- create-modes: runs.expected_steps with new keys ---


def test_expected_steps_no_pipeline_raises():
    import draft.runs as r
    from draft.pipelines import CorruptStateError

    state = {"completed": [], "data": {}}
    with pytest.raises(CorruptStateError):
        r.expected_steps(state)


def test_expected_steps_unknown_pipeline_raises():
    import draft.runs as r
    from draft.pipelines import CorruptStateError

    state = {"completed": [], "data": {"pipeline": "unknown"}}
    with pytest.raises(CorruptStateError):
        r.expected_steps(state)


def test_expected_steps_legacy_full():
    import draft.runs as r

    state = {"completed": [], "data": {"pipeline": "create"}}
    assert r.expected_steps(state) == r.FULL_PIPELINE_STEPS


def test_expected_steps_legacy_skip_pr():
    import draft.runs as r

    state = {"completed": [], "data": {"skip_pr": True, "pipeline": "create"}}
    assert r.expected_steps(state) == r.SKIP_PR_STEPS


def test_expected_steps_no_worktree_open():
    import draft.runs as r

    state = {
        "completed": [],
        "data": {
            "worktree_mode": "no-worktree",
            "pr_mode": "open",
            "pipeline": "create",
        },
    }
    assert r.expected_steps(state) == (
        "implement-spec",
        "push-commits",
        "open-pr",
        "babysit-pr",
    )


def test_expected_steps_pr_reuse():
    import draft.runs as r

    state = {
        "completed": [],
        "data": {"worktree_mode": "worktree", "pr_mode": "reuse", "pipeline": "create"},
    }
    assert r.expected_steps(state) == (
        "create-worktree",
        "implement-spec",
        "push-commits",
        "babysit-pr",
    )


def test_expected_steps_no_worktree_skip_pr():
    import draft.runs as r

    state = {
        "completed": [],
        "data": {"worktree_mode": "no-worktree", "skip_pr": True, "pipeline": "create"},
    }
    assert r.expected_steps(state) == ("implement-spec",)


# --- create-modes: runs.find_active_run_on_branch ---


def test_find_active_run_on_branch_none(tmp_path):
    import draft.runs as r

    with patch("draft.runs.runs_base", return_value=tmp_path / "nope"):
        assert r.find_active_run_on_branch("proj", "foo") is None


def test_find_active_run_on_branch_returns_unfinished(tmp_path):
    import draft.runs as r

    project_dir = tmp_path / "proj"
    run_dir = project_dir / "260506-100000"
    run_dir.mkdir(parents=True)
    state = {
        "completed": ["create-worktree"],
        "data": {"branch": "foo", "pipeline": "create"},
    }
    (run_dir / "state.json").write_text(json.dumps(state))

    with patch("draft.runs.runs_base", return_value=tmp_path):
        result = r.find_active_run_on_branch("proj", "foo")
    assert result == run_dir


def test_find_active_run_on_branch_skips_finished(tmp_path):
    import draft.runs as r

    project_dir = tmp_path / "proj"
    run_dir = project_dir / "260506-100000"
    run_dir.mkdir(parents=True)
    state = {
        "completed": list(r.FULL_PIPELINE_STEPS),
        "data": {"branch": "foo", "pipeline": "create"},
    }
    (run_dir / "state.json").write_text(json.dumps(state))

    with patch("draft.runs.runs_base", return_value=tmp_path):
        result = r.find_active_run_on_branch("proj", "foo")
    assert result is None


def test_find_active_run_on_branch_skips_other_branch(tmp_path):
    import draft.runs as r

    project_dir = tmp_path / "proj"
    run_dir = project_dir / "260506-100000"
    run_dir.mkdir(parents=True)
    state = {
        "completed": ["create-worktree"],
        "data": {"branch": "other", "pipeline": "create"},
    }
    (run_dir / "state.json").write_text(json.dumps(state))

    with patch("draft.runs.runs_base", return_value=tmp_path):
        result = r.find_active_run_on_branch("proj", "foo")
    assert result is None


# --- create-modes: command_continue drift and finished-run exit ---


def _continue_state(
    *,
    completed=None,
    branch="foo",
    wt_dir="/tmp/wt",
    repo="/tmp/repo",
    worktree_mode="worktree",
    delete_worktree=False,
    skip_pr=False,
    pr_mode="open",
    pipeline="create",
):
    return {
        "run_id": "260506-100000",
        "run_dir": "",
        "completed": completed or [],
        "data": {
            "branch": branch,
            "wt_dir": wt_dir,
            "repo": repo,
            "worktree_mode": worktree_mode,
            "delete_worktree": delete_worktree,
            "skip_pr": skip_pr,
            "pr_mode": pr_mode,
            "pipeline": pipeline,
        },
        "step_data": {},
        "step_configs": {},
        "started_at": "2026-05-06T00:00:00",
    }


def test_continue_finished_with_deleted_worktree_exits_clean(tmp_path, capsys):
    import draft.command_continue as cmd_continue
    import draft.runs as r

    project_dir = tmp_path / "proj"
    run_dir = project_dir / "260506-100000"
    run_dir.mkdir(parents=True)
    wt = tmp_path / "gone"  # does not exist

    state = _continue_state(
        completed=list(r.FULL_PIPELINE_STEPS) + ["delete-worktree"],
        wt_dir=str(wt),
        delete_worktree=True,
    )
    state["run_dir"] = str(run_dir)
    (run_dir / "state.json").write_text(json.dumps(state))

    class FakeArgs:
        run_id = "260506-100000"

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cmd_continue.run(FakeArgs())

    assert result == 0
    out = capsys.readouterr().out
    assert "already complete" in out


def test_continue_drift_no_worktree_refuses(tmp_path, capsys):
    import draft.command_continue as cmd_continue

    project_dir = tmp_path / "proj"
    run_dir = project_dir / "260506-100000"
    run_dir.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    state = _continue_state(
        completed=["implement-spec"],
        wt_dir=str(repo),
        repo=str(repo),
        worktree_mode="no-worktree",
        branch="foo",
    )
    state["run_dir"] = str(run_dir)
    (run_dir / "state.json").write_text(json.dumps(state))

    class FakeArgs:
        run_id = "260506-100000"

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.command_continue._branch_at", return_value="other-branch"),
    ):
        result = cmd_continue.run(FakeArgs())

    assert result == 2
    assert "drift" in capsys.readouterr().err


def test_continue_drift_worktree_refuses(tmp_path, capsys):
    import draft.command_continue as cmd_continue

    project_dir = tmp_path / "proj"
    run_dir = project_dir / "260506-100000"
    run_dir.mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()

    state = _continue_state(
        completed=["create-worktree", "implement-spec"],
        wt_dir=str(wt),
        branch="foo",
    )
    state["run_dir"] = str(run_dir)
    (run_dir / "state.json").write_text(json.dumps(state))

    class FakeArgs:
        run_id = "260506-100000"

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.command_continue._branch_at", return_value="other-branch"),
    ):
        result = cmd_continue.run(FakeArgs())

    assert result == 2
    assert "drift" in capsys.readouterr().err


# --- create-modes: WorktreeCreateStep existing-branch mode ---


def test_worktree_create_existing_branch_uses_no_dash_b(tmp_path):
    from draft.steps.create_worktree import CreateWorktreeStep
    from pipeline import RunContext

    ctx = RunContext("rid", tmp_path, {"create-worktree": {"timeout": 60}})
    ctx.set("branch", "foo")
    ctx.set("base_branch", "origin/main")
    ctx.set("wt_dir", "/tmp/wt")
    ctx.set("branch_source", "existing")

    cmd = CreateWorktreeStep().cmd(ctx)
    assert cmd == ["git", "worktree", "add", "/tmp/wt", "foo"]
    assert "-b" not in cmd


def test_worktree_create_new_branch_uses_dash_b(tmp_path):
    from draft.steps.create_worktree import CreateWorktreeStep
    from pipeline import RunContext

    ctx = RunContext("rid", tmp_path, {"create-worktree": {"timeout": 60}})
    ctx.set("branch", "foo")
    ctx.set("base_branch", "origin/main")
    ctx.set("wt_dir", "/tmp/wt")
    ctx.set("branch_source", "new")

    cmd = CreateWorktreeStep().cmd(ctx)
    assert cmd == ["git", "worktree", "add", "/tmp/wt", "-b", "foo", "origin/main"]


# --- reuse-worktree: _resolve_worktree_for_existing_branch ---


def _canonical(project: str, branch: str) -> str:
    return str(
        Path.home() / ".draft" / "worktrees" / project / branch.replace("/", "-")
    )


def test_resolve_worktree_no_existing_returns_create():
    import draft.command_create as cmd

    with patch("draft.command_create._branch_worktrees", return_value=[]):
        wt_dir, mode = cmd._resolve_worktree_for_existing_branch(
            "/repo", "proj", "feature-x", branch_was_explicit=True
        )

    assert mode == "worktree"
    assert wt_dir == _canonical("proj", "feature-x")


def test_resolve_worktree_canonical_clean_reuses(tmp_path):
    import draft.command_create as cmd

    canonical = tmp_path / "wt"
    canonical.mkdir()

    with (
        patch("draft.command_create._canonical_worktree_path", return_value=canonical),
        patch("draft.command_create._branch_worktrees", return_value=[str(canonical)]),
        patch("draft.command_create._current_head_branch", return_value="feature-x"),
        patch("draft.command_create._is_working_tree_clean", return_value=True),
    ):
        wt_dir, mode = cmd._resolve_worktree_for_existing_branch(
            "/repo", "proj", "feature-x", branch_was_explicit=True
        )

    assert mode == "reuse-existing"
    assert wt_dir == str(canonical)


def test_resolve_worktree_no_value_form_refuses(capsys):
    import draft.command_create as cmd

    with (
        patch("draft.command_create._branch_worktrees", return_value=["/some/path"]),
        pytest.raises(SystemExit) as exc,
    ):
        cmd._resolve_worktree_for_existing_branch(
            "/repo", "proj", "feature-x", branch_was_explicit=False
        )

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "(current HEAD)" in err
    assert "--branch feature-x" in err


def test_resolve_worktree_non_canonical_path_refuses(tmp_path, capsys):
    import draft.command_create as cmd

    canonical = tmp_path / "canonical"
    other = tmp_path / "other"
    other.mkdir()

    with (
        patch("draft.command_create._canonical_worktree_path", return_value=canonical),
        patch("draft.command_create._branch_worktrees", return_value=[str(other)]),
        pytest.raises(SystemExit) as exc,
    ):
        cmd._resolve_worktree_for_existing_branch(
            "/repo", "proj", "feature-x", branch_was_explicit=True
        )

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "non-canonical" in err
    assert str(other) in err


def test_resolve_worktree_multiple_paths_refuses(tmp_path, capsys):
    import draft.command_create as cmd

    canonical = tmp_path / "canonical"
    canonical.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()

    with (
        patch("draft.command_create._canonical_worktree_path", return_value=canonical),
        patch(
            "draft.command_create._branch_worktrees",
            return_value=[str(canonical), str(extra)],
        ),
        pytest.raises(SystemExit) as exc,
    ):
        cmd._resolve_worktree_for_existing_branch(
            "/repo", "proj", "feature-x", branch_was_explicit=True
        )

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "non-canonical" in err
    assert str(extra) in err


def test_resolve_worktree_stale_registration_refuses(tmp_path, capsys):
    import draft.command_create as cmd

    canonical = tmp_path / "missing"  # does not exist on disk

    with (
        patch("draft.command_create._canonical_worktree_path", return_value=canonical),
        patch("draft.command_create._branch_worktrees", return_value=[str(canonical)]),
        pytest.raises(SystemExit) as exc,
    ):
        cmd._resolve_worktree_for_existing_branch(
            "/repo", "proj", "feature-x", branch_was_explicit=True
        )

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "stale worktree registration" in err
    assert "git worktree prune" in err


def test_resolve_worktree_detached_head_refuses(tmp_path, capsys):
    import draft.command_create as cmd

    canonical = tmp_path / "wt"
    canonical.mkdir()

    with (
        patch("draft.command_create._canonical_worktree_path", return_value=canonical),
        patch("draft.command_create._branch_worktrees", return_value=[str(canonical)]),
        patch("draft.command_create._current_head_branch", return_value=None),
        pytest.raises(SystemExit) as exc,
    ):
        cmd._resolve_worktree_for_existing_branch(
            "/repo", "proj", "feature-x", branch_was_explicit=True
        )

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "detached HEAD" in err


def test_resolve_worktree_wrong_branch_refuses(tmp_path, capsys):
    import draft.command_create as cmd

    canonical = tmp_path / "wt"
    canonical.mkdir()

    with (
        patch("draft.command_create._canonical_worktree_path", return_value=canonical),
        patch("draft.command_create._branch_worktrees", return_value=[str(canonical)]),
        patch("draft.command_create._current_head_branch", return_value="other-branch"),
        pytest.raises(SystemExit) as exc,
    ):
        cmd._resolve_worktree_for_existing_branch(
            "/repo", "proj", "feature-x", branch_was_explicit=True
        )

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "other-branch" in err
    assert "feature-x" in err


def test_resolve_worktree_dirty_refuses(tmp_path, capsys):
    import draft.command_create as cmd

    canonical = tmp_path / "wt"
    canonical.mkdir()

    with (
        patch("draft.command_create._canonical_worktree_path", return_value=canonical),
        patch("draft.command_create._branch_worktrees", return_value=[str(canonical)]),
        patch("draft.command_create._current_head_branch", return_value="feature-x"),
        patch("draft.command_create._is_working_tree_clean", return_value=False),
        pytest.raises(SystemExit) as exc,
    ):
        cmd._resolve_worktree_for_existing_branch(
            "/repo", "proj", "feature-x", branch_was_explicit=True
        )

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "dirty" in err
    assert "git -C" in err


# --- reuse-worktree: _compose_active_steps and runs.expected_steps ---


def test_compose_active_steps_reuse_existing_skips_worktree_create():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps("reuse-existing", "open", False)
    names = [s.name for s in active]
    assert "create-worktree" not in names
    assert names == ["implement-spec", "push-commits", "open-pr", "babysit-pr"]
    assert "create-worktree" in skipped
    assert "delete-worktree" in skipped


def test_compose_active_steps_reuse_existing_with_pr_reuse_drops_both():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps("reuse-existing", "reuse", False)
    names = [s.name for s in active]
    assert names == ["implement-spec", "push-commits", "babysit-pr"]
    assert skipped == {
        "create-worktree",
        "open-pr",
        "delete-worktree",
        "review-implementation",
    }


def test_compose_active_steps_reuse_existing_with_skip_pr():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps("reuse-existing", "skip", True)
    names = [s.name for s in active]
    assert names == ["implement-spec"]
    assert skipped == {
        "create-worktree",
        "push-commits",
        "open-pr",
        "babysit-pr",
        "delete-worktree",
        "review-implementation",
    }


def test_expected_steps_reuse_existing_open():
    import draft.runs as r

    state = {
        "completed": [],
        "data": {
            "worktree_mode": "reuse-existing",
            "pr_mode": "open",
            "pipeline": "create",
        },
    }
    assert r.expected_steps(state) == (
        "implement-spec",
        "push-commits",
        "open-pr",
        "babysit-pr",
    )


def test_expected_steps_reuse_existing_pr_reuse():
    import draft.runs as r

    state = {
        "completed": [],
        "data": {
            "worktree_mode": "reuse-existing",
            "pr_mode": "reuse",
            "pipeline": "create",
        },
    }
    assert r.expected_steps(state) == ("implement-spec", "push-commits", "babysit-pr")


def test_is_run_finished_reuse_existing():
    import draft.runs as r

    state = {
        "completed": ["implement-spec", "push-commits", "open-pr", "babysit-pr"],
        "data": {
            "worktree_mode": "reuse-existing",
            "pr_mode": "open",
            "pipeline": "create",
        },
    }
    assert r.is_run_finished(state) is True


# --- reuse-worktree: command_continue with reuse-existing ---


def test_continue_reuse_finished_with_deleted_worktree_exits_clean(tmp_path, capsys):
    import draft.command_continue as cmd_continue

    project_dir = tmp_path / "proj"
    run_dir = project_dir / "260506-100000"
    run_dir.mkdir(parents=True)
    wt = tmp_path / "gone"  # does not exist

    state = _continue_state(
        completed=[
            "implement-spec",
            "push-commits",
            "open-pr",
            "babysit-pr",
            "delete-worktree",
        ],
        wt_dir=str(wt),
        delete_worktree=True,
        worktree_mode="reuse-existing",
    )
    state["run_dir"] = str(run_dir)
    (run_dir / "state.json").write_text(json.dumps(state))

    class FakeArgs:
        run_id = "260506-100000"

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cmd_continue.run(FakeArgs())

    assert result == 0
    out = capsys.readouterr().out
    assert "already complete" in out


# --- reuse-worktree: _print_preamble annotations ---


def test_preamble_reused_annotation_for_reuse_existing(capsys):
    import draft.command_create as cmd
    from draft.pipelines import PIPELINES

    skipped = {"create-worktree"}
    cmd._print_preamble(
        "rid",
        "feature-x",
        "/wt",
        "/runs/rid",
        "started",
        PIPELINES["create"].steps,
        skipped,
        "reuse-existing",
    )
    out = capsys.readouterr().out
    assert "create-worktree [skipped, reused]" in out


def test_preamble_skipped_no_reuse_for_no_worktree(capsys):
    import draft.command_create as cmd
    from draft.pipelines import PIPELINES

    skipped = {"create-worktree"}
    cmd._print_preamble(
        "rid",
        "feature-x",
        "/repo",
        "/runs/rid",
        "started",
        PIPELINES["create"].steps,
        skipped,
        "no-worktree",
    )
    out = capsys.readouterr().out
    assert "create-worktree [skipped]" in out
    assert "reused" not in out


def test_preamble_no_skipped_annotation_for_active_step(capsys):
    import draft.command_create as cmd
    from draft.pipelines import PIPELINES

    cmd._print_preamble(
        "rid",
        "feature-x",
        "/wt",
        "/runs/rid",
        "started",
        PIPELINES["create"].steps,
        set(),
        "worktree",
    )
    out = capsys.readouterr().out
    assert "create-worktree\n" in out  # no suffix
    assert "[skipped" not in out


# --- delete-worktree: _compose_active_steps ---


def test_compose_active_steps_delete_worktree_included_for_worktree_mode():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps(
        "worktree", "open", False, delete_worktree=True
    )
    names = [s.name for s in active]
    assert names[-1] == "delete-worktree"
    assert "delete-worktree" not in skipped


def test_compose_active_steps_delete_worktree_included_for_reuse_existing():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps(
        "reuse-existing", "open", False, delete_worktree=True
    )
    names = [s.name for s in active]
    assert names[-1] == "delete-worktree"
    assert "delete-worktree" not in skipped


def test_compose_active_steps_delete_worktree_skipped_when_false():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps(
        "worktree", "open", False, delete_worktree=False
    )
    names = [s.name for s in active]
    assert "delete-worktree" not in names
    assert "delete-worktree" in skipped


def test_compose_active_steps_delete_worktree_skipped_for_no_worktree():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps(
        "no-worktree", "open", False, delete_worktree=True
    )
    names = [s.name for s in active]
    assert "delete-worktree" not in names
    assert "delete-worktree" in skipped


def test_compose_active_steps_skip_pr_with_delete_worktree():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps(
        "worktree", "skip", True, delete_worktree=True
    )
    names = [s.name for s in active]
    assert names == ["create-worktree", "implement-spec", "delete-worktree"]
    assert "delete-worktree" not in skipped


# --- delete-worktree: runs.expected_steps ---


def test_expected_steps_delete_worktree_appended():
    import draft.runs as r

    state = {"completed": [], "data": {"delete_worktree": True, "pipeline": "create"}}
    result = r.expected_steps(state)
    assert result[-1] == "delete-worktree"


def test_expected_steps_delete_worktree_skipped_when_false():
    import draft.runs as r

    state = {"completed": [], "data": {"delete_worktree": False, "pipeline": "create"}}
    assert "delete-worktree" not in r.expected_steps(state)


def test_expected_steps_delete_worktree_skipped_for_no_worktree():
    import draft.runs as r

    state = {
        "completed": [],
        "data": {
            "delete_worktree": True,
            "worktree_mode": "no-worktree",
            "skip_pr": True,
            "pipeline": "create",
        },
    }
    assert "delete-worktree" not in r.expected_steps(state)


def test_expected_steps_delete_worktree_included_for_reuse_existing():
    import draft.runs as r

    state = {
        "completed": [],
        "data": {
            "delete_worktree": True,
            "worktree_mode": "reuse-existing",
            "pr_mode": "open",
            "pipeline": "create",
        },
    }
    result = r.expected_steps(state)
    assert result[-1] == "delete-worktree"


def test_expected_steps_no_pipeline_no_delete_worktree():
    import draft.runs as r
    from draft.pipelines import CorruptStateError

    state = {"completed": [], "data": {}}
    with pytest.raises(CorruptStateError):
        r.expected_steps(state)


# --- delete-worktree: command_continue ---


def test_continue_only_delete_worktree_pending_worktree_absent_exits_clean(
    tmp_path, capsys
):
    import draft.command_continue as cmd_continue
    import draft.runs as r

    project_dir = tmp_path / "proj"
    run_dir = project_dir / "260506-100000"
    run_dir.mkdir(parents=True)
    wt = tmp_path / "gone"  # does not exist

    state = _continue_state(
        completed=list(r.FULL_PIPELINE_STEPS),
        wt_dir=str(wt),
        delete_worktree=True,
    )
    state["run_dir"] = str(run_dir)
    (run_dir / "state.json").write_text(json.dumps(state))

    class FakeArgs:
        run_id = "260506-100000"

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cmd_continue.run(FakeArgs())

    assert result == 0
    out = capsys.readouterr().out
    assert "already complete" in out


# --- delete-worktree: DeleteWorktreeStep behavior ---


def test_delete_worktree_step_path_missing_succeeds(tmp_path):
    from draft.steps.delete_worktree import DeleteWorktreeStep
    from pipeline import RunContext, Runner

    ctx = RunContext("rid", tmp_path, {"delete-worktree": {"timeout": 60}})
    ctx.set("wt_dir", str(tmp_path / "nonexistent"))

    DeleteWorktreeStep().run(
        ctx,
        Runner(),
        None,
        ctx.metrics.session_begin("test").step_begin("delete-worktree"),
    )  # must not raise


def test_delete_worktree_step_empty_wt_dir_raises(tmp_path):
    from draft.steps.delete_worktree import DeleteWorktreeStep
    from pipeline import RunContext, Runner, StepError

    ctx = RunContext("rid", tmp_path, {"delete-worktree": {"timeout": 60}})
    ctx.set("wt_dir", "")

    with pytest.raises(StepError):
        DeleteWorktreeStep().run(
            ctx,
            Runner(),
            None,
            ctx.metrics.session_begin("test").step_begin("delete-worktree"),
        )


def test_delete_worktree_step_git_success(tmp_path):
    from draft.steps.delete_worktree import DeleteWorktreeStep
    from pipeline import RunContext, Runner

    wt = tmp_path / "wt"
    wt.mkdir()
    ctx = RunContext("rid", tmp_path, {"delete-worktree": {"timeout": 60}})
    ctx.set("wt_dir", str(wt))

    with patch("draft.steps.delete_worktree.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        mock_run.return_value.stdout = ""
        DeleteWorktreeStep().run(
            ctx,
            Runner(),
            None,
            ctx.metrics.session_begin("test").step_begin("delete-worktree"),
        )

    mock_run.assert_called_once_with(
        ["git", "worktree", "remove", str(wt), "--force"],
        capture_output=True,
        text=True,
    )


def test_delete_worktree_step_git_nonzero_idempotent_signature_succeeds(tmp_path):
    from draft.steps.delete_worktree import DeleteWorktreeStep
    from pipeline import RunContext, Runner

    wt = tmp_path / "wt"
    wt.mkdir()
    ctx = RunContext("rid", tmp_path, {"delete-worktree": {"timeout": 60}})
    ctx.set("wt_dir", str(wt))

    with patch("draft.steps.delete_worktree.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 128
        mock_run.return_value.stderr = "fatal: 'wt' is not a working tree"
        mock_run.return_value.stdout = ""
        DeleteWorktreeStep().run(
            ctx,
            Runner(),
            None,
            ctx.metrics.session_begin("test").step_begin("delete-worktree"),
        )  # must not raise


def test_delete_worktree_step_git_nonzero_unknown_error_raises(tmp_path):
    from draft.steps.delete_worktree import DeleteWorktreeStep
    from pipeline import RunContext, Runner, StepError

    wt = tmp_path / "wt"
    wt.mkdir()
    ctx = RunContext("rid", tmp_path, {"delete-worktree": {"timeout": 60}})
    ctx.set("wt_dir", str(wt))

    with patch("draft.steps.delete_worktree.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "fatal: internal error"
        mock_run.return_value.stdout = ""
        with pytest.raises(StepError):
            DeleteWorktreeStep().run(
                ctx,
                Runner(),
                None,
                ctx.metrics.session_begin("test").step_begin("delete-worktree"),
            )


# --- command_list --json ---


def _make_list_args(**kwargs):
    class FakeArgs:
        json = False

    for k, v in kwargs.items():
        setattr(FakeArgs, k, v)
    return FakeArgs()


def test_command_list_json_no_runs_dir(tmp_path, capsys):
    import draft.command_list as clm

    with patch("draft.command_list.runs_base", return_value=tmp_path / "nonexistent"):
        result = clm.run(_make_list_args(json=True))
    assert result == 0
    assert json.loads(capsys.readouterr().out) == []


def test_command_list_json_empty_runs(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    base.mkdir()
    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(json=True))
    assert result == 0
    assert json.loads(capsys.readouterr().out) == []


def test_command_list_json_valid_row(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    wt = tmp_path / "my-worktree"
    wt.mkdir()
    state = {
        "completed": ["worktree-create", "code-spec"],
        "data": {
            "worktree_mode": "worktree",
            "pr_mode": "open",
            "skip_pr": False,
            "branch": "feat/foo",
            "wt_dir": str(wt),
            "pr_url": "https://github.com/org/repo/pull/1",
            "pipeline": "create",
        },
    }
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(json=True))

    assert result == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    row = rows[0]
    assert row["state"] == "ok"
    assert row["run_id"] == "260508-100000"
    assert row["project"] == "myproject"
    assert row["branch"] == "feat/foo"
    assert row["pr_url"] == "https://github.com/org/repo/pull/1"
    assert row["workspace"] == "yes"
    assert isinstance(row["running"], bool)


def test_command_list_json_missing_state(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(json=True))

    assert result == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["state"] == "missing"
    assert rows[0]["stages_completed"] is None
    assert rows[0]["stages_total"] is None
    assert rows[0]["workspace"] is None
    assert rows[0]["branch"] is None
    assert rows[0]["pr_url"] is None


def test_command_list_json_corrupt_state(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("not json{{{")

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(json=True))

    assert result == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["state"] == "corrupt"
    assert rows[0]["stages_completed"] is None


def test_command_list_json_workspace_yes(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    wt = tmp_path / "wt"
    wt.mkdir()
    state = {"completed": [], "data": {"wt_dir": str(wt), "pipeline": "create"}}
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(json=True))

    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["workspace"] == "yes"


def test_command_list_json_workspace_no(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": [],
        "data": {"wt_dir": str(tmp_path / "gone"), "pipeline": "create"},
    }
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(json=True))

    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["workspace"] == "no"


def test_command_list_json_workspace_null_when_absent(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {}}
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(json=True))

    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["workspace"] is None


def test_command_list_json_pr_url_null_when_absent(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {}}
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(json=True))

    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["pr_url"] is None


def test_command_list_json_running_true(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({"completed": [], "data": {}}))
    (run_dir / "draft.pid").write_text(str(os.getpid()))

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(json=True))

    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["running"] is True


def test_command_list_json_running_false(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {}}
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(json=True))

    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["running"] is False


def test_command_list_no_json_unchanged(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"branch": "feat"}}
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(object())

    assert result == 0
    out = capsys.readouterr().out
    assert "RUN-ID" in out
    assert "260508-100000" in out


# --- command_status ---


def _make_status_args(run_id, *, use_json=False):
    class FakeArgs:
        pass

    FakeArgs.run_id = run_id
    FakeArgs.json = use_json
    return FakeArgs()


def test_status_run_not_found(capsys):
    import draft.command_status as cs

    with patch("draft.runs.find_run_dir", return_value=None):
        result = cs.run(_make_status_args("260508-notfound"))

    assert result == 1
    assert "error: run '260508-notfound' not found" in capsys.readouterr().err


def test_status_state_absent(tmp_path, capsys):
    import draft.command_status as cs

    run_dir = tmp_path / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cs.run(_make_status_args("260508-100000"))

    assert result == 0
    out = capsys.readouterr().out
    assert "run-id" in out
    assert "260508-100000" in out
    assert "myproject" in out
    assert "unknown" in out
    assert "STEP" not in out


def test_status_state_corrupt(tmp_path, capsys):
    import draft.command_status as cs

    run_dir = tmp_path / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("not json{{")

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cs.run(_make_status_args("260508-100000"))

    assert result == 1
    assert "corrupt" in capsys.readouterr().err


def _make_status_run(tmp_path, run_id="260508-100000", state=None):
    run_dir = tmp_path / "myproject" / run_id
    run_dir.mkdir(parents=True)
    if state is not None:
        (run_dir / "state.json").write_text(json.dumps(state))
    return run_dir


def test_status_done_all_steps_show_done(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": [
            "create-worktree",
            "implement-spec",
            "push-commits",
            "open-pr",
            "babysit-pr",
        ],
        "data": {"branch": "main", "wt_dir": None, "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        result = cs.run(_make_status_args("260508-100000"))

    assert result == 0
    out = capsys.readouterr().out
    assert "status:   done" in out
    assert out.count("done") >= 6


def test_status_running_partial_shows_active(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": ["create-worktree", "implement-spec"],
        "data": {"branch": "feat", "wt_dir": "/some/wt", "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=True),
    ):
        result = cs.run(_make_status_args("260508-100000"))

    assert result == 0
    out = capsys.readouterr().out
    assert "status:   running" in out
    lines = [line for line in out.splitlines() if line.strip()]
    step_lines = [
        line
        for line in lines
        if any(
            s in line
            for s in [
                "create-worktree",
                "implement-spec",
                "push-commits",
                "open-pr",
                "babysit-pr",
            ]
        )
    ]
    statuses = [line.split()[1] for line in step_lines]
    assert statuses[:2] == ["done", "done"]
    assert statuses[2] == "active"
    assert all(s == "pending" for s in statuses[3:])


def test_status_stopped_partial_shows_stopped(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": ["create-worktree", "implement-spec"],
        "data": {"branch": "feat", "wt_dir": "/some/wt", "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        result = cs.run(_make_status_args("260508-100000"))

    assert result == 0
    out = capsys.readouterr().out
    assert "status:   stopped" in out
    lines = [line for line in out.splitlines() if line.strip()]
    step_lines = [
        line
        for line in lines
        if any(
            s in line
            for s in [
                "create-worktree",
                "implement-spec",
                "push-commits",
                "open-pr",
                "babysit-pr",
            ]
        )
    ]
    statuses = [line.split()[1] for line in step_lines]
    assert statuses[:2] == ["done", "done"]
    assert statuses[2] == "stopped"
    assert all(s == "pending" for s in statuses[3:])


def test_status_pr_url_printed_when_present(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": [
            "create-worktree",
            "implement-spec",
            "push-commits",
            "open-pr",
            "babysit-pr",
        ],
        "data": {
            "branch": "feat",
            "pr_url": "https://github.com/org/repo/pull/42",
            "pipeline": "create",
        },
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000"))

    out = capsys.readouterr().out
    assert "pr:" in out
    assert "https://github.com/org/repo/pull/42" in out


def test_status_pr_url_absent_not_printed(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": [
            "create-worktree",
            "implement-spec",
            "push-commits",
            "open-pr",
            "babysit-pr",
        ],
        "data": {"branch": "feat", "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000"))

    out = capsys.readouterr().out
    assert "pr:" not in out


def test_status_wt_dir_absent_shows_dash(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": [],
        "data": {"branch": "feat", "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000"))

    out = capsys.readouterr().out
    assert "worktree: -" in out


def test_status_skipped_steps_excluded_from_table(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": ["implement-spec"],
        "data": {
            "branch": "feat",
            "worktree_mode": "no-worktree",
            "skip_pr": True,
            "pipeline": "create",
        },
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000"))

    out = capsys.readouterr().out
    assert "create-worktree" not in out
    assert "implement-spec" in out


def test_status_no_pid_steps_complete_is_done(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": ["create-worktree", "implement-spec"],
        "data": {"branch": "feat", "skip_pr": True, "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cs.run(_make_status_args("260508-100000"))

    assert result == 0
    out = capsys.readouterr().out
    assert "status:   done" in out


def test_status_no_pid_steps_incomplete_is_stopped(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": ["create-worktree"],
        "data": {"branch": "feat", "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cs.run(_make_status_args("260508-100000"))

    assert result == 0
    out = capsys.readouterr().out
    assert "status:   stopped" in out


# --- command_status --json ---


def test_status_json_run_not_found(capsys):
    import draft.command_status as cs

    with patch("draft.runs.find_run_dir", return_value=None):
        result = cs.run(_make_status_args("260508-notfound", use_json=True))

    assert result == 1
    out = capsys.readouterr().out
    assert out == ""


def test_status_json_state_absent(tmp_path, capsys):
    import draft.command_status as cs

    run_dir = tmp_path / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cs.run(_make_status_args("260508-100000", use_json=True))

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "unknown"
    assert data["run_id"] == "260508-100000"
    assert data["project"] == "myproject"
    assert data["branch"] is None
    assert data["worktree"] is None
    assert data["pr_url"] is None
    assert data["steps"] is None


def test_status_json_corrupt_no_json(tmp_path, capsys):
    import draft.command_status as cs

    run_dir = tmp_path / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("not json{{")

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cs.run(_make_status_args("260508-100000", use_json=True))

    assert result == 1
    assert capsys.readouterr().out == ""


def test_status_json_done_all_steps(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": [
            "create-worktree",
            "implement-spec",
            "push-commits",
            "open-pr",
            "babysit-pr",
        ],
        "data": {"branch": "main", "wt_dir": None, "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        result = cs.run(_make_status_args("260508-100000", use_json=True))

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "done"
    assert all(s["status"] == "done" for s in data["steps"])


def test_status_json_running_partial(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": ["create-worktree", "implement-spec"],
        "data": {"branch": "feat", "wt_dir": "/some/wt", "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=True),
    ):
        result = cs.run(_make_status_args("260508-100000", use_json=True))

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "running"
    step_statuses = {s["name"]: s["status"] for s in data["steps"]}
    assert step_statuses["create-worktree"] == "done"
    assert step_statuses["implement-spec"] == "done"
    assert step_statuses["push-commits"] == "active"


def test_status_json_stopped_partial(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": ["create-worktree", "implement-spec"],
        "data": {"branch": "feat", "wt_dir": "/some/wt", "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        result = cs.run(_make_status_args("260508-100000", use_json=True))

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "stopped"
    step_statuses = {s["name"]: s["status"] for s in data["steps"]}
    assert step_statuses["push-commits"] == "stopped"


def test_status_json_pr_url_null_when_absent(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": [],
        "data": {"branch": "feat", "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000", use_json=True))

    data = json.loads(capsys.readouterr().out)
    assert data["pr_url"] is None


def test_status_json_pr_url_present(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": [
            "create-worktree",
            "implement-spec",
            "push-commits",
            "open-pr",
            "babysit-pr",
        ],
        "data": {
            "branch": "feat",
            "pr_url": "https://github.com/org/repo/pull/42",
            "pipeline": "create",
        },
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000", use_json=True))

    data = json.loads(capsys.readouterr().out)
    assert data["pr_url"] == "https://github.com/org/repo/pull/42"


def test_status_json_worktree_null_when_absent(tmp_path, capsys):
    import draft.command_status as cs

    state = {"completed": [], "data": {"branch": "feat", "pipeline": "create"}}
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000", use_json=True))

    data = json.loads(capsys.readouterr().out)
    assert data["worktree"] is None


def test_status_json_skipped_steps_absent_from_steps_array(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": ["implement-spec"],
        "data": {
            "branch": "feat",
            "worktree_mode": "no-worktree",
            "skip_pr": True,
            "pipeline": "create",
        },
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000", use_json=True))

    data = json.loads(capsys.readouterr().out)
    step_names = [s["name"] for s in data["steps"]]
    assert "create-worktree" not in step_names
    assert "implement-spec" in step_names


def test_status_json_no_json_unchanged(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": [],
        "data": {"branch": "feat", "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        result = cs.run(_make_status_args("260508-100000"))

    assert result == 0
    out = capsys.readouterr().out
    assert "run-id" in out
    assert "status" in out


def test_status_text_includes_logs_started_finished_runtime(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": [],
        "data": {"branch": "feat", "pipeline": "create"},
        "sessions": [
            {
                "started_at": "2025-01-01 10:00:00 UTC",
                "finished_at": "2025-01-01 10:01:00 UTC",
                "exit_code": 0,
                "steps": [],
            }
        ],
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000"))

    out = capsys.readouterr().out
    assert f"logs:          {run_dir}" in out
    assert "started:       2025-01-01 10:00:00 UTC" in out
    assert "finished:      2025-01-01 10:01:00 UTC" in out
    assert "total runtime: 1m00s" in out


def test_status_text_empty_sessions_shows_dashes_and_zero(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": [],
        "data": {"branch": "feat", "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000"))

    out = capsys.readouterr().out
    assert "started:       -" in out
    assert "finished:      -" in out
    assert "total runtime: 0s" in out


def test_status_text_unclosed_trailing_session_uses_heartbeat_for_runtime(
    tmp_path, capsys
):
    import draft.command_status as cs

    state = {
        "completed": [],
        "data": {"branch": "feat", "pipeline": "create"},
        "sessions": [
            {
                "started_at": "2025-01-01 10:00:00 UTC",
                "finished_at": None,
                "exit_code": None,
                "steps": [],
            }
        ],
    }
    run_dir = _make_status_run(tmp_path, state=state)
    (run_dir / "heartbeat").write_text("2025-01-01 10:01:30 UTC")

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000"))

    out = capsys.readouterr().out
    assert "finished:      -" in out
    assert "total runtime: 1m30s" in out


def test_status_json_includes_logs_started_finished_runtime(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": [],
        "data": {"branch": "feat", "pipeline": "create"},
        "sessions": [
            {
                "started_at": "2025-01-01 10:00:00 UTC",
                "finished_at": "2025-01-01 10:00:45 UTC",
                "exit_code": 0,
                "steps": [],
            }
        ],
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000", use_json=True))

    data = json.loads(capsys.readouterr().out)
    assert data["logs"] == str(run_dir)
    assert data["started_at"] == "2025-01-01 10:00:00 UTC"
    assert data["finished_at"] == "2025-01-01 10:00:45 UTC"
    assert data["total_runtime_seconds"] == 45.0


def test_status_json_empty_sessions_emits_nulls_and_zero(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": [],
        "data": {"branch": "feat", "pipeline": "create"},
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000", use_json=True))

    data = json.loads(capsys.readouterr().out)
    assert data["started_at"] is None
    assert data["finished_at"] is None
    assert data["total_runtime_seconds"] == 0.0


def test_status_state_absent_does_not_add_new_keys(tmp_path, capsys):
    import draft.command_status as cs

    run_dir = tmp_path / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        result = cs.run(_make_status_args("260508-100000", use_json=True))

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert "logs" not in data
    assert "started_at" not in data
    assert "finished_at" not in data
    assert "total_runtime_seconds" not in data
    assert "total_llm_cost_usd" not in data


def _make_status_state_with_cost(cost):
    return {
        "completed": ["create-worktree"],
        "data": {"branch": "feat", "pipeline": "create"},
        "sessions": [
            {
                "command": "create",
                "started_at": "2025-01-01 10:00:00 UTC",
                "finished_at": "2025-01-01 10:00:01 UTC",
                "exit_code": 0,
                "steps": [
                    {
                        "name": "create-worktree",
                        "started_at": "2025-01-01 10:00:00 UTC",
                        "finished_at": "2025-01-01 10:00:01 UTC",
                        "exit_code": 0,
                        "data": {"llm_cost_usd": cost} if cost is not None else {},
                    }
                ],
            }
        ],
    }


def test_status_human_cost_present(tmp_path, capsys):
    import draft.command_status as cs

    state = _make_status_state_with_cost(0.42)
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000"))

    out = capsys.readouterr().out
    assert "cost:          $0.42" in out
    lines = out.splitlines()
    runtime_idx = next(i for i, line in enumerate(lines) if "total runtime:" in line)
    cost_idx = next(i for i, line in enumerate(lines) if "cost:" in line)
    assert cost_idx == runtime_idx + 1


def test_status_human_cost_absent(tmp_path, capsys):
    import draft.command_status as cs

    state = _make_status_state_with_cost(None)
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000"))

    assert "cost:          -" in capsys.readouterr().out


def test_status_human_cost_subcent(tmp_path, capsys):
    import draft.command_status as cs

    state = _make_status_state_with_cost(0.003)
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000"))

    assert "cost:          $0.00" in capsys.readouterr().out


def test_status_json_cost_present(tmp_path, capsys):
    import draft.command_status as cs

    state = _make_status_state_with_cost(0.42)
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000", use_json=True))

    data = json.loads(capsys.readouterr().out)
    assert data["total_llm_cost_usd"] == pytest.approx(0.42)
    step = next(s for s in data["steps"] if s["name"] == "create-worktree")
    assert step["llm_cost_usd"] == pytest.approx(0.42)


def test_status_json_cost_absent(tmp_path, capsys):
    import draft.command_status as cs

    state = _make_status_state_with_cost(None)
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000", use_json=True))

    data = json.loads(capsys.readouterr().out)
    assert data["total_llm_cost_usd"] is None
    for step in data["steps"]:
        assert step["llm_cost_usd"] is None


def test_status_json_cost_per_step_sum_across_sessions(tmp_path, capsys):
    import draft.command_status as cs

    state = {
        "completed": ["create-worktree"],
        "data": {"branch": "feat", "pipeline": "create"},
        "sessions": [
            {
                "command": "create",
                "started_at": "2025-01-01 10:00:00 UTC",
                "finished_at": "2025-01-01 10:00:01 UTC",
                "exit_code": 0,
                "steps": [
                    {
                        "name": "create-worktree",
                        "started_at": "2025-01-01 10:00:00 UTC",
                        "finished_at": "2025-01-01 10:00:01 UTC",
                        "exit_code": 0,
                        "data": {"llm_cost_usd": 0.10},
                    },
                ],
            },
            {
                "command": "continue",
                "started_at": "2025-01-01 11:00:00 UTC",
                "finished_at": "2025-01-01 11:00:01 UTC",
                "exit_code": 0,
                "steps": [
                    {
                        "name": "create-worktree",
                        "started_at": "2025-01-01 11:00:00 UTC",
                        "finished_at": "2025-01-01 11:00:01 UTC",
                        "exit_code": 0,
                        "data": {"llm_cost_usd": 0.15},
                    },
                ],
            },
        ],
    }
    run_dir = _make_status_run(tmp_path, state=state)

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.runs.is_run_active", return_value=False),
    ):
        cs.run(_make_status_args("260508-100000", use_json=True))

    data = json.loads(capsys.readouterr().out)
    assert data["total_llm_cost_usd"] == pytest.approx(0.25)
    step = next(s for s in data["steps"] if s["name"] == "create-worktree")
    assert step["llm_cost_usd"] == pytest.approx(0.25)


# --- _validate_overrides ---


def test_validate_overrides_rejects_max_retries_on_single_shot_step(capsys):
    import draft.command_create as cmd

    with pytest.raises(SystemExit) as exc:
        cmd._validate_overrides(["push-commits.max_retries=3"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "push-commits" in err
    assert "max_retries" in err


def test_validate_overrides_rejects_retry_delay_on_implement_spec(capsys):
    import draft.command_create as cmd

    with pytest.raises(SystemExit) as exc:
        cmd._validate_overrides(["implement-spec.retry_delay=0"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "retry_delay" in err


def test_validate_overrides_rejects_retry_delay_on_babysit_pr(capsys):
    import draft.command_create as cmd

    with pytest.raises(SystemExit) as exc:
        cmd._validate_overrides(["babysit-pr.retry_delay=10"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "retry_delay" in err


def test_validate_overrides_accepts_max_retries_on_implement_spec():
    import draft.command_create as cmd

    cmd._validate_overrides(["implement-spec.max_retries=3"])  # must not raise


def test_validate_overrides_malformed_is_ignored():
    import draft.command_create as cmd

    cmd._validate_overrides(["foo"])  # must not raise


# --- command_init ---


def _run_init(tmp_path):
    import draft.command_init as ci

    with patch("draft.command_init._repo_root", return_value=str(tmp_path)):
        return ci.run(object())


def test_init_happy_path(tmp_path, capsys):
    result = _run_init(tmp_path)
    assert result == 0
    target = tmp_path / ".draft" / "config.yaml"
    assert target.exists()
    out = capsys.readouterr().out
    assert out.strip() == str(target)


def test_init_pipeline_order(tmp_path):
    import yaml

    _run_init(tmp_path)
    data = yaml.safe_load((tmp_path / ".draft" / "config.yaml").read_text())
    assert list(data["steps"].keys()) == [
        "create-worktree",
        "implement-spec",
        "review-implementation",
        "push-commits",
        "open-pr",
        "babysit-pr",
        "delete-worktree",
    ]


def test_init_values_match_defaults(tmp_path):
    import yaml

    from draft.config import _LOOPING_STEPS
    from draft.pipelines import PIPELINES

    _run_init(tmp_path)
    data = yaml.safe_load((tmp_path / ".draft" / "config.yaml").read_text())
    for step in PIPELINES["create"].steps:
        d = step.defaults()
        cfg = data["steps"][step.name]
        if "timeout" in d:
            assert cfg["timeout"] == d["timeout"]
        if step.name in _LOOPING_STEPS and "max_retries" in d:
            assert cfg["max_retries"] == d["max_retries"]
        if step.name not in _LOOPING_STEPS:
            assert "max_retries" not in cfg


def test_init_already_exists(tmp_path, capsys):
    draft_dir = tmp_path / ".draft"
    draft_dir.mkdir()
    target = draft_dir / "config.yaml"
    target.write_text("existing content")

    result = _run_init(tmp_path)
    assert result == 1
    err = capsys.readouterr().err
    assert str(target) in err
    assert "delete" in err
    assert target.read_text() == "existing content"


def test_init_draft_is_file(tmp_path, capsys):
    draft_file = tmp_path / ".draft"
    draft_file.write_text("not a dir")

    result = _run_init(tmp_path)
    assert result == 1
    err = capsys.readouterr().err
    assert "not a directory" in err
    assert draft_file.read_text() == "not a dir"


def test_init_not_in_git_repo(tmp_path, capsys):
    import subprocess

    import draft.command_init as ci

    with patch(
        "draft.command_init._repo_root",
        side_effect=subprocess.CalledProcessError(128, "git"),
    ):
        result = ci.run(object())
    assert result == 1
    assert "git repository" in capsys.readouterr().err
    assert not (tmp_path / ".draft").exists()


def test_init_round_trip_load_config(tmp_path):
    import yaml

    from draft.config import load_config

    _run_init(tmp_path)
    cfg = load_config(str(tmp_path))
    data = yaml.safe_load((tmp_path / ".draft" / "config.yaml").read_text())
    assert cfg["steps"] == data["steps"]


def test_init_argparse_wiring():
    import argparse

    import draft.command_init as ci

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers()
    ci.register(subs)
    args = parser.parse_args(["init"])
    assert args.func is ci.run


def test_init_cli_has_init_subcommand():
    import argparse

    import draft.command_init as ci

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers()
    ci.register(subs)
    choices = subs.choices
    assert "init" in choices
