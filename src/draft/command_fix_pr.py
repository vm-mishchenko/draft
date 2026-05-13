import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from draft import runs
from draft.command_common import (
    _apply_overrides,
    _assert_branch_free_for_in_place,
    _assert_git_repo,
    _assert_main_clone,
    _assert_on_path,
    _checkout_in_place,
    _project_name,
    _repo_root,
    _resolve_worktree_for_existing_branch,
    _validate_overrides,
    _validate_run_id,
)
from draft.config import ConfigError, load_config, step_config, validate_config
from draft.hooks import DraftLifecycle, HookRunner
from draft.pipelines import PIPELINES
from draft.steps.fix_pr import FixPrStep
from pipeline import RunContext, Runner, StepError
from pipeline.heartbeat import HeartbeatPulse


def register(subparsers):
    p = subparsers.add_parser(
        "fix-pr", help="Fix failing CI checks for a PR locally; commit but do not push."
    )
    p.add_argument("pr_input", help="PR URL or number.")
    p.add_argument(
        "--spec",
        metavar="PATH",
        dest="spec_path",
        default=None,
        help="Path to spec file (default: PR body).",
    )
    p.add_argument(
        "--no-worktree",
        action="store_true",
        default=False,
        help="Run in the main repo instead of a worktree.",
    )
    p.add_argument(
        "--delete-worktree",
        action="store_true",
        default=False,
        help="Remove the worktree when done.",
    )
    p.add_argument(
        "--run-id",
        metavar="NAME",
        default=None,
        help="Custom run id (default: auto-generated timestamp).",
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
        "--watch",
        action="store_true",
        default=False,
        help="Wait for the first failing check to appear instead of refusing on pending or no-failures CI.",
    )
    p.set_defaults(func=run)


def _fetch_pr(pr_input: str, repo: str) -> dict:
    result = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            str(pr_input),
            "--json",
            "url,number,state,isDraft,headRefName,headRefOid,baseRefName,isCrossRepository,body",
        ],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        print(f"error: {stderr}", file=sys.stderr)
        sys.exit(2)
    return json.loads(result.stdout)


def _assert_pr_acceptable(pr: dict) -> None:
    if pr["state"] != "OPEN":
        print(
            f"error: PR is not open (state: {pr['state']})",
            file=sys.stderr,
        )
        sys.exit(2)
    if pr.get("isCrossRepository"):
        print(
            "error: cross-repository (fork) PRs are not supported",
            file=sys.stderr,
        )
        sys.exit(2)


def _assert_branch_exists_and_matches(
    repo: str, branch: str, expected_sha: str, pr_number: int
) -> None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    if result.returncode != 0:
        print(f"error: branch '{branch}' does not exist locally", file=sys.stderr)
        print(f"       fetch it with: gh pr checkout {pr_number}", file=sys.stderr)
        sys.exit(2)
    local_sha = result.stdout.strip()
    if local_sha != expected_sha:
        print(f"error: local branch '{branch}' is at {local_sha}", file=sys.stderr)
        print(f"       PR headRefOid is {expected_sha}", file=sys.stderr)
        print(f"       sync with: gh pr checkout {pr_number}", file=sys.stderr)
        sys.exit(2)


def _assert_working_tree_clean(wt_dir: str) -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=wt_dir,
    )
    if result.stdout.strip():
        print("error: working tree is dirty:", file=sys.stderr)
        print(result.stdout, file=sys.stderr, end="")
        sys.exit(2)


def _resolve_worktree_for_fix_pr(
    repo: str, project: str, branch: str, args
) -> tuple[str, str]:
    if args.no_worktree:
        _assert_working_tree_clean(repo)
        _assert_branch_free_for_in_place(repo, branch)
        return repo, "no-worktree"
    return _resolve_worktree_for_existing_branch(
        repo, project, branch, branch_was_explicit=True
    )


def _snapshot_spec(run_dir: Path, spec_path: str | None, pr_body: str) -> Path:
    dest = run_dir / "spec.md"
    if spec_path:
        shutil.copy(spec_path, dest)
    else:
        dest.write_text(pr_body or "")
    return dest


def _compose_active_steps_fix_pr(worktree_mode: str, delete_worktree: bool):
    pipeline = PIPELINES["fix-pr"]
    skipped = set()
    if worktree_mode in ("no-worktree", "reuse-existing"):
        skipped.add("create-worktree")
    if not (delete_worktree and worktree_mode in ("worktree", "reuse-existing")):
        skipped.add("delete-worktree")
    active = [s for s in pipeline.steps if s.name not in skipped]
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
            if step.name == "create-worktree" and worktree_mode == "reuse-existing":
                suffix = " [skipped, reused]"
            else:
                suffix = " [skipped]"
        else:
            suffix = ""
        print(f"  - {step.name}{suffix}")
    print()


def _fetch_pr_head_oid(pr_input: str, repo: str) -> str | None:
    result = subprocess.run(
        ["gh", "pr", "view", str(pr_input), "--json", "headRefOid"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)["headRefOid"]
    except (json.JSONDecodeError, KeyError):
        return None


def _single_check_gate(pr_url: str) -> str:
    from draft.steps.babysit_pr import check_ci_counts

    try:
        counts = check_ci_counts(pr_url)
    except Exception:
        return "pending"
    if counts["failure"] > 0:
        return "failure"
    if sum(counts.values()) == 0:
        return "no-checks"
    if counts["pending"] == 0:
        return "green"
    return "pending"


def _watch_for_failures(
    pr_input: str,
    pr_url: str,
    repo: str,
    initial_oid: str,
    watch_delay: int,
    watch_timeout: int,
) -> str:
    from draft.steps.babysit_pr import check_ci_counts

    deadline = time.monotonic() + watch_timeout
    first_iteration = True

    while True:
        if not first_iteration:
            if time.monotonic() >= deadline:
                return "timeout"
            sleep_time = min(watch_delay, deadline - time.monotonic())
            if sleep_time > 0:
                time.sleep(sleep_time)

            oid = _fetch_pr_head_oid(pr_input, repo)
            if oid is not None and oid != initial_oid:
                print(
                    f"remote PR head moved during --watch: was {initial_oid}, now {oid}",
                    file=sys.stderr,
                )
                return "sha-changed"

        first_iteration = False

        try:
            counts = check_ci_counts(pr_url)
        except Exception as e:
            print(f"warning: gh pr checks failed: {e}", file=sys.stderr)
            counts = None

        if counts is not None:
            total = sum(counts.values())
            print(
                f"CI: {counts['success']}/{total} passed, "
                f"{counts['failure']} failed, "
                f"{counts['pending']} pending"
            )
            if total == 0:
                return "no-checks"
            if counts["failure"] > 0:
                return "failure"
            if counts["pending"] == 0:
                return "green"


def run(args) -> int:
    if args.delete_worktree and args.no_worktree:
        print(
            "error: --delete-worktree cannot be combined with --no-worktree",
            file=sys.stderr,
        )
        return 2

    _assert_git_repo()
    _assert_main_clone()
    _assert_on_path("gh")

    repo = _repo_root()
    project = _project_name(repo)

    pr_data = _fetch_pr(args.pr_input, repo)
    _assert_pr_acceptable(pr_data)

    branch = pr_data["headRefName"]
    remote_sha = pr_data["headRefOid"]
    pr_number = pr_data["number"]

    _assert_branch_exists_and_matches(repo, branch, remote_sha, pr_number)

    existing = runs.find_active_run_on_branch(project, branch)
    if existing is not None:
        run_id_existing = existing.name
        print(
            f"error: branch '{branch}' is already targeted by an unresolved run '{run_id_existing}'",
            file=sys.stderr,
        )
        print(f"       resume it: draft continue {run_id_existing}", file=sys.stderr)
        print(f"       or remove it: draft delete {run_id_existing}", file=sys.stderr)
        sys.exit(2)

    wt_dir, worktree_mode = _resolve_worktree_for_fix_pr(repo, project, branch, args)

    try:
        config = load_config(repo)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _validate_overrides(args.overrides)
    config = _apply_overrides(config, args.overrides)
    try:
        validate_config(config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    fix_cfg = step_config(config, "fix-pr", FixPrStep().defaults())

    if args.watch:
        gate = _watch_for_failures(
            args.pr_input,
            pr_data["url"],
            repo,
            pr_data["headRefOid"],
            int(fix_cfg["watch_delay"]),
            int(fix_cfg["watch_timeout"]),
        )
    else:
        gate = _single_check_gate(pr_data["url"])

    if gate == "green":
        print(f"PR is green: {pr_data['url']}")
        return 0
    if gate == "no-checks":
        print("error: PR has no configured checks; nothing to fix", file=sys.stderr)
        return 2
    if gate == "pending":
        print("CI is still pending; wait for it to settle and re-run", file=sys.stderr)
        return 2
    if gate == "timeout":
        print(
            f"fix-pr: --watch timed out after {fix_cfg['watch_timeout']}s waiting for failing checks",
            file=sys.stderr,
        )
        return 124
    if gate == "sha-changed":
        return 2
    # gate == "failure": fall through to pipeline

    existing = runs.find_active_run_on_branch(project, branch)
    if existing is not None:
        run_id_existing = existing.name
        print(
            f"error: branch '{branch}' is already targeted by an unresolved run '{run_id_existing}'",
            file=sys.stderr,
        )
        print(f"       resume it: draft continue {run_id_existing}", file=sys.stderr)
        print(f"       or remove it: draft delete {run_id_existing}", file=sys.stderr)
        return 2

    if args.run_id:
        _validate_run_id(args.run_id, project)
        run_id = args.run_id
    else:
        run_id = time.strftime("%y%m%d-%H%M%S")

    run_dir = Path.home() / ".draft" / "runs" / project / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    pid_file = run_dir / "draft.pid"
    pid_file.write_text(str(os.getpid()))

    spec_path_dest = _snapshot_spec(run_dir, args.spec_path, pr_data.get("body") or "")

    pipeline = PIPELINES["fix-pr"]
    step_configs = {
        step.name: step_config(config, step.name, step.defaults())
        for step in pipeline.steps
    }

    active_steps, skipped_names = _compose_active_steps_fix_pr(
        worktree_mode, args.delete_worktree
    )

    ctx = RunContext(run_id, run_dir, step_configs)
    ctx.set("pipeline", "fix-pr")
    ctx.set("pr_url", pr_data["url"])
    ctx.set("branch", branch)
    ctx.set("base_branch", pr_data["baseRefName"])
    ctx.set("wt_dir", wt_dir)
    ctx.set("repo", repo)
    ctx.set("spec", str(spec_path_dest))
    ctx.set("project", project)
    ctx.set("worktree_mode", worktree_mode)
    ctx.set("delete_worktree", args.delete_worktree)

    if worktree_mode == "no-worktree":
        _checkout_in_place(repo, branch)
    else:
        Path(wt_dir).parent.mkdir(parents=True, exist_ok=True)

    from pipeline import Pipeline

    session_metrics = ctx.metrics.session_begin("fix-pr")
    ctx.save()
    started_at = ctx._sessions[-1]["started_at"]
    _print_preamble(
        run_id,
        branch,
        wt_dir,
        run_dir,
        started_at,
        pipeline.steps,
        skipped_names,
        worktree_mode,
    )
    print("mode: local commit (no push)")
    print()

    engine = Runner()
    lifecycle = DraftLifecycle(
        HookRunner(config, cwd=wt_dir, run_dir=run_dir, engine=engine)
    )

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

    if rc == 0:
        print("done.")
    return rc
