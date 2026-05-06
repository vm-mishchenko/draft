import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from draft import runs


def register(subparsers):
    p = subparsers.add_parser("delete", help="Remove a run's state and git worktree.")
    p.add_argument("run_id", help="Run ID to delete.")
    p.add_argument(
        "--delete-branch",
        action="store_true",
        help="Also delete the git branch associated with the run.",
    )
    p.set_defaults(func=run)


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def run(args) -> int:
    run_dir = runs.find_run_dir(args.run_id)
    if run_dir is None:
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

    # Load worktree path and branch from state.json
    state_path = run_dir / "state.json"
    wt_dir = None
    branch = None
    repo = None
    if state_path.exists():
        try:
            payload = json.loads(state_path.read_text())
            data = payload.get("data", {})
            wt_dir = data.get("wt_dir")
            branch = data.get("branch")
            repo = data.get("repo")
        except Exception:
            pass

    # Remove git worktree
    if wt_dir and Path(wt_dir).exists():
        subprocess.run(
            ["git", "worktree", "remove", wt_dir, "--force"],
            capture_output=True,
        )

    # Optionally delete branch
    if getattr(args, "delete_branch", False):
        if branch and repo and Path(repo).exists():
            result = subprocess.run(
                ["git", "branch", "-D", branch],
                capture_output=True, text=True, cwd=repo,
            )
            if result.returncode == 0:
                print(f"deleted branch {branch}")
            else:
                stderr = result.stderr.strip() or result.stdout.strip()
                print(
                    f"warning: failed to delete branch '{branch}': {stderr}",
                    file=sys.stderr,
                )
        else:
            print(
                "warning: --delete-branch requested but branch or repo missing from state",
                file=sys.stderr,
            )

    # Remove run directory
    shutil.rmtree(run_dir)
    print(f"deleted run {args.run_id}")
    return 0
