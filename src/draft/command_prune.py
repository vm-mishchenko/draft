import contextlib
import sys
from pathlib import Path

from draft import runs


def register(subparsers):
    p = subparsers.add_parser(
        "prune",
        help="Bulk-delete finished runs.",
        description="Bulk-delete successfully finished runs. By default operates on the current project. See also: draft delete.",
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
        "--all",
        dest="include_all",
        action="store_true",
        help="Include every non-active run regardless of finished status.",
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


def _build_selection(candidates, *, include_all):
    from draft.pipelines import CorruptStateError

    selection = []
    active = []
    for run_dir in candidates:
        if runs.is_run_active(run_dir):
            active.append(run_dir)
            continue
        state = runs.load_state(run_dir)
        try:
            finished = state is not None and runs.is_run_finished(state)
        except CorruptStateError:
            finished = True
        if include_all or finished:
            selection.append((run_dir, state))

    selection.sort(key=lambda x: x[0].name, reverse=True)
    active.sort(key=lambda d: d.name, reverse=True)
    return selection, active


def _print_selection(selection):
    print("runs to delete:")
    for run_dir, state in selection:
        branch = "<unknown>"
        if state is not None:
            b = state.get("data", {}).get("branch")
            if b:
                branch = b
        print(f"  {run_dir.name}  {branch}")


def _confirm() -> bool:
    if not sys.stdin.isatty():
        return False
    answer = input("proceed? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def run(args) -> int:
    scope = _resolve_project_scope(args)
    if isinstance(scope, int):
        return scope

    selection, active = _build_selection(scope, include_all=args.include_all)

    if not selection:
        print("no runs to prune")
        if active:
            print(f"skipped {len(active)} active")
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
    for run_dir, _ in selection:
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

    print(
        f"deleted {n_deleted}; skipped {len(active)} active; selected {len(selection)}"
    )
    return 0
