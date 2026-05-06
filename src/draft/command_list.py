import json

from draft.runs import runs_base


def register(subparsers):
    p = subparsers.add_parser("list", help="List the 15 most recent runs.")
    p.set_defaults(func=run)


def run(args) -> int:
    base = runs_base()
    if not base.exists():
        print("no runs")
        return 0

    dirs = []
    for project_dir in base.iterdir():
        if not project_dir.is_dir():
            continue
        for run_dir in project_dir.iterdir():
            if run_dir.is_dir():
                dirs.append(run_dir)

    dirs = sorted(dirs, key=lambda d: d.name, reverse=True)[:15]

    if not dirs:
        print("no runs")
        return 0

    total_steps = 6
    header = f"{'RUN-ID':<18}  {'PROJECT':<20}  {'STAGES':<10}  {'BRANCH':<30}  PR"
    print(header)
    print("-" * len(header))

    for d in dirs:
        state_path = d / "state.json"
        if not state_path.exists():
            project = d.parent.name
            print(f"{d.name:<18}  {project:<20}  {'-':<10}  {'-':<30}  -")
            continue
        try:
            payload = json.loads(state_path.read_text())
        except Exception:
            project = d.parent.name
            print(f"{d.name:<18}  {project:<20}  {'corrupt':<10}  {'-':<30}  -")
            continue

        project = payload.get("data", {}).get("project", d.parent.name) or d.parent.name
        completed = len(payload.get("completed", []))
        branch = payload.get("data", {}).get("branch", "-") or "-"
        pr_url = payload.get("data", {}).get("pr_url", "") or "-"
        stages = f"{completed}/{total_steps}"
        print(f"{d.name:<18}  {project:<20}  {stages:<10}  {branch:<30}  {pr_url}")

    return 0
