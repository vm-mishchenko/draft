import contextlib
import sys
from pathlib import Path

from draft import runs

_STATUS_ORDER = {"done": 0, "stopped": 1, "missing": 2, "corrupt": 3}


def register(subparsers):
    p = subparsers.add_parser(
        "prune",
        help="Bulk-delete non-running runs in the current project.",
        description="Bulk-delete every run in scope except those actively running. By default operates on the current project. See also: draft delete.",
        allow_abbrev=False,
    )
    p.add_argument(
        "--yes", "-y", action="store_true", help="Skip the confirmation prompt."
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the selection and exit without deleting.",
    )
    p.add_argument(
        "--project",
        metavar="NAME",
        help="Operate on the named project instead of the current one.",
    )
    p.add_argument(
        "--all-projects",
        action="store_true",
        help="Operate across every project under ~/.draft/runs/.",
    )
    p.add_argument(
        "--delete-branch",
        action="store_true",
        help="Also delete the local git branch for each pruned run.",
    )
    p.set_defaults(func=run)


def _resolve_project_scope(args) -> list[Path] | int:
    if args.project and args.all_projects:
        print(
            "error: --project and --all-projects are mutually exclusive",
            file=sys.stderr,
        )
        return 2

    if args.all_projects:
        candidates = []
        for name in runs.all_project_names():
            candidates.extend(runs.project_runs(name))
        return candidates

    if args.project:
        project_dir = runs.runs_base() / args.project
        if not project_dir.exists():
            print(
                f"error: project '{args.project}' not found under {runs.runs_base()}",
                file=sys.stderr,
            )
            return 1
        return runs.project_runs(args.project)

    name = runs.current_project_name()
    if name is None:
        print(
            "error: not in a git repo; use --project or --all-projects", file=sys.stderr
        )
        return 1
    return runs.project_runs(name)


def _build_selection(candidates):
    selection = []
    active = []
    for run_dir in candidates:
        status = runs.classify_run(run_dir)
        if status == "running":
            active.append(run_dir)
            continue
        state = runs.load_state(run_dir)
        selection.append((run_dir, state, status))

    selection.sort(key=lambda t: t[0].name, reverse=True)
    selection.sort(key=lambda t: _STATUS_ORDER[t[2]])
    active.sort(key=lambda d: d.name, reverse=True)
    return selection, active


def _print_selection(selection):
    print("runs to delete:")
    for run_dir, state, status in selection:
        branch = "<unknown>"
        if state is not None:
            b = state.get("data", {}).get("branch")
            if b:
                branch = b
        print(f"  {run_dir.name}  {status:<7}  {branch}")


def _count_non_running_in_other_projects(current: str | None) -> int:
    count = 0
    for name in runs.all_project_names():
        if name == current:
            continue
        for run_dir in runs.project_runs(name):
            if runs.classify_run(run_dir) != "running":
                count += 1
    return count


def _confirm() -> bool:
    if not sys.stdin.isatty():
        return False
    answer = input("proceed? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def run(args) -> int:
    scope = _resolve_project_scope(args)
    if isinstance(scope, int):
        return scope

    selection, active = _build_selection(scope)

    if not selection:
        if not getattr(args, "project", None) and not getattr(
            args, "all_projects", False
        ):
            current = runs.current_project_name()
            other = _count_non_running_in_other_projects(current)
            if other > 0:
                print(
                    f"{other} non-running run(s) in other projects; pass --all-projects to include them"
                )
        print(f"deleted 0; skipped {len(active)} active")
        return 0

    _print_selection(selection)

    if args.dry_run:
        print(f"would delete {len(selection)} run(s); would skip {len(active)} active")
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            print(
                "error: refusing to prompt: stdin is not a tty; pass --yes to proceed non-interactively",
                file=sys.stderr,
            )
            return 1
        if not _confirm():
            print("aborted")
            return 0

    n_deleted = 0
    for run_dir, _, _ in selection:
        project = run_dir.parent.name
        result = runs.delete_run(run_dir, delete_branch=args.delete_branch)
        for warning in result["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)
        if result["branch_deleted"]:
            print(f"deleted branch {result['branch']}")
        print(f"deleted run {run_dir.name}  {project}")
        n_deleted += 1

    for run_dir in active:
        pid_file = run_dir / "draft.pid"
        pid = None
        with contextlib.suppress(Exception):
            pid = int(pid_file.read_text().strip())
        print(f"skipped active run {run_dir.name} (pid {pid})")

    print(f"deleted {n_deleted}; skipped {len(active)} active")
    return 0
