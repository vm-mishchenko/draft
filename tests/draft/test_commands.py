import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    with patch("draft.runs.find_run_dir", return_value=run_dir), \
         patch("draft.runs.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        mock_run.return_value.stdout = ""
        result = cmd_delete.run(FakeArgs())

    assert result == 0
    assert not run_dir.exists()
    branch_calls = [
        call for call in mock_run.call_args_list
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

    with patch("draft.runs.find_run_dir", return_value=run_dir), \
         patch("draft.runs.subprocess.run") as mock_run:
        result = cmd_delete.run(FakeArgs())

    assert result == 0
    branch_calls = [
        call for call in mock_run.call_args_list
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

    state = {"data": {"wt_dir": str(tmp_path / "gone"), "branch": "b", "repo": str(tmp_path)}}
    run_dir = _make_run_dir(tmp_path, state=state)

    with patch("draft.runs.subprocess.run") as mock_run:
        result = r.delete_run(run_dir)

    worktree_calls = [
        c for c in mock_run.call_args_list
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
        c for c in mock_run.call_args_list
        if c.args and c.args[0][:3] == ["git", "branch", "-D"]
    ]
    assert len(branch_calls) == 1


def test_delete_run_delete_branch_missing_branch(tmp_path):
    import draft.runs as r

    state = {"data": {}}
    run_dir = _make_run_dir(tmp_path, state=state)

    result = r.delete_run(run_dir, delete_branch=True)

    assert "--delete-branch requested but branch or repo missing from state" in result["warnings"]
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
        "completed": ["worktree-create", "code-spec", "push", "pr-open", "pr-view", "pr-babysit"],
        "data": {},
    }
    assert r.is_run_finished(state) is True


def test_is_run_finished_missing_pr_babysit(tmp_path):
    import draft.runs as r

    state = {
        "completed": ["worktree-create", "code-spec", "push", "pr-open", "pr-view"],
        "data": {},
    }
    assert r.is_run_finished(state) is False


def test_is_run_finished_skip_pr_true(tmp_path):
    import draft.runs as r

    state = {
        "completed": ["worktree-create", "code-spec"],
        "data": {"skip_pr": True},
    }
    assert r.is_run_finished(state) is True


def test_is_run_finished_skip_pr_false(tmp_path):
    import draft.runs as r

    state = {
        "completed": ["worktree-create", "code-spec"],
        "data": {"skip_pr": False},
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
        "completed": ["worktree-create", "code-spec", "push", "pr-open", "pr-view", "pr-babysit"],
        "data": {"branch": branch},
    }


def _partial_state(branch="draft/feat"):
    return {
        "completed": ["worktree-create"],
        "data": {"branch": branch},
    }


def test_prune_no_runs(tmp_path, capsys):
    import draft.command_prune as cp

    with patch("draft.runs.current_project_name", return_value="myproject"), \
         patch("draft.runs.project_runs", return_value=[]):
        result = cp.run(_make_prune_args())

    assert result == 0
    assert "no runs to prune" in capsys.readouterr().out


def test_prune_one_finished_one_in_progress_default_selection(tmp_path, capsys, monkeypatch):
    import draft.command_prune as cp

    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=_full_state())
    run_partial = _make_run_dir(tmp_path, run_id="260505-120000", state=_partial_state())

    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))

    with patch("draft.runs.current_project_name", return_value="myproject"), \
         patch("draft.runs.project_runs", return_value=[run_done, run_partial]):
        result = cp.run(_make_prune_args())

    captured = capsys.readouterr()
    assert "260505-130000" in captured.out
    assert "260505-120000" not in captured.out


def test_prune_confirmation_declined(tmp_path, capsys, monkeypatch):
    import draft.command_prune as cp

    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=_full_state())

    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))

    with patch("draft.runs.current_project_name", return_value="myproject"), \
         patch("draft.runs.project_runs", return_value=[run_done]), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="n"):
        result = cp.run(_make_prune_args())

    assert result == 0
    assert "aborted" in capsys.readouterr().out
    assert run_done.exists()


def test_prune_yes_deletes_run(tmp_path, capsys):
    import draft.command_prune as cp

    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=_full_state())

    with patch("draft.runs.current_project_name", return_value="myproject"), \
         patch("draft.runs.project_runs", return_value=[run_done]):
        result = cp.run(_make_prune_args(yes=True))

    assert result == 0
    assert not run_done.exists()
    captured = capsys.readouterr()
    assert "deleted 1" in captured.out


def test_prune_dry_run_no_deletion(tmp_path, capsys):
    import draft.command_prune as cp

    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=_full_state())

    with patch("draft.runs.current_project_name", return_value="myproject"), \
         patch("draft.runs.project_runs", return_value=[run_done]):
        result = cp.run(_make_prune_args(dry_run=True, yes=True))

    assert result == 0
    assert run_done.exists()
    captured = capsys.readouterr()
    assert "would" in captured.out


def test_prune_all_includes_incomplete(tmp_path, capsys):
    import draft.command_prune as cp

    run_partial = _make_run_dir(tmp_path, run_id="260505-120000", state=_partial_state())

    with patch("draft.runs.current_project_name", return_value="myproject"), \
         patch("draft.runs.project_runs", return_value=[run_partial]):
        result = cp.run(_make_prune_args(include_all=True, dry_run=True))

    assert result == 0
    captured = capsys.readouterr()
    assert "260505-120000" in captured.out


def test_prune_all_skips_active(tmp_path, capsys):
    import draft.command_prune as cp

    run_active = _make_run_dir(tmp_path, run_id="260505-120000", state=_partial_state())
    (run_active / "draft.pid").write_text(str(os.getpid()))

    with patch("draft.runs.current_project_name", return_value="myproject"), \
         patch("draft.runs.project_runs", return_value=[run_active]):
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

    run_a = _make_run_dir(tmp_path, run_id="260505-130000", project="proj_a", state=_full_state())
    run_b = _make_run_dir(tmp_path, run_id="260505-120000", project="proj_b", state=_full_state())

    with patch("draft.runs.all_project_names", return_value=["proj_a", "proj_b"]), \
         patch("draft.runs.project_runs", side_effect=lambda name: [run_a] if name == "proj_a" else [run_b]):
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
        "completed": ["worktree-create", "code-spec", "push", "pr-open", "pr-view", "pr-babysit"],
        "data": {"branch": "draft/feat", "repo": str(repo_dir)},
    }
    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=state)

    with patch("draft.runs.current_project_name", return_value="myproject"), \
         patch("draft.runs.project_runs", return_value=[run_done]), \
         patch("draft.runs.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        mock_run.return_value.stdout = ""
        result = cp.run(_make_prune_args(yes=True, delete_branch=True))

    assert result == 0
    branch_calls = [
        c for c in mock_run.call_args_list
        if c.args and c.args[0][:3] == ["git", "branch", "-D"]
    ]
    assert len(branch_calls) == 1


def test_prune_non_tty_stdin_without_yes(tmp_path, capsys, monkeypatch):
    import draft.command_prune as cp

    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=_full_state())

    with patch("draft.runs.current_project_name", return_value="myproject"), \
         patch("draft.runs.project_runs", return_value=[run_done]), \
         patch("sys.stdin.isatty", return_value=False):
        result = cp.run(_make_prune_args())

    assert result != 0
    assert "non-interactively" in capsys.readouterr().err


def test_prune_skip_pr_finished_included(tmp_path, capsys):
    import draft.command_prune as cp

    state = {
        "completed": ["worktree-create", "code-spec"],
        "data": {"branch": "draft/feat", "skip_pr": True},
    }
    run_done = _make_run_dir(tmp_path, run_id="260505-130000", state=state)

    with patch("draft.runs.current_project_name", return_value="myproject"), \
         patch("draft.runs.project_runs", return_value=[run_done]):
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
        cmd._reject_flag_conflicts(_make_create_args(delete_worktree=True, no_worktree=True))
    assert exc.value.code == 2
    assert "--delete-worktree" in capsys.readouterr().err


def test_reject_flag_conflicts_no_op_when_clean():
    import draft.command_create as cmd

    cmd._reject_flag_conflicts(_make_create_args())  # must not raise


# --- create-modes: _compose_active_steps ---


def test_compose_active_steps_default():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps("worktree", "open", False)
    assert [s.name for s in active] == ["worktree-create", "code-spec", "push", "pr-open", "pr-view", "pr-babysit"]
    assert skipped == set()


def test_compose_active_steps_no_worktree():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps("no-worktree", "open", False)
    assert "worktree-create" not in [s.name for s in active]
    assert "worktree-create" in skipped


def test_compose_active_steps_skip_pr():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps("worktree", "skip", True)
    assert [s.name for s in active] == ["worktree-create", "code-spec"]
    assert skipped == {"push", "pr-open", "pr-view", "pr-babysit"}


def test_compose_active_steps_pr_reuse_skips_pr_open():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps("worktree", "reuse", False)
    names = [s.name for s in active]
    assert "pr-open" not in names
    assert "pr-view" in names
    assert "pr-babysit" in names
    assert "pr-open" in skipped


# --- create-modes: runs.expected_steps with new keys ---


def test_expected_steps_legacy_full():
    import draft.runs as r

    state = {"completed": [], "data": {}}
    assert r.expected_steps(state) == r.FULL_PIPELINE_STEPS


def test_expected_steps_legacy_skip_pr():
    import draft.runs as r

    state = {"completed": [], "data": {"skip_pr": True}}
    assert r.expected_steps(state) == r.SKIP_PR_STEPS


def test_expected_steps_no_worktree_open():
    import draft.runs as r

    state = {"completed": [], "data": {"worktree_mode": "no-worktree", "pr_mode": "open"}}
    assert r.expected_steps(state) == ("code-spec", "push", "pr-open", "pr-view", "pr-babysit")


def test_expected_steps_pr_reuse():
    import draft.runs as r

    state = {"completed": [], "data": {"worktree_mode": "worktree", "pr_mode": "reuse"}}
    assert r.expected_steps(state) == ("worktree-create", "code-spec", "push", "pr-view", "pr-babysit")


def test_expected_steps_no_worktree_skip_pr():
    import draft.runs as r

    state = {"completed": [], "data": {"worktree_mode": "no-worktree", "skip_pr": True}}
    assert r.expected_steps(state) == ("code-spec",)


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
        "completed": ["worktree-create"],
        "data": {"branch": "foo"},
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
        "data": {"branch": "foo"},
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
        "completed": ["worktree-create"],
        "data": {"branch": "other"},
    }
    (run_dir / "state.json").write_text(json.dumps(state))

    with patch("draft.runs.runs_base", return_value=tmp_path):
        result = r.find_active_run_on_branch("proj", "foo")
    assert result is None


# --- create-modes: command_continue drift and finished-run exit ---


def _continue_state(*, completed=None, branch="foo", wt_dir="/tmp/wt", repo="/tmp/repo",
                    worktree_mode="worktree", delete_worktree=False, skip_pr=False, pr_mode="open"):
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


def test_continue_drift_no_worktree_refuses(tmp_path, capsys):
    import draft.command_continue as cmd_continue

    project_dir = tmp_path / "proj"
    run_dir = project_dir / "260506-100000"
    run_dir.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    state = _continue_state(
        completed=["code-spec"],
        wt_dir=str(repo),
        repo=str(repo),
        worktree_mode="no-worktree",
        branch="foo",
    )
    state["run_dir"] = str(run_dir)
    (run_dir / "state.json").write_text(json.dumps(state))

    class FakeArgs:
        run_id = "260506-100000"

    with patch("draft.runs.find_run_dir", return_value=run_dir), \
         patch("draft.command_continue._branch_at", return_value="other-branch"):
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
        completed=["worktree-create", "code-spec"],
        wt_dir=str(wt),
        branch="foo",
    )
    state["run_dir"] = str(run_dir)
    (run_dir / "state.json").write_text(json.dumps(state))

    class FakeArgs:
        run_id = "260506-100000"

    with patch("draft.runs.find_run_dir", return_value=run_dir), \
         patch("draft.command_continue._branch_at", return_value="other-branch"):
        result = cmd_continue.run(FakeArgs())

    assert result == 2
    assert "drift" in capsys.readouterr().err


# --- create-modes: WorktreeCreateStep existing-branch mode ---


def test_worktree_create_existing_branch_uses_no_dash_b(tmp_path):
    from draft.steps.worktree_create import WorktreeCreateStep
    from pipeline import RunContext

    ctx = RunContext("rid", tmp_path, {"worktree-create": {"max_retries": 1, "timeout": 60}})
    ctx.set("branch", "foo")
    ctx.set("base_branch", "origin/main")
    ctx.set("wt_dir", "/tmp/wt")
    ctx.set("branch_source", "existing")

    cmd = WorktreeCreateStep().cmd(ctx)
    assert cmd == ["git", "worktree", "add", "/tmp/wt", "foo"]
    assert "-b" not in cmd


def test_worktree_create_new_branch_uses_dash_b(tmp_path):
    from draft.steps.worktree_create import WorktreeCreateStep
    from pipeline import RunContext

    ctx = RunContext("rid", tmp_path, {"worktree-create": {"max_retries": 1, "timeout": 60}})
    ctx.set("branch", "foo")
    ctx.set("base_branch", "origin/main")
    ctx.set("wt_dir", "/tmp/wt")
    ctx.set("branch_source", "new")

    cmd = WorktreeCreateStep().cmd(ctx)
    assert cmd == ["git", "worktree", "add", "/tmp/wt", "-b", "foo", "origin/main"]
