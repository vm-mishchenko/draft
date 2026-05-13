import json
import os
from pathlib import Path

from draft import runs
from draft.runs import runs_base


def register(subparsers):
    p = subparsers.add_parser("list", help="List the 15 most recent runs.")
    p.add_argument("--json", action="store_true", default=False, help="Emit JSON.")
    p.set_defaults(func=run)


def _workspace_status(wt_dir: str) -> str:
    if not wt_dir:
        return "-"
    try:
        return "yes" if Path(wt_dir).is_dir() else "no"
    except OSError:
        return "-"


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

    dirs = []
    for project_dir in base.iterdir():
        if not project_dir.is_dir():
            continue
        for run_dir in project_dir.iterdir():
            if run_dir.is_dir():
                dirs.append(run_dir)

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

    header = f"{'RUN-ID':<18}  {'PROJECT':<20}  {'STAGES':<10}  {'RUNNING':<8}  {'WORKSPACE':<10}  {'BRANCH':<30}  PR"
    print(header)
    print("-" * len(header))

    for d in dirs:
        row = _row_data(d)
        running_str = "yes" if row["running"] else "-"
        workspace_str = row["workspace"] if row["workspace"] is not None else "-"
        branch_str = row["branch"] or "-"
        pr_str = row["pr_url"] or "-"
        if row["state"] == "missing":
            stages_str = "-"
        elif row["state"] == "corrupt":
            stages_str = "corrupt"
        else:
            stages_str = f"{row['stages_completed']}/{row['stages_total']}"
        print(
            f"{row['run_id']:<18}  {row['project']:<20}  {stages_str:<10}  {running_str:<8}  {workspace_str:<10}  {branch_str:<30}  {pr_str}"
        )

    return 0
