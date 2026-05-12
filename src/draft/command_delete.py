import sys

from draft import runs


def register(subparsers):
    p = subparsers.add_parser(
        "delete",
        help="Remove a run's state and git worktree.",
        description="Remove a run's state and git worktree. See also: draft prune for bulk deletion.",
    )
    p.add_argument("run_id", help="Run ID to delete.")
    p.add_argument(
        "--delete-branch",
        action="store_true",
        help="Also delete the git branch associated with the run.",
    )
    p.set_defaults(func=run)


def run(args) -> int:
    run_dir = runs.find_run_dir(args.run_id)
    if run_dir is None:
        print(f"error: run '{args.run_id}' not found", file=sys.stderr)
        return 1

    result = runs.delete_run(
        run_dir, delete_branch=getattr(args, "delete_branch", False)
    )

    if result["status"] == "active":
        pid = result["pid"]
        print(
            f"error: run '{args.run_id}' is currently active (pid {pid}). "
            "Stop it before deleting.",
            file=sys.stderr,
        )
        return 3

    for warning in result["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)

    if result["branch_deleted"]:
        print(f"deleted branch {result['branch']}")

    print(f"deleted run {args.run_id}")
    return 0
