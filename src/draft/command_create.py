import os
import subprocess
import sys
import time
from pathlib import Path

from draft import runs
from draft.command_common import (
    _apply_overrides,
    _assert_git_repo,
    _assert_main_clone,
    _assert_on_path,
    _branch_worktrees,
    _canonical_worktree_path,
    _checkout_in_place,
    _current_head_branch,
    _is_working_tree_clean,
    _local_branch_exists,
    _project_name,
    _repo_root,
    _validate_overrides,
    _validate_run_id,
)
from draft.config import (
    ConfigError,
    load_config,
    resolve_pr_body_template,
    resolve_prompt_template,
    step_config,
    validate_config,
    validate_reviewer_argv0s,
)
from draft.hooks import DraftLifecycle, HookRunner
from draft.pipelines import PIPELINES
from draft.types import BranchSource, PrMode, WorktreeMode
from pipeline import RunContext, RunMetrics, Runner, StepError, fmt_duration
from pipeline.heartbeat import HeartbeatPulse

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


# --- pre-flight helpers ---


def _resolve_base_branch(repo: str, from_branch: str | None) -> str:
    if from_branch:
        return from_branch
    for candidate in ("origin/main", "origin/master"):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            capture_output=True,
            cwd=repo,
        )
        if result.returncode == 0:
            return candidate
    print(
        "error: could not find origin/main or origin/master; use --from to specify a base branch",
        file=sys.stderr,
    )
    sys.exit(3)


def _unique_branch(repo: str, branch: str) -> str:
    from draft.steps.create_worktree import _branch_exists

    i = 1
    candidate = branch
    while _branch_exists(repo, candidate):
        candidate = f"{branch}-{i}"
        i += 1
    return candidate


def _branch_slug_from_claude(prompt_text: str, run_id: str) -> str:
    from importlib.resources import files

    try:
        template = (
            files("draft.steps.implement_spec").joinpath("branch_slug.md").read_text()
        )
        full_prompt = template.replace("{{PROMPT}}", prompt_text)
        result = subprocess.run(
            ["claude", "-p", full_prompt],
            capture_output=True,
            text=True,
            timeout=60,
        )
        slug = result.stdout.strip().lower()
        slug = "".join(c if c.isalnum() or c == "-" else "-" for c in slug)
        slug = slug.strip("-")[:50]
        if slug:
            return slug
    except Exception:
        pass
    return f"draft-{run_id}"


# --- new helpers for create-modes ---


def _reject_flag_conflicts(args) -> None:
    if args.branch is not None and args.from_branch is not None:
        print("error: --branch and --from are mutually exclusive", file=sys.stderr)
        sys.exit(2)
    if args.delete_worktree and args.no_worktree:
        print(
            "error: --delete-worktree cannot be combined with --no-worktree",
            file=sys.stderr,
        )
        sys.exit(2)


def _assert_spec_readable(spec_path: str) -> None:
    p = Path(spec_path).expanduser()
    try:
        resolved = p.resolve(strict=True)
    except FileNotFoundError:
        print(f"error: spec file not found: {p.resolve()}", file=sys.stderr)
        sys.exit(2)
    except PermissionError:
        print(f"error: cannot read spec file: {p.resolve()}", file=sys.stderr)
        sys.exit(2)
    if not resolved.is_file():
        print(f"error: spec path is not a regular file: {resolved}", file=sys.stderr)
        sys.exit(2)
    if not os.access(resolved, os.R_OK):
        print(f"error: cannot read spec file: {resolved}", file=sys.stderr)
        sys.exit(2)


def _resolve_worktree_for_existing_branch(
    repo: str, project: str, branch: str, branch_was_explicit: bool
) -> tuple[str, str]:
    paths = _branch_worktrees(repo, branch)
    canonical = _canonical_worktree_path(project, branch)
    canonical_str = str(canonical)

    if not paths:
        return (canonical_str, WorktreeMode.WORKTREE)

    if not branch_was_explicit:
        print(
            f"error: branch '{branch}' (current HEAD) has a worktree at:",
            file=sys.stderr,
        )
        for p in paths:
            print(f"       {p}", file=sys.stderr)
        print(
            f"       pass '--branch {branch}' explicitly to reuse it, or remove the worktree first",
            file=sys.stderr,
        )
        sys.exit(2)

    if len(paths) != 1 or Path(paths[0]).resolve() != canonical.resolve():
        print(
            f"error: branch '{branch}' is checked out at non-canonical path(s):",
            file=sys.stderr,
        )
        for p in paths:
            print(f"       {p}", file=sys.stderr)
        print(
            f"       only worktrees at {canonical_str} can be reused; remove the others first",
            file=sys.stderr,
        )
        sys.exit(2)

    if not Path(canonical_str).is_dir():
        print(
            f"error: branch '{branch}' has a stale worktree registration; directory missing:",
            file=sys.stderr,
        )
        print(f"       {canonical_str}", file=sys.stderr)
        print(
            "       run 'git worktree prune' to clean up, then rerun", file=sys.stderr
        )
        sys.exit(2)

    head = _current_head_branch(canonical_str)
    if head is None:
        print(
            f"error: worktree {canonical_str} has detached HEAD; refusing to reuse",
            file=sys.stderr,
        )
        sys.exit(2)
    if head != branch:
        print(
            f"error: worktree {canonical_str} is on branch '{head}', not '{branch}'",
            file=sys.stderr,
        )
        sys.exit(2)

    if not _is_working_tree_clean(canonical_str):
        print("error: worktree is dirty; cannot reuse:", file=sys.stderr)
        print(f"       {canonical_str}", file=sys.stderr)
        print(f"       inspect with: git -C {canonical_str} status", file=sys.stderr)
        sys.exit(2)

    return (canonical_str, WorktreeMode.REUSE_EXISTING)


def _assert_branch_free_for_in_place(repo: str, branch: str) -> None:
    paths = [p for p in _branch_worktrees(repo, branch) if p != repo]
    if paths:
        print(
            f"error: branch '{branch}' is currently checked out in another worktree:",
            file=sys.stderr,
        )
        for p in paths:
            print(f"       {p}", file=sys.stderr)
        sys.exit(2)


def _base_short_name(base: str) -> str:
    return base.removeprefix("origin/").removeprefix("refs/heads/")


def _resolve_working_branch(repo: str, args, base: str) -> tuple[str, BranchSource]:
    """Returns (branch, branch_source) where branch_source is 'new' or 'existing'."""
    if args.branch is None:
        return ("", BranchSource.NEW)  # caller derives the new branch slug

    base_short = _base_short_name(base)

    if args.branch == _BRANCH_HEAD_SENTINEL:
        head = _current_head_branch(repo)
        if head is None:
            print(
                "error: HEAD is detached; cannot resolve --branch without a value",
                file=sys.stderr,
            )
            sys.exit(2)
        branch = head
    else:
        branch = args.branch
        if not _local_branch_exists(repo, branch):
            print(f"error: branch '{branch}' does not exist locally", file=sys.stderr)
            print("       fetch it first or pick an existing branch", file=sys.stderr)
            sys.exit(2)

    if branch == base_short:
        print(
            f"error: working branch '{branch}' is the configured base; refusing",
            file=sys.stderr,
        )
        sys.exit(2)

    return (branch, BranchSource.EXISTING)


def _detect_pr_mode(
    branch: str, branch_source: BranchSource, skip_pr: bool, repo: str
) -> tuple[PrMode, str | None]:
    """Returns (pr_mode, pr_url)."""
    if skip_pr:
        return (PrMode.SKIP, None)
    if branch_source == BranchSource.NEW:
        return (PrMode.OPEN, None)

    result = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "url",
            "-q",
            ".[].url",
        ],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        print(
            f"error: failed to query open PRs for '{branch}': {stderr}", file=sys.stderr
        )
        sys.exit(3)
    urls = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(urls) == 0:
        return (PrMode.OPEN, None)
    if len(urls) == 1:
        return (PrMode.REUSE, urls[0])
    print(f"error: branch '{branch}' has multiple open PRs:", file=sys.stderr)
    for u in urls:
        print(f"       {u}", file=sys.stderr)
    sys.exit(2)


def _assert_no_active_run_on_branch(project: str, branch: str) -> None:
    existing = runs.find_active_run_on_branch(project, branch)
    if existing is not None:
        run_id = existing.name
        print(
            f"error: branch '{branch}' is already targeted by an unresolved run '{run_id}'",
            file=sys.stderr,
        )
        print(f"       resume it: draft continue {run_id}", file=sys.stderr)
        print(f"       or remove it: draft delete {run_id}", file=sys.stderr)
        sys.exit(2)


def _compose_active_steps(
    worktree_mode: str,
    pr_mode: str,
    skip_pr: bool,
    delete_worktree: bool = False,
    skip_review: bool = False,
    has_any_reviewer: bool = False,
):
    skipped = set()
    if worktree_mode in (WorktreeMode.NO_WORKTREE, WorktreeMode.REUSE_EXISTING):
        skipped.add("create-worktree")
    if skip_pr:
        skipped.update({"push-commits", "open-pr", "babysit-pr"})
    elif pr_mode == PrMode.REUSE:
        skipped.add("open-pr")
    if not (
        delete_worktree
        and worktree_mode in (WorktreeMode.WORKTREE, WorktreeMode.REUSE_EXISTING)
    ):
        skipped.add("delete-worktree")
    if (not has_any_reviewer) or skip_review:
        skipped.add("review-implementation")
    active = [s for s in PIPELINES["create"].steps if s.name not in skipped]
    return active, skipped


def _print_preamble(
    run_id, branch, wt_dir, run_dir, started_at, all_steps, skipped, worktree_mode
):
    print(f"run-id:   {run_id}")
    print(f"branch:   {branch}")
    print(f"worktree: {wt_dir}")
    print(f"logs:     {run_dir}")
    print(f"started:  {started_at}")
    print("stages:")
    for step in all_steps:
        if step.name in skipped:
            if (
                step.name == "create-worktree"
                and worktree_mode == WorktreeMode.REUSE_EXISTING
            ):
                suffix = " [skipped, reused]"
            else:
                suffix = " [skipped]"
        else:
            suffix = ""
        print(f"  - {step.name}{suffix}")
    print()


def _remove_worktree(wt_dir: str) -> None:
    result = subprocess.run(
        ["git", "worktree", "remove", wt_dir, "--force"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        print(f"warning: failed to remove worktree {wt_dir}: {stderr}", file=sys.stderr)


def _print_run_summary(metrics: RunMetrics) -> None:
    try:
        agg = metrics.aggregates()
    except Exception:
        return
    runtime_str = fmt_duration(agg["total_runtime_seconds"])
    cost = agg["total_llm_cost_usd"]
    cost_str = f"${cost:.2f}" if cost is not None else "-"
    print(f"{'runtime:':<9}{runtime_str}")
    print(f"{'cost:':<9}{cost_str}")


def run(args) -> int:
    if not args.spec_path and not args.prompt:
        print("error: provide a spec file or --prompt TEXT", file=sys.stderr)
        return 1

    _reject_flag_conflicts(args)

    if args.spec_path and not args.prompt:
        _assert_spec_readable(args.spec_path)

    # 1. Pre-flight
    _assert_git_repo()
    _assert_main_clone()
    _assert_on_path("claude")
    if not args.skip_pr:
        _assert_on_path("gh")

    # 2. Run ID
    repo = _repo_root()
    project_name = _project_name(repo)
    if args.run_id:
        _validate_run_id(args.run_id, project_name)
        run_id = args.run_id
    else:
        run_id = time.strftime("%y%m%d-%H%M%S")

    base_branch = _resolve_base_branch(repo, args.from_branch)

    # 3. Resolve working branch (no side effects yet)
    branch, branch_source = _resolve_working_branch(repo, args, base_branch)

    # 4. Branch-context preflight (no side effects, may exit)
    existing_wt_dir: str | None = None
    existing_worktree_mode: str | None = None
    if branch_source == BranchSource.EXISTING:
        _assert_no_active_run_on_branch(project_name, branch)
        if args.no_worktree:
            _assert_branch_free_for_in_place(repo, branch)
            if not _is_working_tree_clean(repo):
                print(
                    "error: working tree is dirty; commit or stash before --no-worktree",
                    file=sys.stderr,
                )
                return 2
        else:
            branch_was_explicit = (
                args.branch is not None and args.branch != _BRANCH_HEAD_SENTINEL
            )
            existing_wt_dir, existing_worktree_mode = (
                _resolve_worktree_for_existing_branch(
                    repo, project_name, branch, branch_was_explicit
                )
            )
    else:  # new branch
        if args.no_worktree:
            print(
                "error: --no-worktree requires --branch (existing branch)",
                file=sys.stderr,
            )
            return 2

    # 5. Detect PR mode (may exit if multiple PRs)
    pr_mode, pr_url = _detect_pr_mode(branch, branch_source, args.skip_pr, repo)

    # 6. Spec resolution + new-branch slug
    run_dir = Path.home() / ".draft" / "runs" / project_name / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "draft.pid").write_text(str(os.getpid()))

    if args.prompt:
        prompt_file = run_dir / "prompt.md"
        prompt_file.write_text(args.prompt)
        spec = str(prompt_file)
        if branch_source == BranchSource.NEW:
            branch = _branch_slug_from_claude(args.prompt, run_id)
    else:
        spec = str(Path(args.spec_path).resolve())
        if branch_source == BranchSource.NEW:
            stem = Path(spec).stem
            branch = stem.lower().replace("_", "-").replace(" ", "-")[:50]

    if branch_source == BranchSource.NEW:
        branch = _unique_branch(repo, branch)

    # 7. Worktree path
    if args.no_worktree:
        worktree_mode = WorktreeMode.NO_WORKTREE
        wt_dir = repo
    elif existing_worktree_mode is not None:
        worktree_mode = existing_worktree_mode
        wt_dir = existing_wt_dir
    else:
        worktree_mode = WorktreeMode.WORKTREE
        wt_dir = str(_canonical_worktree_path(project_name, branch))

    # 8. Config
    try:
        config = load_config(repo)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        (run_dir / "draft.pid").unlink(missing_ok=True)
        return 1
    _validate_overrides(args.overrides)
    config = _apply_overrides(config, args.overrides)
    try:
        validate_config(config)
        config = resolve_prompt_template(config, repo)
        config = resolve_pr_body_template(config, repo)
        validate_reviewer_argv0s(config, repo)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        (run_dir / "draft.pid").unlink(missing_ok=True)
        return 3

    reviewers = (
        config.get("steps", {}).get("review-implementation", {}).get("reviewers", [])
    ) or []
    has_any_reviewer = any(
        isinstance(r, dict) and isinstance(r.get("cmd"), str) and r["cmd"].strip()
        for r in reviewers
    )

    # 9. Step configs
    step_configs = {
        step.name: step_config(config, step.name, step.defaults())
        for step in PIPELINES["create"].steps
    }

    # 10. Active steps
    active_steps, skipped_names = _compose_active_steps(
        worktree_mode,
        pr_mode,
        args.skip_pr,
        args.delete_worktree,
        args.no_review,
        has_any_reviewer,
    )

    # 11. Context
    ctx = RunContext(run_id, run_dir, step_configs)
    ctx.set("branch", branch)
    ctx.set("branch_source", branch_source)
    ctx.set("base_branch", base_branch)
    ctx.set("wt_dir", wt_dir)
    ctx.set("repo", repo)
    ctx.set("spec", spec)
    ctx.set("project", project_name)
    ctx.set("skip_pr", args.skip_pr)
    ctx.set("worktree_mode", worktree_mode)
    ctx.set("pr_mode", pr_mode)
    ctx.set("delete_worktree", args.delete_worktree)
    ctx.set("skip_review", args.no_review)
    ctx.set("has_review_cmd", has_any_reviewer)
    ctx.set("pipeline", "create")
    if pr_url is not None:
        ctx.set("pr_url", pr_url)

    # 12. In-place checkout (worktree_mode == no-worktree)
    if worktree_mode == WorktreeMode.NO_WORKTREE:
        _checkout_in_place(repo, branch)
    else:
        Path(wt_dir).parent.mkdir(parents=True, exist_ok=True)

    # 13. Session + preamble
    from pipeline import Pipeline

    pid_file = run_dir / "draft.pid"
    session_metrics = ctx.metrics.session_begin("create")
    ctx.save()
    started_at = ctx._sessions[-1]["started_at"]
    _print_preamble(
        run_id,
        branch,
        wt_dir,
        run_dir,
        started_at,
        PIPELINES["create"].steps,
        skipped_names,
        worktree_mode,
    )

    try:
        from draft.steps.implement_spec import original_spec

        label = original_spec.preamble_label(ctx)
        if label:
            print(f"original-spec: attached from {label}", file=sys.stderr)
    except Exception:
        pass

    # 14. Lifecycle + engine
    engine = Runner(model=config.get("model"))
    lifecycle = DraftLifecycle(
        HookRunner(config, cwd=wt_dir, run_dir=run_dir, engine=engine)
    )

    # 15. Run pipeline
    hb = HeartbeatPulse(ctx.heartbeat).start()
    rc = 0
    try:
        Pipeline(active_steps).run(ctx, engine, lifecycle, session_metrics)
    except StepError as exc:
        print(
            f"\nerror: step '{exc.step_name}' failed (exit {exc.exit_code})",
            file=sys.stderr,
        )
        rc = 1
    except BaseException:
        rc = -1
        raise
    finally:
        hb.stop()
        session_metrics.end(rc)
        ctx.save()
        pid_file.unlink(missing_ok=True)

    # 16. Done
    if rc == 0:
        if args.skip_pr and not (
            args.delete_worktree
            and worktree_mode in (WorktreeMode.WORKTREE, WorktreeMode.REUSE_EXISTING)
        ):
            print(f"done. (push and PR skipped; worktree left at {wt_dir})")
        else:
            print("done.")
        _print_run_summary(ctx.metrics)
    return rc
