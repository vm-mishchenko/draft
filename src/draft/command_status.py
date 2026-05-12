import json
import sys

from draft import runs
from pipeline import RunMetrics, fmt_duration


def register(subparsers):
    p = subparsers.add_parser(
        "status",
        help="Show the status of a run.",
        description="Show the status of a run and its steps.",
    )
    p.add_argument("run_id", help="Run ID to inspect.")
    p.add_argument("--json", action="store_true", default=False, help="Emit JSON.")
    p.set_defaults(func=run)


def run(args) -> int:
    run_dir = runs.find_run_dir(args.run_id)
    if run_dir is None:
        print(f"error: run '{args.run_id}' not found", file=sys.stderr)
        return 1

    use_json = getattr(args, "json", False)

    state_path = run_dir / "state.json"
    if not state_path.exists():
        if use_json:
            print(
                json.dumps(
                    {
                        "run_id": args.run_id,
                        "project": run_dir.parent.name,
                        "branch": None,
                        "status": "unknown",
                        "worktree": None,
                        "pr_url": None,
                        "steps": None,
                    },
                    indent=2,
                )
            )
        else:
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
    sessions = state.get("sessions", [])
    started_at = sessions[0].get("started_at") if sessions else None
    finished_at = sessions[-1].get("finished_at") if sessions else None
    metrics = RunMetrics(sessions, run_dir)
    agg = metrics.aggregates()
    total_seconds = agg["total_runtime_seconds"]
    total_cost = agg["total_llm_cost_usd"]
    per_step = metrics.per_step_costs()
    per_step_time = metrics.per_step_times()

    if runs.is_run_finished(state):
        run_status = "done"
    elif runs.is_run_active(run_dir):
        run_status = "running"
    else:
        run_status = "stopped"

    completed = state.get("completed", [])
    first_unfinished = True
    step_rows = []
    for step in runs.expected_steps(state):
        if step in completed:
            step_status = "done"
        elif first_unfinished:
            step_status = "active" if run_status == "running" else "stopped"
            first_unfinished = False
        else:
            step_status = "pending"
        step_rows.append(
            {
                "name": step,
                "status": step_status,
                "llm_cost_usd": per_step.get(step),
                "runtime_seconds": per_step_time.get(step),
            }
        )

    worktree = data.get("wt_dir") or None
    pr_url = data.get("pr_url") or None

    if use_json:
        result = {
            "run_id": args.run_id,
            "project": run_dir.parent.name,
            "branch": data.get("branch") or None,
            "status": run_status,
            "worktree": worktree,
            "pr_url": pr_url,
            "logs": str(run_dir),
            "started_at": started_at,
            "finished_at": finished_at,
            "total_runtime_seconds": total_seconds,
            "total_llm_cost_usd": total_cost,
            "steps": step_rows,
        }
        print(json.dumps(result, indent=2))
        return 0

    print(f"run-id:   {args.run_id}")
    print(f"project:  {run_dir.parent.name}")
    print(f"branch:   {data.get('branch', '-')}")
    print(f"status:   {run_status}")
    print(f"worktree: {worktree or '-'}")
    if pr_url:
        print(f"pr:       {pr_url}")
    print(f"logs:          {run_dir}")
    print(f"started:       {started_at or '-'}")
    print(f"finished:      {finished_at or '-'}")
    print(f"total runtime: {fmt_duration(total_seconds)}")
    if total_cost is None:
        print("cost:          -")
    else:
        print(f"cost:          ${total_cost:.2f}")

    print()
    print(f"{'STEP':<24}{'STATUS':<12}{'COST':<10}{'TIME':<10}{'%'}")

    for row in step_rows:
        cost = row["llm_cost_usd"]
        secs = row["runtime_seconds"]
        cost_str = f"${cost:.2f}" if cost is not None else "-"
        if secs is not None:
            time_str = fmt_duration(secs)
            pct_str = (
                f"{int(secs / total_seconds * 100)}%" if total_seconds > 0 else "-"
            )
        else:
            time_str = "-"
            pct_str = "-"
        print(
            f"{row['name']:<24}{row['status']:<12}{cost_str:<10}{time_str:<10}{pct_str}"
        )

    return 0
