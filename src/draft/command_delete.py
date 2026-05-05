import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def register(subparsers):
    p = subparsers.add_parser("delete", help="Remove a run's state and git worktree.")
    p.add_argument("run_id", help="Run ID to delete.")
    p.set_defaults(func=run)


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def run(args) -> int:
    run_dir = Path("/tmp/draft") / args.run_id
    if not run_dir.exists():
        print(f"error: run '{args.run_id}' not found", file=sys.stderr)
        return 1

    # Check if active
    pid_file = run_dir / "draft.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _is_pid_alive(pid):
                print(
                    f"error: run '{args.run_id}' is currently active (pid {pid}). "
                    "Stop it before deleting.",
                    file=sys.stderr,
                )
                return 3
        except ValueError:
            pass

    # Load worktree path from state.json
    state_path = run_dir / "state.json"
    wt_dir = None
    if state_path.exists():
        try:
            payload = json.loads(state_path.read_text())
            wt_dir = payload.get("data", {}).get("wt_dir")
        except Exception:
            pass

    # Remove git worktree
    if wt_dir and Path(wt_dir).exists():
        subprocess.run(
            ["git", "worktree", "remove", wt_dir, "--force"],
            capture_output=True,
        )

    # Remove run directory
    shutil.rmtree(run_dir)
    print(f"deleted run {args.run_id}")
    return 0
