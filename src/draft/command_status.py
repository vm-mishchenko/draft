import json
import sys

from draft import runs


def register(subparsers):
    p = subparsers.add_parser(
        "status",
        help="Show the status of a run.",
        description="Show the status of a run and its steps.",
    )
    p.add_argument("run_id", help="Run ID to inspect.")
    p.set_defaults(func=run)


def run(args) -> int:
    run_dir = runs.find_run_dir(args.run_id)
    if run_dir is None:
        print(f"error: run '{args.run_id}' not found", file=sys.stderr)
        return 1

    state_path = run_dir / "state.json"
    if not state_path.exists():
        print(f"run-id:  {args.run_id}")
        print(f"project: {run_dir.parent.name}")
        print("status:  unknown")
        return 0

    try:
        state = json.loads(state_path.read_text())
    except json.JSONDecodeError:
        print(f"error: state.json for run '{args.run_id}' is corrupt", file=sys.stderr)
        return 1

    data = state.get("data", {})

    if runs.is_run_finished(state):
        run_status = "done"
    elif runs.is_run_active(run_dir):
        run_status = "running"
    else:
        run_status = "stopped"

    print(f"run-id:   {args.run_id}")
    print(f"project:  {run_dir.parent.name}")
    print(f"branch:   {data.get('branch', '-')}")
    print(f"status:   {run_status}")
    print(f"worktree: {data.get('wt_dir') or '-'}")
    pr_url = data.get("pr_url", "")
    if pr_url:
        print(f"pr:       {pr_url}")

    print()
    print(f"{'STEP':<24}{'STATUS'}")

    completed = state.get("completed", [])
    first_unfinished = True
    for step in runs.expected_steps(state):
        if step in completed:
            step_status = "done"
        elif first_unfinished:
            step_status = "active" if run_status == "running" else "stopped"
            first_unfinished = False
        else:
            step_status = "pending"
        print(f"{step:<24}{step_status}")

    return 0
