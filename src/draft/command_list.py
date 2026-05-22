import json
import os
import subprocess
import sys
from pathlib import Path

from draft import runs
from draft.runs import runs_base


class _ListProjectError(Exception):
    pass


def register(subparsers):
    p = subparsers.add_parser("list", help="List the 15 most recent runs.")
    p.add_argument("--json", action="store_true", default=False, help="Emit JSON.")
    p.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="List runs across all projects.",
    )
    p.set_defaults(func=run)


def _run_git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _current_project_name_for_list() -> str | None:
    result = _run_git(["git", "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "not a git repository" in stderr or "not a git repo" in stderr:
            return None
        raise _ListProjectError(
            f"git rev-parse failed: {stderr or result.stdout.strip()}"
        )

    wt_result = _run_git(["git", "worktree", "list", "--porcelain"])
    if wt_result.returncode != 0:
        raise _ListProjectError(f"git worktree list failed: {wt_result.stderr.strip()}")

    main_worktree = None
    for line in wt_result.stdout.splitlines():
        if line.startswith("worktree "):
            main_worktree = line[len("worktree ") :].strip()
            break

    if main_worktree is None:
        raise _ListProjectError(
            "could not determine main worktree from git worktree list"
        )

    return Path(main_worktree).name


def _all_run_dirs(base: Path) -> list[Path]:
    dirs = []
    for project_dir in base.iterdir():
        if not project_dir.is_dir():
            continue
        for run_dir in project_dir.iterdir():
            if run_dir.is_dir():
                dirs.append(run_dir)
    return dirs


def _project_run_dirs(base: Path, project: str) -> list[Path]:
    project_dir = base / project
    if not project_dir.exists():
        return []
    return [run_dir for run_dir in project_dir.iterdir() if run_dir.is_dir()]


def _selected_run_dirs(base: Path, args) -> list[Path] | int:
    if getattr(args, "all", False):
        return _all_run_dirs(base)

    try:
        project = _current_project_name_for_list()
    except _ListProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if project is None:
        return _all_run_dirs(base)
    return _project_run_dirs(base, project)


def _workspace_status(wt_dir: str) -> str:
    if not wt_dir:
        return "-"
    try:
        return "yes" if Path(wt_dir).is_dir() else "no"
    except OSError:
        return "-"


def _load_state_payload_for_display(run_dir: Path) -> dict | None:
    try:
        return json.loads((run_dir / "state.json").read_text())
    except Exception:
        return None


def _workspace_display(wt_dir: str | None) -> str:
    if not wt_dir:
        return "-"
    try:
        return str(wt_dir) if Path(wt_dir).is_dir() else "(deleted)"
    except OSError:
        return "(deleted)"


def _format_run_line(row: dict) -> str:
    if row["state"] == "missing":
        parts = ["missing"]
    elif row["state"] == "corrupt":
        parts = ["corrupt"]
    else:
        parts = [f"{row['stages_completed']}/{row['stages_total']}"]
    if row["running"]:
        parts.append("running")
    return f"Run: {row['run_id']} ({', '.join(parts)})"


def _print_human_record(run_dir: Path) -> None:
    row = _row_data(run_dir)
    payload = _load_state_payload_for_display(run_dir)
    wt_dir = (payload or {}).get("data", {}).get("wt_dir") or None
    print(_format_run_line(row))
    print(f"Project: {row['project']}")
    print(f"Branch: {row['branch'] or '-'}")
    print(f"PR: {row['pr_url'] or '-'}")
    print(f"Workspace: {_workspace_display(wt_dir)}")
    print(f"Logs: {run_dir}")


def _row_data(run_dir: Path) -> dict:
    running = _is_run_active(run_dir)
    run_id = run_dir.name
    project = run_dir.parent.name
    state_path = run_dir / "state.json"
    if not state_path.exists():
        return {
            "run_id": run_id,
            "project": project,
            "state": "missing",
            "stages_completed": None,
            "stages_total": None,
            "running": running,
            "workspace": None,
            "branch": None,
            "pr_url": None,
        }
    try:
        payload = json.loads(state_path.read_text())
    except Exception:
        return {
            "run_id": run_id,
            "project": project,
            "state": "corrupt",
            "stages_completed": None,
            "stages_total": None,
            "running": running,
            "workspace": None,
            "branch": None,
            "pr_url": None,
        }
    from draft.pipelines import CorruptStateError

    project = payload.get("data", {}).get("project", project) or project
    try:
        stages_total = len(runs.expected_steps(payload))
    except CorruptStateError:
        return {
            "run_id": run_id,
            "project": project,
            "state": "corrupt",
            "stages_completed": None,
            "stages_total": None,
            "running": running,
            "workspace": None,
            "branch": None,
            "pr_url": None,
        }
    stages_completed = len(payload.get("completed", []))
    branch = payload.get("data", {}).get("branch") or None
    pr_url = payload.get("data", {}).get("pr_url") or None
    wt_dir = payload.get("data", {}).get("wt_dir") or ""
    ws = _workspace_status(wt_dir)
    workspace = None if ws == "-" else ws
    return {
        "run_id": run_id,
        "project": project,
        "state": "ok",
        "stages_completed": stages_completed,
        "stages_total": stages_total,
        "running": running,
        "workspace": workspace,
        "branch": branch,
        "pr_url": pr_url,
    }


def _is_run_active(run_dir: Path) -> bool:
    pid_file = run_dir / "draft.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def run(args) -> int:
    base = runs_base()
    if not base.exists():
        if getattr(args, "json", False):
            print(json.dumps([], indent=2))
        else:
            print("no runs")
        return 0

    result = _selected_run_dirs(base, args)
    if isinstance(result, int):
        return result
    dirs = result

    dirs = sorted(dirs, key=lambda d: d.name, reverse=False)[-15:]

    if not dirs:
        if getattr(args, "json", False):
            print(json.dumps([], indent=2))
        else:
            print("no runs")
        return 0

    if getattr(args, "json", False):
        rows = [_row_data(d) for d in dirs]
        print(json.dumps(rows, indent=2))
        return 0

    for index, d in enumerate(dirs):
        if index > 0:
            print()
        _print_human_record(d)

    return 0
