import json
from pathlib import Path


def register(subparsers):
    p = subparsers.add_parser("list", help="List the 15 most recent runs.")
    p.set_defaults(func=run)


def run(args) -> int:
    base = Path("/tmp/draft")
    if not base.exists():
        print("no runs")
        return 0

    dirs = sorted(
        [d for d in base.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True,
    )[:15]

    if not dirs:
        print("no runs")
        return 0

    total_steps = 6
    header = f"{'RUN-ID':<18}  {'STAGES':<10}  {'BRANCH':<30}  {'PR':<50}  LOGS"
    print(header)
    print("-" * len(header))

    for d in dirs:
        state_path = d / "state.json"
        if not state_path.exists():
            print(f"{d.name:<18}  {'-':<10}  {'-':<30}  {'-':<50}  {d}")
            continue
        try:
            payload = json.loads(state_path.read_text())
        except Exception:
            print(f"{d.name:<18}  {'corrupt':<10}  {'-':<30}  {'-':<50}  {d}")
            continue

        completed = len(payload.get("completed", []))
        branch = payload.get("data", {}).get("branch", "-") or "-"
        pr_url = payload.get("data", {}).get("pr_url", "") or "-"
        stages = f"{completed}/{total_steps}"
        print(f"{d.name:<18}  {stages:<10}  {branch:<30}  {pr_url:<50}  {d}")

    return 0
