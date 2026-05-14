import sys

from draft.api import CreateParams, create
from draft.command_common import _assert_on_path
from draft.errors import DraftError, StepFailedError
from pipeline.runner import SubprocessLLMClient

_BRANCH_HEAD_SENTINEL = ""


def register(subparsers):
    p = subparsers.add_parser(
        "create", help="Start a fresh run from a spec file or prompt."
    )
    p.add_argument("spec_path", nargs="?", help="Path to spec file.")
    p.add_argument(
        "--prompt", metavar="TEXT", help="Inline prompt text instead of a spec file."
    )
    p.add_argument(
        "--set",
        metavar="STEP.KEY=VALUE",
        action="append",
        dest="overrides",
        default=[],
        help="Override a step config value (repeatable).",
    )
    p.add_argument(
        "--skip-pr",
        action="store_true",
        default=False,
        help="Stop after code generation; skip push and PR.",
    )
    p.add_argument(
        "--from",
        metavar="BRANCH",
        dest="from_branch",
        default=None,
        help="Base branch to create the worktree from (default: origin/main or origin/master).",
    )
    p.add_argument(
        "--branch",
        nargs="?",
        const=_BRANCH_HEAD_SENTINEL,
        default=None,
        metavar="NAME",
        help="Use an existing branch as the working branch (no value: current HEAD).",
    )
    p.add_argument(
        "--no-worktree",
        action="store_true",
        default=False,
        help="Run in the main repo instead of creating a worktree.",
    )
    p.add_argument(
        "--delete-worktree",
        action="store_true",
        default=False,
        help="Remove the worktree on success (after pr-babysit green, or after commit if --skip-pr).",
    )
    p.add_argument(
        "--no-review",
        action="store_true",
        default=False,
        help="Skip the review-implementation step for this run, even if configured.",
    )
    p.add_argument(
        "--run-id",
        metavar="NAME",
        default=None,
        help="Custom run id (default: auto-generated timestamp).",
    )
    p.set_defaults(func=run)


def run(args) -> int:
    params = CreateParams(
        spec_path=args.spec_path,
        prompt=args.prompt,
        overrides=args.overrides,
        skip_pr=args.skip_pr,
        from_branch=args.from_branch,
        branch=args.branch,
        no_worktree=args.no_worktree,
        delete_worktree=args.delete_worktree,
        no_review=args.no_review,
        run_id=args.run_id,
    )

    try:
        _assert_on_path("claude")
        result = create(params, llm=SubprocessLLMClient())
    except StepFailedError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return exc.exit_code
    except DraftError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    if params.skip_pr and not (
        params.delete_worktree
        and result.worktree_mode in ("worktree", "reuse-existing")
    ):
        print(f"done. (push and PR skipped; worktree left at {result.wt_dir})")
    else:
        print("done.")
    return 0
