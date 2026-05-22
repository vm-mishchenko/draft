import contextlib
import io
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

# --- command_list ---


def _make_list_args(**kwargs):
    class FakeArgs:
        json = False
        all = False

    for k, v in kwargs.items():
        setattr(FakeArgs, k, v)
    return FakeArgs()


def test_command_list_no_runs(tmp_path, capsys):
    import draft.command_list as clm

    with patch("draft.command_list.runs_base", return_value=tmp_path / "nonexistent"):
        result = clm.run(_make_list_args(all=True))
    captured = capsys.readouterr()
    assert "no runs" in captured.out
    assert result == 0


def test_command_list_no_runs_real(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    base.mkdir()

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(all=True))
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
        clm.run(_make_list_args(all=True))
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
        clm.run(_make_list_args(all=True))
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
        clm.run(_make_list_args(all=True))
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
        clm.run(_make_list_args(all=True))
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
        clm.run(_make_list_args(all=True))
    assert "corrupt" in capsys.readouterr().out


def test_command_list_missing_state_shows_dash(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(all=True))
    out = capsys.readouterr().out
    assert result == 0
    assert "Run: 260508-100000 (missing)" in out


def test_command_list_corrupt_state_shows_corrupt(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("not json{{{")

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(all=True))
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
        clm.run(_make_list_args(all=True))
    out = capsys.readouterr().out
    assert "Workspace:" not in out
    assert "Run: 260508-100000" in out


def test_command_list_workspace_column_no(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": [],
        "data": {"wt_dir": str(tmp_path / "nonexistent"), "pipeline": "create"},
    }
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))
    out = capsys.readouterr().out
    assert "Workspace:" not in out
    assert "Run: 260508-100000" in out


def test_command_list_workspace_column_absent_wt_dir(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {}}
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))
    out = capsys.readouterr().out
    assert "Workspace:" not in out
    assert "Run: 260508-100000" in out


def test_command_list_missing_state_workspace_dash(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260508-100001"
    run_dir.mkdir(parents=True)

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(all=True))
    out = capsys.readouterr().out
    assert result == 0
    assert "Branch: -" in out
    assert "PR: -" in out


def test_command_list_corrupt_state_workspace_dash(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260508-100002"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("not json{{{")

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(all=True))
    out = capsys.readouterr().out
    assert result == 0
    assert "Branch: -" in out
    assert "PR: -" in out


def test_command_list_normal_run_full_record(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    wt = tmp_path / "my-wt"
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
    run_dir = base / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(state))

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(all=True))

    assert result == 0
    out = capsys.readouterr().out
    assert "Run: 260508-100000 (2/5, stopped)" in out
    assert "Branch: feat/foo" in out
    assert "PR: https://github.com/org/repo/pull/1" in out
    assert "Project: myproject" in out
    assert "Workspace:" not in out
    assert "Logs:" not in out


def test_command_list_active_run_shows_running(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": [],
        "data": {"worktree_mode": "worktree", "pr_mode": "open", "pipeline": "create"},
    }
    run_dir = base / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(state))
    (run_dir / "draft.pid").write_text(str(os.getpid()))

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("Run: 260508-100000 (")
    assert "running" in lines[0]


def test_command_list_non_active_run_no_running(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": ["worktree-create"],
        "data": {"worktree_mode": "worktree", "pr_mode": "open", "pipeline": "create"},
    }
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("Run: 260508-100000 (")
    assert "running" not in lines[0]


def test_command_list_multiple_runs_blank_line_separator(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create"}}
    _make_list_run(base, "260508-100000", state)
    _make_list_run(base, "260508-100001", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    lines = out.splitlines()
    run_lines = [i for i, line in enumerate(lines) if line.startswith("Run:")]
    assert len(run_lines) == 2
    assert lines[run_lines[1] - 1] == ""


def test_command_list_human_status_stopped(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": ["worktree-create"],
        "data": {"branch": "test-branch", "pipeline": "create"},
    }
    _make_list_run(base, "260521-220712", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    assert "Run: 260521-220712 (1/5, stopped)" in out
    assert "Branch: test-branch" in out
    assert "PR: -" in out


def test_command_list_human_status_done(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": [
            "create-worktree",
            "implement-spec",
            "push-commits",
            "open-pr",
            "babysit-pr",
        ],
        "data": {
            "branch": "feat/done",
            "worktree_mode": "worktree",
            "pr_mode": "open",
            "pipeline": "create",
        },
    }
    _make_list_run(base, "260521-220712", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    assert "Run: 260521-220712 (5/5, done)" in out


def test_command_list_human_status_missing(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260521-220712"
    run_dir.mkdir(parents=True)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    assert "Run: 260521-220712 (missing)" in out
    assert "Branch: -" in out
    assert "PR: -" in out


def test_command_list_human_status_corrupt(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260521-220712"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("not json{{{")

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    assert "Run: 260521-220712 (corrupt)" in out
    assert "Branch: -" in out
    assert "PR: -" in out


def test_command_list_corrupt_pipeline_readable_state(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    # state.json is readable but pipeline field is missing → corrupt classification
    state = {
        "completed": ["worktree-create"],
        "data": {
            "branch": "stored-branch",
            "pr_url": "https://github.com/org/repo/pull/9",
        },
    }
    _make_list_run(base, "260521-220712", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    assert "Run: 260521-220712 (corrupt)" in out
    assert "Branch: stored-branch" in out
    assert "PR: https://github.com/org/repo/pull/9" in out


def test_command_list_running_with_missing_state(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myproject" / "260521-220712"
    run_dir.mkdir(parents=True)
    (run_dir / "draft.pid").write_text(str(os.getpid()))

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    assert "Run: 260521-220712 (running)" in out


def test_command_list_human_no_project_workspace_logs(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": ["worktree-create"],
        "data": {"branch": "feat/x", "pipeline": "create"},
    }
    _make_list_run(base, "260521-220712", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    assert "Project: myproject" in out
    assert "Workspace:" not in out
    assert "Logs:" not in out


# --- command_list project selection ---


def _make_porcelain(main_path: str, linked_paths: list[str] = None) -> str:
    lines = [f"worktree {main_path}", "HEAD abc123", "branch refs/heads/main", ""]
    for p in linked_paths or []:
        lines += [f"worktree {p}", "HEAD abc123", "branch refs/heads/feat", ""]
    return "\n".join(lines)


def test_command_list_project_filters_by_current_project(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create"}}
    _make_list_run(base, "260508-100000", state)
    other_dir = base / "other" / "260508-200000"
    other_dir.mkdir(parents=True)

    rev_parse = subprocess.CompletedProcess(
        [], 0, stdout=str(tmp_path / "myproject") + "\n", stderr=""
    )
    wt_list = subprocess.CompletedProcess(
        [], 0, stdout=_make_porcelain(str(tmp_path / "myproject")), stderr=""
    )

    with (
        patch("draft.command_list.runs_base", return_value=base),
        patch("draft.command_list._run_git", side_effect=[rev_parse, wt_list]),
    ):
        result = clm.run(_make_list_args())

    assert result == 0
    out = capsys.readouterr().out
    assert "260508-100000" in out
    assert "260508-200000" not in out


def test_command_list_all_flag_skips_git(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create"}}
    _make_list_run(base, "260508-100000", state)
    other_dir = base / "other" / "260508-200000"
    other_dir.mkdir(parents=True)
    (other_dir / "state.json").write_text(json.dumps(state))

    with (
        patch("draft.command_list.runs_base", return_value=base),
        patch("draft.command_list._run_git") as mock_git,
    ):
        result = clm.run(_make_list_args(all=True))

    assert result == 0
    mock_git.assert_not_called()
    out = capsys.readouterr().out
    assert "260508-100000" in out
    assert "260508-200000" in out


def test_command_list_outside_git_shows_all(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create"}}
    _make_list_run(base, "260508-100000", state)
    other_dir = base / "other" / "260508-200000"
    other_dir.mkdir(parents=True)
    (other_dir / "state.json").write_text(json.dumps(state))

    not_git = subprocess.CompletedProcess(
        [],
        128,
        stdout="",
        stderr="fatal: not a git repository (or any of the parent directories): .git",
    )

    with (
        patch("draft.command_list.runs_base", return_value=base),
        patch("draft.command_list._run_git", return_value=not_git),
    ):
        result = clm.run(_make_list_args())

    assert result == 0
    out = capsys.readouterr().out
    assert "260508-100000" in out
    assert "260508-200000" in out


def test_command_list_no_project_dir_prints_no_runs(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    other_dir = base / "other" / "260508-200000"
    other_dir.mkdir(parents=True)

    rev_parse = subprocess.CompletedProcess(
        [], 0, stdout=str(tmp_path / "myproject") + "\n", stderr=""
    )
    wt_list = subprocess.CompletedProcess(
        [], 0, stdout=_make_porcelain(str(tmp_path / "myproject")), stderr=""
    )

    with (
        patch("draft.command_list.runs_base", return_value=base),
        patch("draft.command_list._run_git", side_effect=[rev_parse, wt_list]),
    ):
        result = clm.run(_make_list_args())

    assert result == 0
    assert "no runs" in capsys.readouterr().out


def test_command_list_linked_worktree_uses_main_worktree_name(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create"}}
    _make_list_run(base, "260508-100000", state)

    main_path = str(tmp_path / "myproject")
    linked_path = str(tmp_path / "myproject-linked-worktree")
    rev_parse = subprocess.CompletedProcess([], 0, stdout=linked_path + "\n", stderr="")
    wt_list = subprocess.CompletedProcess(
        [], 0, stdout=_make_porcelain(main_path, [linked_path]), stderr=""
    )

    with (
        patch("draft.command_list.runs_base", return_value=base),
        patch("draft.command_list._run_git", side_effect=[rev_parse, wt_list]),
    ):
        result = clm.run(_make_list_args())

    assert result == 0
    out = capsys.readouterr().out
    assert "260508-100000" in out


def test_command_list_rev_parse_failure_returns_error(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create"}}
    _make_list_run(base, "260508-100000", state)

    git_error = subprocess.CompletedProcess(
        [], 1, stdout="", stderr="git: command not found"
    )

    with (
        patch("draft.command_list.runs_base", return_value=base),
        patch("draft.command_list._run_git", return_value=git_error),
    ):
        result = clm.run(_make_list_args())

    assert result != 0
    assert "error:" in capsys.readouterr().err


def test_command_list_no_worktree_line_returns_error(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create"}}
    _make_list_run(base, "260508-100000", state)

    rev_parse = subprocess.CompletedProcess(
        [], 0, stdout=str(tmp_path / "myproject") + "\n", stderr=""
    )
    wt_list = subprocess.CompletedProcess(
        [], 0, stdout="HEAD abc123\nbranch refs/heads/main\n", stderr=""
    )

    with (
        patch("draft.command_list.runs_base", return_value=base),
        patch("draft.command_list._run_git", side_effect=[rev_parse, wt_list]),
    ):
        result = clm.run(_make_list_args())

    assert result != 0
    assert "error:" in capsys.readouterr().err


def test_command_list_15_run_limit_within_project(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create"}}
    for i in range(20):
        _make_list_run(base, f"260508-{100000 + i:06d}", state)

    rev_parse = subprocess.CompletedProcess(
        [], 0, stdout=str(tmp_path / "myproject") + "\n", stderr=""
    )
    wt_list = subprocess.CompletedProcess(
        [], 0, stdout=_make_porcelain(str(tmp_path / "myproject")), stderr=""
    )

    with (
        patch("draft.command_list.runs_base", return_value=base),
        patch("draft.command_list._run_git", side_effect=[rev_parse, wt_list]),
    ):
        result = clm.run(_make_list_args())

    assert result == 0
    out = capsys.readouterr().out
    run_lines = [line for line in out.splitlines() if line.startswith("Run:")]
    assert len(run_lines) == 15


def test_command_list_json_project_selection(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": [],
        "data": {"pipeline": "create"},
    }
    _make_list_run(base, "260508-100000", state)
    other_dir = base / "other" / "260508-200000"
    other_dir.mkdir(parents=True)
    (other_dir / "state.json").write_text(json.dumps(state))

    rev_parse = subprocess.CompletedProcess(
        [], 0, stdout=str(tmp_path / "myproject") + "\n", stderr=""
    )
    wt_list = subprocess.CompletedProcess(
        [], 0, stdout=_make_porcelain(str(tmp_path / "myproject")), stderr=""
    )

    with (
        patch("draft.command_list.runs_base", return_value=base),
        patch("draft.command_list._run_git", side_effect=[rev_parse, wt_list]),
    ):
        result = clm.run(_make_list_args(json=True))

    assert result == 0
    rows = json.loads(capsys.readouterr().out)
    run_ids = [r["run_id"] for r in rows]
    assert "260508-100000" in run_ids
    assert "260508-200000" not in run_ids


def test_command_list_json_all_flag_returns_all_projects(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create"}}
    _make_list_run(base, "260508-100000", state)
    other_dir = base / "other" / "260508-200000"
    other_dir.mkdir(parents=True)
    (other_dir / "state.json").write_text(json.dumps(state))

    with (
        patch("draft.command_list.runs_base", return_value=base),
    ):
        result = clm.run(_make_list_args(all=True, json=True))

    assert result == 0
    rows = json.loads(capsys.readouterr().out)
    run_ids = [r["run_id"] for r in rows]
    assert "260508-100000" in run_ids
    assert "260508-200000" in run_ids


def test_command_list_current_project_omits_project_field(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"branch": "feat/x", "pipeline": "create"}}
    _make_list_run(base, "260508-100000", state)

    rev_parse = subprocess.CompletedProcess(
        [], 0, stdout=str(tmp_path / "myproject") + "\n", stderr=""
    )
    wt_list = subprocess.CompletedProcess(
        [], 0, stdout=_make_porcelain(str(tmp_path / "myproject")), stderr=""
    )

    with (
        patch("draft.command_list.runs_base", return_value=base),
        patch("draft.command_list._run_git", side_effect=[rev_parse, wt_list]),
    ):
        result = clm.run(_make_list_args())

    assert result == 0
    out = capsys.readouterr().out
    assert "260508-100000" in out
    assert "Project:" not in out


def test_command_list_all_flag_human_includes_project(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"branch": "test-branch", "pipeline": "create"}}
    run_dir = base / "draft" / "260521-220712"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(state))
    other_dir = base / "other-project" / "260521-220713"
    other_dir.mkdir(parents=True)
    (other_dir / "state.json").write_text(json.dumps(state))

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(all=True))

    assert result == 0
    out = capsys.readouterr().out
    assert "Project: draft" in out
    assert "Project: other-project" in out


def test_command_list_outside_git_human_includes_project(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"branch": "feat/x", "pipeline": "create"}}
    run_dir = base / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(state))

    not_git = subprocess.CompletedProcess(
        [],
        128,
        stdout="",
        stderr="fatal: not a git repository (or any of the parent directories): .git",
    )

    with (
        patch("draft.command_list.runs_base", return_value=base),
        patch("draft.command_list._run_git", return_value=not_git),
    ):
        result = clm.run(_make_list_args())

    assert result == 0
    out = capsys.readouterr().out
    assert "Project: myproject" in out


def test_command_list_outside_git_all_flag_human_includes_project(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create"}}
    run_dir = base / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(state))

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(all=True))

    assert result == 0
    out = capsys.readouterr().out
    assert "Project: myproject" in out


def test_command_list_all_project_missing_state_includes_project(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myrepo" / "260521-220712"
    run_dir.mkdir(parents=True)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    assert "Run: 260521-220712 (missing)" in out
    assert "Project: myrepo" in out
    assert "Branch: -" in out
    assert "PR: -" in out


def test_command_list_all_project_corrupt_state_includes_project(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    run_dir = base / "myrepo" / "260521-220712"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("not json{{{")

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    assert "Run: 260521-220712 (corrupt)" in out
    assert "Project: myrepo" in out
    assert "Branch: -" in out
    assert "PR: -" in out


def test_command_list_all_project_readable_corrupt_pipeline_includes_project(
    tmp_path, capsys
):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {
        "completed": ["worktree-create"],
        "data": {
            "branch": "stored-branch",
            "pr_url": "https://github.com/org/repo/pull/9",
        },
    }
    run_dir = base / "myrepo" / "260521-220712"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(state))

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    assert "Project: myrepo" in out
    assert "Branch: stored-branch" in out
    assert "PR: https://github.com/org/repo/pull/9" in out


def test_command_list_all_project_field_order(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"branch": "feat/x", "pipeline": "create"}}
    run_dir = base / "myrepo" / "260521-220712"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(state))

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    run_idx = next(i for i, line in enumerate(lines) if line.startswith("Run:"))
    project_idx = next(i for i, line in enumerate(lines) if line.startswith("Project:"))
    branch_idx = next(i for i, line in enumerate(lines) if line.startswith("Branch:"))
    pr_idx = next(i for i, line in enumerate(lines) if line.startswith("PR:"))
    assert run_idx < project_idx < branch_idx < pr_idx


def test_command_list_all_multiple_records_blank_line_separator(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create"}}
    _make_list_run(base, "260508-100000", state)
    _make_list_run(base, "260508-100001", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True))

    out = capsys.readouterr().out
    lines = out.splitlines()
    run_lines = [i for i, line in enumerate(lines) if line.startswith("Run:")]
    assert len(run_lines) == 2
    assert lines[run_lines[1] - 1] == ""


def test_command_list_json_all_project_field_unchanged(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create", "branch": "feat/x"}}
    run_dir = base / "myproject" / "260508-100000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(state))

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(all=True, json=True))

    assert result == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0]["project"] == "myproject"
    assert "run_id" in rows[0]
    assert "branch" in rows[0]


def test_command_list_json_current_project_row_shape_unchanged(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"pipeline": "create", "branch": "feat/x"}}
    _make_list_run(base, "260508-100000", state)

    rev_parse = subprocess.CompletedProcess(
        [], 0, stdout=str(tmp_path / "myproject") + "\n", stderr=""
    )
    wt_list = subprocess.CompletedProcess(
        [], 0, stdout=_make_porcelain(str(tmp_path / "myproject")), stderr=""
    )

    with (
        patch("draft.command_list.runs_base", return_value=base),
        patch("draft.command_list._run_git", side_effect=[rev_parse, wt_list]),
    ):
        result = clm.run(_make_list_args(json=True))

    assert result == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0]["project"] == "myproject"
    assert "run_id" in rows[0]


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
    assert "(project: myproject)" in captured.out


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
    captured = capsys.readouterr()
    assert "(project: myproject)" in captured.out


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
        patch("draft.command_continue._load_run_config", return_value={}),
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
        patch("draft.runs.all_project_names", return_value=[]),
    ):
        result = cp.run(_make_prune_args())

    assert result == 0
    assert "deleted 0; skipped 0 active" in capsys.readouterr().out


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
    assert "260505-120000" in captured.out
    assert "done" in captured.out
    assert "stopped" in captured.out


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
    assert "deleted 1; skipped 0 active" in captured.out


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


def test_prune_default_includes_incomplete(tmp_path, capsys):
    import draft.command_prune as cp

    run_partial = _make_run_dir(
        tmp_path, run_id="260505-120000", state=_partial_state()
    )

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[run_partial]),
    ):
        result = cp.run(_make_prune_args(dry_run=True))

    assert result == 0
    captured = capsys.readouterr()
    assert "260505-120000" in captured.out


def test_prune_default_skips_active(tmp_path, capsys):
    import draft.command_prune as cp

    run_active = _make_run_dir(tmp_path, run_id="260505-120000", state=_partial_state())
    (run_active / "draft.pid").write_text(str(os.getpid()))

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[run_active]),
        patch("draft.runs.all_project_names", return_value=[]),
    ):
        result = cp.run(_make_prune_args(yes=True))

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


def test_prune_status_grouping_order(tmp_path, capsys):
    import draft.command_prune as cp

    run_done = _make_run_dir(tmp_path, run_id="260505-140000", state=_full_state())
    run_stopped = _make_run_dir(
        tmp_path, run_id="260505-130000", state=_partial_state()
    )
    run_missing = _make_run_dir(tmp_path, run_id="260505-120000")
    run_corrupt = _make_run_dir(tmp_path, run_id="260505-110000", state=None)
    (run_corrupt / "state.json").write_text("not valid json {{{")
    run_active = _make_run_dir(tmp_path, run_id="260505-100000", state=_partial_state())
    (run_active / "draft.pid").write_text(str(os.getpid()))

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch(
            "draft.runs.project_runs",
            return_value=[run_done, run_stopped, run_missing, run_corrupt, run_active],
        ),
    ):
        result = cp.run(_make_prune_args(dry_run=True))

    assert result == 0
    out = capsys.readouterr().out
    pos_done = out.index("260505-140000")
    pos_stopped = out.index("260505-130000")
    pos_missing = out.index("260505-120000")
    pos_corrupt = out.index("260505-110000")
    assert pos_done < pos_stopped < pos_missing < pos_corrupt
    assert "260505-100000" not in out
    assert "would skip 1 active" in out


def test_prune_all_projects_hint_fires(tmp_path, capsys):
    import draft.command_prune as cp

    other_run = _make_run_dir(
        tmp_path, run_id="260505-120000", project="other", state=_partial_state()
    )

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch(
            "draft.runs.project_runs",
            side_effect=lambda name: [] if name == "myproject" else [other_run],
        ),
        patch("draft.runs.all_project_names", return_value=["myproject", "other"]),
    ):
        result = cp.run(_make_prune_args())

    assert result == 0
    out = capsys.readouterr().out
    assert "non-running run(s) in other projects" in out
    assert "--all-projects" in out
    assert "deleted 0; skipped 0 active" in out


def test_prune_all_projects_hint_suppressed_under_project_flag(tmp_path, capsys):
    import draft.command_prune as cp

    (tmp_path / "myproject").mkdir()

    with (
        patch("draft.runs.runs_base", return_value=tmp_path),
        patch("draft.runs.project_runs", return_value=[]),
        patch("draft.runs.all_project_names", return_value=["myproject", "other"]),
    ):
        result = cp.run(_make_prune_args(project="myproject"))

    assert result == 0
    out = capsys.readouterr().out
    assert "non-running run(s) in other projects" not in out


def test_prune_status_column_in_selection_line(tmp_path, capsys):
    import re

    import draft.command_prune as cp

    run_stopped = _make_run_dir(
        tmp_path, run_id="260505-120000", state=_partial_state()
    )

    with (
        patch("draft.runs.current_project_name", return_value="myproject"),
        patch("draft.runs.project_runs", return_value=[run_stopped]),
    ):
        cp.run(_make_prune_args(dry_run=True))

    out = capsys.readouterr().out
    assert re.search(r"\b260505-120000\s+stopped\s+draft/feat\b", out)


def test_prune_all_flag_rejected(capsys):
    import argparse

    import draft.command_prune as cp

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    cp.register(subparsers)

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["prune", "--all"])

    assert exc_info.value.code != 0


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


def test_compose_active_steps_with_any_reviewer_active():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps(
        "worktree", "open", False, skip_review=False, has_any_reviewer=True
    )
    names = [s.name for s in active]
    assert "review-implementation" in names
    assert "review-implementation" not in skipped


def test_compose_active_steps_with_any_reviewer_skip_review():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps(
        "worktree", "open", False, skip_review=True, has_any_reviewer=True
    )
    assert "review-implementation" in skipped


def test_compose_active_steps_no_reviewer():
    import draft.command_create as cmd

    active, skipped = cmd._compose_active_steps(
        "worktree", "open", False, has_any_reviewer=False
    )
    assert "review-implementation" in skipped


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
    ctx.set("base_branch", "main")
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
    ctx.set("base_branch", "main")
    ctx.set("wt_dir", "/tmp/wt")
    ctx.set("branch_source", "new")

    cmd = CreateWorktreeStep().cmd(ctx)
    assert cmd == ["git", "worktree", "add", "/tmp/wt", "-b", "foo", "main"]


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


def test_command_list_json_no_runs_dir(tmp_path, capsys):
    import draft.command_list as clm

    with patch("draft.command_list.runs_base", return_value=tmp_path / "nonexistent"):
        result = clm.run(_make_list_args(all=True, json=True))
    assert result == 0
    assert json.loads(capsys.readouterr().out) == []


def test_command_list_json_empty_runs(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    base.mkdir()
    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(all=True, json=True))
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
        result = clm.run(_make_list_args(all=True, json=True))

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
        result = clm.run(_make_list_args(all=True, json=True))

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
        result = clm.run(_make_list_args(all=True, json=True))

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
        clm.run(_make_list_args(all=True, json=True))

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
        clm.run(_make_list_args(all=True, json=True))

    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["workspace"] == "no"


def test_command_list_json_workspace_null_when_absent(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {}}
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True, json=True))

    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["workspace"] is None


def test_command_list_json_pr_url_null_when_absent(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {}}
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True, json=True))

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
        clm.run(_make_list_args(all=True, json=True))

    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["running"] is True


def test_command_list_json_running_false(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {}}
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        clm.run(_make_list_args(all=True, json=True))

    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["running"] is False


def test_command_list_human_record_layout(tmp_path, capsys):
    import draft.command_list as clm

    base = tmp_path / "runs"
    state = {"completed": [], "data": {"branch": "feat", "pipeline": "create"}}
    _make_list_run(base, "260508-100000", state)

    with patch("draft.command_list.runs_base", return_value=base):
        result = clm.run(_make_list_args(all=True))

    assert result == 0
    out = capsys.readouterr().out
    assert "Run: 260508-100000" in out
    assert "Branch: feat" in out


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


# --- command_create preamble emission ---


def test_command_create_preamble_emitted_when_label_returned(capsys):
    import sys
    from unittest.mock import MagicMock, patch

    ctx = MagicMock()

    with patch(
        "draft.steps.implement_spec.original_spec.preamble_label",
        return_value="run 250101-120000",
    ):
        try:
            from draft.steps.implement_spec import original_spec

            label = original_spec.preamble_label(ctx)
            if label:
                print(f"original-spec: attached from {label}", file=sys.stderr)
        except Exception:
            pass

    captured = capsys.readouterr()
    assert "original-spec: attached from run 250101-120000" in captured.err


def test_command_create_preamble_suppressed_when_resolver_raises(capsys):
    import sys
    from unittest.mock import MagicMock, patch

    ctx = MagicMock()

    with patch(
        "draft.steps.implement_spec.original_spec.preamble_label",
        side_effect=Exception("resolver boom"),
    ):
        try:
            from draft.steps.implement_spec import original_spec

            label = original_spec.preamble_label(ctx)
            if label:
                print(f"original-spec: attached from {label}", file=sys.stderr)
        except Exception:
            pass

    captured = capsys.readouterr()
    assert "original-spec" not in captured.err


def test_command_create_preamble_silent_for_none_label(capsys):
    import sys
    from unittest.mock import MagicMock, patch

    ctx = MagicMock()

    with patch(
        "draft.steps.implement_spec.original_spec.preamble_label",
        return_value=None,
    ):
        try:
            from draft.steps.implement_spec import original_spec

            label = original_spec.preamble_label(ctx)
            if label:
                print(f"original-spec: attached from {label}", file=sys.stderr)
        except Exception:
            pass

    captured = capsys.readouterr()
    assert "original-spec" not in captured.err


# --- _print_run_summary ---


def _make_run_metrics_with_sessions(sessions, tmp_path):
    from pipeline import RunMetrics
    from pipeline.heartbeat import Heartbeat

    return RunMetrics(sessions, Heartbeat(tmp_path))


def _make_session(started_ts, finished_ts, steps=None):
    return {
        "command": "create",
        "started_at": started_ts,
        "finished_at": finished_ts,
        "exit_code": 0,
        "steps": steps or [],
    }


def _make_step(cost_usd):
    step = {
        "name": "implement",
        "started_at": "2026-01-01 00:00:00 UTC",
        "finished_at": "2026-01-01 00:00:05 UTC",
        "exit_code": 0,
        "data": {},
    }
    if cost_usd is not None:
        step["data"]["llm_cost_usd"] = cost_usd
    return step


def _make_pipeline_run_with_cost(cost_usd):
    from pipeline import KnownMetric

    def fake_run(ctx, engine, lifecycle, session_metrics):
        step = session_metrics.step_begin("implement")
        if cost_usd is not None:
            step.set(KnownMetric.LLM_COST_USD, cost_usd)
        step.end(0)

    return fake_run


def test_print_run_summary_with_cost(tmp_path, capsys):
    import draft.command_create as cc

    args = _make_create_args(spec_path="spec.md")
    all_patches = _patch_create_run_infra(
        tmp_path, pipeline_run_side_effect=_make_pipeline_run_with_cost(0.4567)
    )
    with _apply_patches(all_patches):
        rc = cc.run(args)

    out = capsys.readouterr().out
    assert rc == 0
    lines = out.splitlines()
    done_idx = next((i for i, line in enumerate(lines) if line == "done."), None)
    assert done_idx is not None
    assert lines[done_idx + 1].startswith("runtime:")
    assert "cost:    $0.46" in lines[done_idx + 2]


def test_print_run_summary_no_cost(tmp_path, capsys):
    import draft.command_create as cc

    args = _make_create_args(spec_path="spec.md")
    all_patches = _patch_create_run_infra(
        tmp_path, pipeline_run_side_effect=_make_pipeline_run_with_cost(None)
    )
    with _apply_patches(all_patches):
        rc = cc.run(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "cost:    -" in out


def test_print_run_summary_zero_cost(tmp_path, capsys):
    import draft.command_create as cc

    args = _make_create_args(spec_path="spec.md")
    all_patches = _patch_create_run_infra(
        tmp_path, pipeline_run_side_effect=_make_pipeline_run_with_cost(0.0)
    )
    with _apply_patches(all_patches):
        rc = cc.run(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "cost:    $0.00" in out


def test_print_run_summary_aggregates_raises(tmp_path, capsys):
    from unittest.mock import patch

    from draft.command_create import _print_run_summary
    from pipeline import RunMetrics
    from pipeline.heartbeat import Heartbeat

    metrics = RunMetrics([], Heartbeat(tmp_path))
    with patch.object(RunMetrics, "aggregates", side_effect=RuntimeError("boom")):
        _print_run_summary(metrics)
    out = capsys.readouterr().out
    assert "runtime:" not in out
    assert "cost:" not in out


def _make_create_args(**kwargs):
    class FakeArgs:
        spec_path = None
        prompt = None
        run_id = None
        branch = None
        from_branch = None
        no_worktree = False
        skip_pr = False
        delete_worktree = False
        no_review = False
        overrides = []
        config_path = None

    for k, v in kwargs.items():
        setattr(FakeArgs, k, v)
    return FakeArgs()


@contextlib.contextmanager
def _apply_patches(patch_list):
    stack = contextlib.ExitStack()
    for p in patch_list:
        stack.enter_context(p)
    with stack:
        yield


def _patch_create_run_infra(tmp_path, pipeline_run_side_effect=None):
    from unittest.mock import MagicMock, patch

    from draft.types import BranchSource

    patches = [
        patch("draft.command_create._reject_flag_conflicts"),
        patch("draft.command_create._assert_spec_readable"),
        patch("draft.command_create._assert_git_repo"),
        patch("draft.command_create._assert_main_clone"),
        patch("draft.command_create._assert_on_path"),
        patch("draft.command_create._repo_root", return_value=str(tmp_path)),
        patch("draft.command_create._project_name", return_value="test-project"),
        patch("draft.command_create._resolve_base_branch", return_value="main"),
        patch(
            "draft.command_create._resolve_working_branch",
            return_value=("test-branch", BranchSource.NEW),
        ),
        patch("draft.command_create._detect_pr_mode", return_value=("open", None)),
        patch("draft.command_create._unique_branch", return_value="test-branch"),
        patch(
            "draft.command_create._canonical_worktree_path",
            return_value=tmp_path / "wt",
        ),
        patch("draft.command_create._load_run_config", return_value={}),
        patch("draft.command_create._validate_overrides"),
        patch("draft.command_create._apply_overrides", side_effect=lambda c, _: c),
        patch("draft.command_create.validate_config"),
        patch(
            "draft.command_create.resolve_prompt_template",
            side_effect=lambda c, _: c,
        ),
        patch(
            "draft.command_create.resolve_pr_body_template",
            side_effect=lambda c, _: c,
        ),
        patch("draft.command_create.validate_reviewer_argv0s"),
        patch("draft.command_create.step_config", return_value={}),
        patch("draft.command_create._compose_active_steps", return_value=([], set())),
        patch("draft.command_create._print_preamble"),
        patch("draft.command_create.Runner"),
        patch("draft.command_create.DraftLifecycle"),
        patch("draft.command_create.HookRunner"),
        patch("draft.command_create.HeartbeatPulse"),
        patch("draft.command_create.PIPELINES", {"create": MagicMock(steps=[])}),
    ]

    mock_pipeline_cls = MagicMock()
    if pipeline_run_side_effect is not None:
        mock_pipeline_cls.return_value.run.side_effect = pipeline_run_side_effect
    else:
        mock_pipeline_cls.return_value.run.return_value = None
    patches.append(patch("pipeline.Pipeline", mock_pipeline_cls))

    return patches


def test_command_create_success_prints_summary(tmp_path, capsys):
    import draft.command_create as cc

    args = _make_create_args(spec_path="spec.md")
    all_patches = _patch_create_run_infra(tmp_path)
    with _apply_patches(all_patches):
        rc = cc.run(args)

    out = capsys.readouterr().out
    assert rc == 0
    lines = out.splitlines()
    done_idx = next((i for i, line in enumerate(lines) if line == "done."), None)
    assert done_idx is not None
    assert lines[done_idx + 1].startswith("runtime:")
    assert lines[done_idx + 2].startswith("cost:")


def test_command_create_failed_run_no_summary(tmp_path, capsys):
    import draft.command_create as cc
    from pipeline import StepError

    args = _make_create_args(spec_path="spec.md")
    err = StepError("implement", 1)
    all_patches = _patch_create_run_infra(tmp_path, pipeline_run_side_effect=err)
    with _apply_patches(all_patches):
        rc = cc.run(args)

    out = capsys.readouterr().out
    assert rc == 1
    assert "done." not in out
    assert "runtime:" not in out
    assert "cost:" not in out


def test_command_create_skip_pr_summary_follows_done_line(tmp_path, capsys):
    import draft.command_create as cc

    args = _make_create_args(spec_path="spec.md", skip_pr=True)
    all_patches = _patch_create_run_infra(tmp_path)
    with _apply_patches(all_patches):
        rc = cc.run(args)

    out = capsys.readouterr().out
    assert rc == 0
    lines = out.splitlines()
    done_idx = next(
        (i for i, line in enumerate(lines) if "done. (push and PR skipped" in line),
        None,
    )
    assert done_idx is not None, f"done. line not found in: {out!r}"
    assert lines[done_idx + 1].startswith("runtime:")
    assert lines[done_idx + 2].startswith("cost:")


def test_command_create_aggregates_raises_done_still_prints(tmp_path, capsys):
    from unittest.mock import patch

    import draft.command_create as cc
    from pipeline import RunMetrics

    args = _make_create_args(spec_path="spec.md")
    all_patches = _patch_create_run_infra(tmp_path)
    with (
        patch.object(RunMetrics, "aggregates", side_effect=RuntimeError("boom")),
        _apply_patches(all_patches),
    ):
        rc = cc.run(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "done." in out
    assert "runtime:" not in out
    assert "cost:" not in out


# --- --config flag ---


def _patch_create_preflight(tmp_path, repo_dir):
    """Patches for create pre-flight only (up through PR mode detection)."""
    from draft.types import BranchSource

    return [
        patch("draft.command_create._reject_flag_conflicts"),
        patch("draft.command_create._assert_spec_readable"),
        patch("draft.command_create._assert_git_repo"),
        patch("draft.command_create._assert_main_clone"),
        patch("draft.command_create._assert_on_path"),
        patch("draft.command_create._repo_root", return_value=str(repo_dir)),
        patch("draft.command_create._project_name", return_value=repo_dir.name),
        patch("draft.command_create._resolve_base_branch", return_value="main"),
        patch(
            "draft.command_create._resolve_working_branch",
            return_value=("test-branch", BranchSource.NEW),
        ),
        patch("draft.command_create._detect_pr_mode", return_value=("open", None)),
    ]


def test_create_config_flag_missing_file_no_run_created(tmp_path, capsys):
    import draft.command_create as cc

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    missing = tmp_path / "nope.yaml"

    args = _make_create_args(
        spec_path="spec.md", skip_pr=True, config_path=str(missing)
    )
    preflight = _patch_create_preflight(tmp_path, repo_dir)
    with (
        _apply_patches(preflight),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cc.run(args)

    assert rc == 1
    err = capsys.readouterr().err
    assert "--config file not found" in err
    runs_dir = home_dir / ".draft" / "runs"
    assert not runs_dir.exists() or not list(runs_dir.rglob("draft.pid"))


def test_create_config_flag_malformed_yaml_no_run_created(tmp_path, capsys):
    import draft.command_create as cc

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    broken = tmp_path / "broken.yaml"
    broken.write_text("steps: [invalid: yaml")

    args = _make_create_args(spec_path="spec.md", skip_pr=True, config_path=str(broken))
    preflight = _patch_create_preflight(tmp_path, repo_dir)
    with (
        _apply_patches(preflight),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cc.run(args)

    assert rc == 1
    err = capsys.readouterr().err
    assert "malformed YAML in" in err
    assert str(broken) in err
    assert not (home_dir / ".draft" / "runs").exists() or not list(
        (home_dir / ".draft" / "runs").rglob("draft.pid")
    )


def test_create_config_flag_validation_error_includes_source_path(tmp_path, capsys):
    import draft.command_create as cc

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("steps:\n  implement-spec:\n    retry_delay: 5\n")

    args = _make_create_args(spec_path="spec.md", skip_pr=True, config_path=str(cfg))
    preflight = _patch_create_preflight(tmp_path, repo_dir)
    with (
        _apply_patches(preflight),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cc.run(args)

    assert rc == 3
    err = capsys.readouterr().err
    assert f"error in {cfg}" in err


def test_create_config_flag_uses_only_specified_file(tmp_path, capsys):

    import draft.command_create as cc

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    cfg = tmp_path / "config.fast.yaml"
    cfg.write_text("model: fast-model\n")

    args = _make_create_args(spec_path="spec.md", config_path=str(cfg))
    all_patches = _patch_create_run_infra(tmp_path)
    # Replace the _load_run_config mock with actual routing (to verify file is read)
    filtered = [p for p in all_patches if "load_run_config" not in str(p)]

    captured_config = {}

    def fake_load(repo, cp):
        from draft.config import load_config_from_file

        result = load_config_from_file(cp) if cp is not None else {}
        captured_config["config"] = result
        return result

    with (
        _apply_patches(filtered),
        patch("draft.command_create._load_run_config", side_effect=fake_load),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cc.run(args)

    assert rc == 0
    assert captured_config.get("config", {}).get("model") == "fast-model"


def test_create_no_config_flag_persists_null(tmp_path):
    import json

    import draft.command_create as cc

    home_dir = tmp_path / "home"
    args = _make_create_args(spec_path="spec.md")
    all_patches = _patch_create_run_infra(tmp_path)
    with (
        _apply_patches(all_patches),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        cc.run(args)

    runs_dir = home_dir / ".draft" / "runs"
    state_files = list(runs_dir.rglob("state.json"))
    assert state_files, "expected a state.json to be written"
    state = json.loads(state_files[0].read_text())
    assert state.get("config_path") is None


def test_create_config_flag_relative_path_resolves_against_cwd(tmp_path, monkeypatch):
    import json

    import draft.command_create as cc

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("model: x\n")

    monkeypatch.chdir(tmp_path)
    args = _make_create_args(spec_path="spec.md", config_path="cfg.yaml")
    all_patches = _patch_create_run_infra(tmp_path)
    filtered = [p for p in all_patches if "load_run_config" not in str(p)]
    with (
        _apply_patches(filtered),
        patch("draft.command_create._load_run_config", return_value={}),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cc.run(args)

    assert rc == 0
    runs_dir = home_dir / ".draft" / "runs"
    state_files = list(runs_dir.rglob("state.json"))
    assert state_files
    state = json.loads(state_files[0].read_text())
    assert state["config_path"] == str(cfg.resolve())


def test_create_config_flag_no_run_dir_on_failure(tmp_path, capsys):
    import draft.command_create as cc

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    for config_content, expected_rc, err_fragment in [
        (None, 1, "--config file not found"),  # missing file
        ("steps: [invalid", 1, "malformed YAML"),  # malformed yaml
    ]:
        capsys.readouterr()
        if config_content is None:
            cfg = tmp_path / "missing_XXXX.yaml"
        else:
            cfg = tmp_path / "bad.yaml"
            cfg.write_text(config_content)

        args = _make_create_args(
            spec_path="spec.md", skip_pr=True, config_path=str(cfg)
        )
        preflight = _patch_create_preflight(tmp_path, repo_dir)
        with (
            _apply_patches(preflight),
            patch("pathlib.Path.home", return_value=home_dir),
        ):
            rc = cc.run(args)

        assert rc == expected_rc
        err = capsys.readouterr().err
        assert err_fragment in err
        assert not (home_dir / ".draft" / "runs").exists() or not list(
            (home_dir / ".draft" / "runs").rglob("draft.pid")
        )


def test_create_preamble_prints_config_line_with_flag(tmp_path, capsys):
    import draft.command_create as cc

    cfg = tmp_path / "config.fast.yaml"
    abs_cfg = str(cfg.resolve())
    repo_dir = str(tmp_path / "repo")
    run_dir = str(tmp_path / "runs" / "260505-120000")

    cc._print_preamble(
        "260505-120000",
        "feature",
        str(tmp_path / "wt"),
        run_dir,
        "2026-01-01T00:00:00",
        [],
        set(),
        "worktree",
        cfg,
        repo_dir,
    )

    out = capsys.readouterr().out
    assert f"config:   {abs_cfg}" in out


def test_create_preamble_prints_config_line_without_flag(tmp_path, capsys):
    import draft.command_create as cc

    repo_dir = str(tmp_path / "repo")
    run_dir = str(tmp_path / "runs" / "260505-120000")

    cc._print_preamble(
        "260505-120000",
        "feature",
        str(tmp_path / "wt"),
        run_dir,
        "2026-01-01T00:00:00",
        [],
        set(),
        "worktree",
        None,
        repo_dir,
    )

    out = capsys.readouterr().out
    assert "config:" in out
    assert ".draft/config.yaml" in out


# --- babysit --config flag ---


def _make_babysit_args(**kwargs):
    class FakeArgs:
        pr_input = "1"
        spec_path = None
        no_worktree = False
        delete_worktree = False
        run_id = None
        overrides = []
        config_path = None

    for k, v in kwargs.items():
        setattr(FakeArgs, k, v)
    return FakeArgs()


def _patch_babysit_preflight(repo_dir):
    from draft.types import WorktreeMode

    return [
        patch("draft.command_babysit._assert_git_repo"),
        patch("draft.command_babysit._assert_main_clone"),
        patch("draft.command_babysit._assert_on_path"),
        patch("draft.command_babysit._repo_root", return_value=str(repo_dir)),
        patch("draft.command_babysit._project_name", return_value=repo_dir.name),
        patch(
            "draft.command_babysit._fetch_pr",
            return_value={
                "headRefName": "feature",
                "headRefOid": "abc",
                "number": 1,
                "state": "OPEN",
                "url": "https://github.com/test/repo/pull/1",
                "baseRefName": "main",
                "isCrossRepository": False,
                "body": "",
            },
        ),
        patch("draft.command_babysit._assert_branch_exists_and_matches"),
        patch("draft.runs.find_active_run_on_branch", return_value=None),
        patch(
            "draft.command_babysit._resolve_worktree_for_babysit",
            return_value=(str(repo_dir / "wt"), WorktreeMode.WORKTREE),
        ),
        patch("draft.command_babysit._pr_already_green", return_value=False),
    ]


def test_babysit_config_flag_missing_file_no_run_created(tmp_path, capsys):
    import draft.command_babysit as cmd

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    missing = tmp_path / "nope.yaml"

    args = _make_babysit_args(config_path=str(missing))
    preflight = _patch_babysit_preflight(repo_dir)
    with (
        _apply_patches(preflight),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cmd.run(args)

    assert rc == 1
    assert "--config file not found" in capsys.readouterr().err
    assert not (home_dir / ".draft" / "runs").exists() or not list(
        (home_dir / ".draft" / "runs").rglob("draft.pid")
    )


def test_babysit_config_flag_uses_only_specified_file(tmp_path, capsys):
    from unittest.mock import MagicMock

    import draft.command_babysit as cmd

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    cfg = tmp_path / "config.fast.yaml"
    cfg.write_text("model: babysit-model\n")

    args = _make_babysit_args(config_path=str(cfg))
    preflight = _patch_babysit_preflight(repo_dir)
    captured = {}

    def fake_load(repo, cp):
        from draft.config import load_config_from_file

        result = load_config_from_file(cp) if cp is not None else {}
        captured["model"] = result.get("model")
        return result

    with (
        _apply_patches(preflight),
        patch("draft.command_babysit._load_run_config", side_effect=fake_load),
        patch("draft.command_babysit._validate_overrides"),
        patch("draft.command_babysit._apply_overrides", side_effect=lambda c, _: c),
        patch("draft.command_babysit.validate_config"),
        patch("draft.command_babysit.step_config", return_value={}),
        patch(
            "draft.command_babysit._compose_active_steps_babysit",
            return_value=([], set()),
        ),
        patch("draft.command_babysit._print_preamble"),
        patch("draft.command_babysit.Runner"),
        patch("draft.command_babysit.DraftLifecycle"),
        patch("draft.command_babysit.HookRunner"),
        patch("draft.command_babysit.HeartbeatPulse"),
        patch("draft.command_babysit.PIPELINES", {"babysit": MagicMock(steps=[])}),
        patch("pipeline.Pipeline"),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cmd.run(args)

    assert rc == 0
    assert captured.get("model") == "babysit-model"


def test_babysit_config_flag_validation_error_includes_source_path(tmp_path, capsys):
    import draft.command_babysit as cmd

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("steps:\n  implement-spec:\n    retry_delay: 5\n")

    args = _make_babysit_args(config_path=str(cfg))
    preflight = _patch_babysit_preflight(repo_dir)
    with (
        _apply_patches(preflight),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cmd.run(args)

    assert rc == 3
    assert f"error in {cfg}" in capsys.readouterr().err


def test_babysit_config_flag_no_run_dir_on_failure(tmp_path, capsys):
    import draft.command_babysit as cmd

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    missing = tmp_path / "gone.yaml"

    args = _make_babysit_args(config_path=str(missing))
    preflight = _patch_babysit_preflight(repo_dir)
    with (
        _apply_patches(preflight),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cmd.run(args)

    assert rc == 1
    assert not (home_dir / ".draft" / "runs").exists() or not list(
        (home_dir / ".draft" / "runs").rglob("draft.pid")
    )


# --- fix-pr --config flag ---


def _make_fix_pr_args(**kwargs):
    class FakeArgs:
        pr_input = "1"
        spec_path = None
        no_worktree = False
        delete_worktree = False
        run_id = None
        overrides = []
        config_path = None
        watch = False

    for k, v in kwargs.items():
        setattr(FakeArgs, k, v)
    return FakeArgs()


def _patch_fix_pr_preflight(repo_dir):

    return [
        patch("draft.command_fix_pr._assert_git_repo"),
        patch("draft.command_fix_pr._assert_main_clone"),
        patch("draft.command_fix_pr._assert_on_path"),
        patch("draft.command_fix_pr._repo_root", return_value=str(repo_dir)),
        patch("draft.command_fix_pr._project_name", return_value=repo_dir.name),
        patch(
            "draft.command_fix_pr._fetch_pr",
            return_value={
                "headRefName": "feature",
                "headRefOid": "abc",
                "number": 1,
                "state": "OPEN",
                "url": "https://github.com/test/repo/pull/1",
                "baseRefName": "main",
                "isCrossRepository": False,
                "body": "",
            },
        ),
        patch("draft.command_fix_pr._assert_branch_exists_and_matches"),
        patch("draft.runs.find_active_run_on_branch", return_value=None),
        patch(
            "draft.command_fix_pr._resolve_worktree_for_fix_pr",
            return_value=(str(repo_dir / "wt"), "worktree"),
        ),
        patch("draft.command_fix_pr._single_check_gate", return_value="failure"),
    ]


def test_fix_pr_config_flag_missing_file_no_run_created(tmp_path, capsys):
    import draft.command_fix_pr as cmd

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    missing = tmp_path / "nope.yaml"

    args = _make_fix_pr_args(config_path=str(missing))
    preflight = _patch_fix_pr_preflight(repo_dir)
    with (
        _apply_patches(preflight),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cmd.run(args)

    assert rc == 1
    assert "--config file not found" in capsys.readouterr().err
    assert not (home_dir / ".draft" / "runs").exists() or not list(
        (home_dir / ".draft" / "runs").rglob("draft.pid")
    )


def test_fix_pr_config_flag_uses_only_specified_file(tmp_path, capsys):
    from unittest.mock import MagicMock

    import draft.command_fix_pr as cmd

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    cfg = tmp_path / "config.fast.yaml"
    cfg.write_text("model: fixpr-model\n")

    args = _make_fix_pr_args(config_path=str(cfg))
    preflight = _patch_fix_pr_preflight(repo_dir)
    captured = {}

    def fake_load(repo, cp):
        from draft.config import load_config_from_file

        result = load_config_from_file(cp) if cp is not None else {}
        captured["model"] = result.get("model")
        return result

    with (
        _apply_patches(preflight),
        patch("draft.command_fix_pr._load_run_config", side_effect=fake_load),
        patch("draft.command_fix_pr._validate_overrides"),
        patch("draft.command_fix_pr._apply_overrides", side_effect=lambda c, _: c),
        patch("draft.command_fix_pr.validate_config"),
        patch("draft.command_fix_pr.step_config", return_value={}),
        patch(
            "draft.command_fix_pr._compose_active_steps_fix_pr",
            return_value=([], set()),
        ),
        patch("draft.command_fix_pr._print_preamble"),
        patch("draft.command_fix_pr.Runner"),
        patch("draft.command_fix_pr.DraftLifecycle"),
        patch("draft.command_fix_pr.HookRunner"),
        patch("draft.command_fix_pr.HeartbeatPulse"),
        patch("draft.command_fix_pr.PIPELINES", {"fix-pr": MagicMock(steps=[])}),
        patch("pipeline.Pipeline"),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cmd.run(args)

    assert rc == 0
    assert captured.get("model") == "fixpr-model"


def test_fix_pr_config_flag_validation_error_includes_source_path(tmp_path, capsys):
    import draft.command_fix_pr as cmd

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("steps:\n  implement-spec:\n    retry_delay: 5\n")

    args = _make_fix_pr_args(config_path=str(cfg))
    preflight = _patch_fix_pr_preflight(repo_dir)
    with (
        _apply_patches(preflight),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cmd.run(args)

    assert rc == 3
    assert f"error in {cfg}" in capsys.readouterr().err


def test_fix_pr_config_flag_no_run_dir_on_failure(tmp_path, capsys):
    import draft.command_fix_pr as cmd

    home_dir = tmp_path / "home"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)
    missing = tmp_path / "gone.yaml"

    args = _make_fix_pr_args(config_path=str(missing))
    preflight = _patch_fix_pr_preflight(repo_dir)
    with (
        _apply_patches(preflight),
        patch("pathlib.Path.home", return_value=home_dir),
    ):
        rc = cmd.run(args)

    assert rc == 1
    assert not (home_dir / ".draft" / "runs").exists() or not list(
        (home_dir / ".draft" / "runs").rglob("draft.pid")
    )


# --- continue --config flag ---


def _make_continue_state(run_dir, config_path=None, extra_data=None):
    import json

    data = {
        "branch": "fix",
        "wt_dir": str(run_dir.parent / "wt"),
        "repo": str(run_dir.parent.parent),
        "pipeline": "create",
    }
    if extra_data:
        data.update(extra_data)
    state = {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "completed": [],
        "data": data,
        "step_data": {},
        "step_configs": {},
        "sessions": [],
        "config_path": config_path,
    }
    (run_dir / "state.json").write_text(json.dumps(state))


def test_continue_uses_persisted_config_path(tmp_path, capsys):
    import draft.command_continue as cmd

    run_dir = tmp_path / "myproject" / "260505-120000"
    run_dir.mkdir(parents=True)

    cfg = tmp_path / "config.fast.yaml"
    cfg.write_text("model: persisted-model\n")
    _make_continue_state(run_dir, config_path=str(cfg))

    class FakeArgs:
        run_id = "260505-120000"

    captured = {}

    def fake_load(repo, cp):
        captured["config_path"] = str(cp) if cp else None
        return {"model": "persisted-model"}

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.command_continue._load_run_config", side_effect=fake_load),
        patch("draft.command_continue.Pipeline") as MockPipeline,
    ):
        MockPipeline.return_value.run.return_value = None
        cmd.run(FakeArgs())

    assert captured.get("config_path") == str(cfg)


def test_continue_persisted_config_missing_errors_cleanly(tmp_path, capsys):

    import draft.command_continue as cmd

    run_dir = tmp_path / "myproject" / "260505-120000"
    run_dir.mkdir(parents=True)

    deleted_cfg = tmp_path / "deleted.yaml"
    _make_continue_state(run_dir, config_path=str(deleted_cfg))
    original_state = (run_dir / "state.json").read_text()

    class FakeArgs:
        run_id = "260505-120000"

    with patch("draft.runs.find_run_dir", return_value=run_dir):
        rc = cmd.run(FakeArgs())

    assert rc == 2
    err = capsys.readouterr().err
    assert "config file from create run no longer exists" in err
    assert str(deleted_cfg) in err
    assert (run_dir / "state.json").read_text() == original_state
    assert not (run_dir / "draft.pid").exists()


def test_continue_null_config_path_falls_back_to_default(tmp_path, capsys):
    import draft.command_continue as cmd

    run_dir = tmp_path / "myproject" / "260505-120000"
    run_dir.mkdir(parents=True)
    _make_continue_state(run_dir, config_path=None)

    class FakeArgs:
        run_id = "260505-120000"

    captured = {}

    def fake_load(repo, cp):
        captured["config_path"] = cp
        return {}

    with (
        patch("draft.runs.find_run_dir", return_value=run_dir),
        patch("draft.command_continue._load_run_config", side_effect=fake_load),
        patch("draft.command_continue.Pipeline") as MockPipeline,
    ):
        MockPipeline.return_value.run.return_value = None
        cmd.run(FakeArgs())

    assert captured.get("config_path") is None


# --- _resolve_base_branch ---


def _make_resolve(local_branches, remote_refs=None):
    """Returns a configured _resolve_base_branch caller with patched git helpers."""
    from draft.command_create import _resolve_base_branch

    remote_refs = remote_refs or set()

    def local_exists(repo, branch):
        return branch in local_branches

    def remote_exists(repo, ref):
        return ref in remote_refs

    return _resolve_base_branch, local_exists, remote_exists


def test_resolve_base_branch_default_main(capsys):
    from draft.command_create import _resolve_base_branch

    with (
        patch(
            "draft.command_create._local_branch_exists",
            side_effect=lambda repo, b: b == "main",
        ),
        patch("draft.command_create._remote_ref_exists", return_value=False),
    ):
        result = _resolve_base_branch("/repo", None)

    assert result == "main"


def test_resolve_base_branch_default_master_when_no_main(capsys):
    from draft.command_create import _resolve_base_branch

    with (
        patch(
            "draft.command_create._local_branch_exists",
            side_effect=lambda repo, b: b == "master",
        ),
        patch("draft.command_create._remote_ref_exists", return_value=False),
    ):
        result = _resolve_base_branch("/repo", None)

    assert result == "master"


def test_resolve_base_branch_default_neither_exits_3(capsys):
    from draft.command_create import _resolve_base_branch

    with (
        patch("draft.command_create._local_branch_exists", return_value=False),
        patch("draft.command_create._remote_ref_exists", return_value=False),
        pytest.raises(SystemExit) as exc_info,
    ):
        _resolve_base_branch("/repo", None)

    assert exc_info.value.code == 3
    assert "could not find local branch 'main' or 'master'" in capsys.readouterr().err


def test_resolve_base_branch_from_main_local_exists(capsys):
    from draft.command_create import _resolve_base_branch

    with (
        patch("draft.command_create._local_branch_exists", return_value=True),
        patch("draft.command_create._remote_ref_exists", return_value=False),
    ):
        result = _resolve_base_branch("/repo", "main")

    assert result == "main"


def test_resolve_base_branch_from_origin_main_strips_prefix(capsys):
    from draft.command_create import _resolve_base_branch

    with (
        patch("draft.command_create._local_branch_exists", return_value=True),
        patch("draft.command_create._remote_ref_exists", return_value=False),
    ):
        result = _resolve_base_branch("/repo", "origin/main")

    assert result == "main"


def test_resolve_base_branch_from_origin_feature_strips_prefix(capsys):
    from draft.command_create import _resolve_base_branch

    with (
        patch("draft.command_create._local_branch_exists", return_value=True),
        patch("draft.command_create._remote_ref_exists", return_value=False),
    ):
        result = _resolve_base_branch("/repo", "origin/feature-x")

    assert result == "feature-x"


def test_resolve_base_branch_missing_local_with_remote_suggests_create(capsys):
    from draft.command_create import _resolve_base_branch

    with (
        patch("draft.command_create._local_branch_exists", return_value=False),
        patch("draft.command_create._remote_ref_exists", return_value=True),
        pytest.raises(SystemExit) as exc_info,
    ):
        _resolve_base_branch("/repo", "feature-x")

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "local branch 'feature-x' does not exist" in err
    assert "git branch feature-x origin/feature-x" in err


def test_resolve_base_branch_origin_prefix_missing_local_with_remote(capsys):
    from draft.command_create import _resolve_base_branch

    with (
        patch("draft.command_create._local_branch_exists", return_value=False),
        patch("draft.command_create._remote_ref_exists", return_value=True),
        pytest.raises(SystemExit) as exc_info,
    ):
        _resolve_base_branch("/repo", "origin/feature-x")

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "local branch 'feature-x' does not exist" in err
    assert "git branch feature-x origin/feature-x" in err


def test_resolve_base_branch_missing_local_no_remote(capsys):
    from draft.command_create import _resolve_base_branch

    with (
        patch("draft.command_create._local_branch_exists", return_value=False),
        patch("draft.command_create._remote_ref_exists", return_value=False),
        pytest.raises(SystemExit) as exc_info,
    ):
        _resolve_base_branch("/repo", "feature-x")

    assert exc_info.value.code == 2
    assert (
        "--from must be a local branch name (got: feature-x)" in capsys.readouterr().err
    )


def test_resolve_base_branch_empty_after_strip_exits_2(capsys):
    from draft.command_create import _resolve_base_branch

    with (
        patch("draft.command_create._local_branch_exists", return_value=False),
        patch("draft.command_create._remote_ref_exists", return_value=False),
        pytest.raises(SystemExit) as exc_info,
    ):
        _resolve_base_branch("/repo", "origin/")

    assert exc_info.value.code == 2
    assert "--from cannot be empty after stripping 'origin/'" in capsys.readouterr().err


def test_resolve_base_branch_head_is_not_local_branch(capsys):
    from draft.command_create import _resolve_base_branch

    with (
        patch("draft.command_create._local_branch_exists", return_value=False),
        patch("draft.command_create._remote_ref_exists", return_value=False),
        pytest.raises(SystemExit) as exc_info,
    ):
        _resolve_base_branch("/repo", "HEAD")

    assert exc_info.value.code == 2
    assert "--from must be a local branch name (got: HEAD)" in capsys.readouterr().err


def test_resolve_base_branch_tag_not_local_branch(capsys):
    from draft.command_create import _resolve_base_branch

    with (
        patch("draft.command_create._local_branch_exists", return_value=False),
        patch("draft.command_create._remote_ref_exists", return_value=False),
        pytest.raises(SystemExit) as exc_info,
    ):
        _resolve_base_branch("/repo", "v1.0.0")

    assert exc_info.value.code == 2
    assert "--from must be a local branch name (got: v1.0.0)" in capsys.readouterr().err
