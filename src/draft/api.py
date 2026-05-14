import os
import subprocess
import time
from dataclasses import dataclass, field
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
    validate_review_cmd_argv0,
)
from draft.errors import DraftError, PreflightError, StepFailedError, UserInputError
from draft.hooks import DraftLifecycle, HookRunner
from draft.pipelines import PIPELINES
from pipeline import Pipeline, RunContext, StepError
from pipeline.heartbeat import HeartbeatPulse
from pipeline.runner import LLMClient, Runner

_BRANCH_HEAD_SENTINEL = ""


@dataclass
class CreateParams:
    spec_path: str | None = None
    prompt: str | None = None
    overrides: list[str] = field(default_factory=list)
    skip_pr: bool = False
    from_branch: str | None = None
    branch: str | None = None
    no_worktree: bool = False
    delete_worktree: bool = False
    no_review: bool = False
    run_id: str | None = None


@dataclass
class CreateResult:
    run_id: str
    branch: str
    wt_dir: str
    run_dir: Path
    worktree_mode: str


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
    raise PreflightError(
        "could not find origin/main or origin/master; use --from to specify a base branch"
    )


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


def _assert_spec_readable(spec_path: str) -> None:
    p = Path(spec_path).expanduser()
    try:
        resolved = p.resolve(strict=True)
    except FileNotFoundError:
        raise UserInputError(f"spec file not found: {p.resolve()}") from None
    except PermissionError:
        raise UserInputError(f"cannot read spec file: {p.resolve()}") from None
    if not resolved.is_file():
        raise UserInputError(f"spec path is not a regular file: {resolved}")
    if not os.access(resolved, os.R_OK):
        raise UserInputError(f"cannot read spec file: {resolved}")


def _resolve_worktree_for_existing_branch(
    repo: str, project: str, branch: str, branch_was_explicit: bool
) -> tuple[str, str]:
    paths = _branch_worktrees(repo, branch)
    canonical = _canonical_worktree_path(project, branch)
    canonical_str = str(canonical)

    if not paths:
        return (canonical_str, "worktree")

    if not branch_was_explicit:
        lines = [f"branch '{branch}' (current HEAD) has a worktree at:"]
        lines.extend(f"       {p}" for p in paths)
        lines.append(
            f"       pass '--branch {branch}' explicitly to reuse it, or remove the worktree first"
        )
        raise UserInputError("\n".join(lines))

    if len(paths) != 1 or Path(paths[0]).resolve() != canonical.resolve():
        lines = [f"branch '{branch}' is checked out at non-canonical path(s):"]
        lines.extend(f"       {p}" for p in paths)
        lines.append(
            f"       only worktrees at {canonical_str} can be reused; remove the others first"
        )
        raise UserInputError("\n".join(lines))

    if not Path(canonical_str).is_dir():
        raise UserInputError(
            f"branch '{branch}' has a stale worktree registration; directory missing:\n"
            f"       {canonical_str}\n"
            f"       run 'git worktree prune' to clean up, then rerun"
        )

    head = _current_head_branch(canonical_str)
    if head is None:
        raise UserInputError(
            f"worktree {canonical_str} has detached HEAD; refusing to reuse"
        )
    if head != branch:
        raise UserInputError(
            f"worktree {canonical_str} is on branch '{head}', not '{branch}'"
        )

    if not _is_working_tree_clean(canonical_str):
        raise UserInputError(
            f"worktree is dirty; cannot reuse:\n"
            f"       {canonical_str}\n"
            f"       inspect with: git -C {canonical_str} status"
        )

    return (canonical_str, "reuse-existing")


def _assert_branch_free_for_in_place(repo: str, branch: str) -> None:
    paths = [p for p in _branch_worktrees(repo, branch) if p != repo]
    if paths:
        lines = [f"branch '{branch}' is currently checked out in another worktree:"]
        lines.extend(f"       {p}" for p in paths)
        raise UserInputError("\n".join(lines))


def _base_short_name(base: str) -> str:
    return base.removeprefix("origin/").removeprefix("refs/heads/")


def _resolve_working_branch(
    repo: str, branch: str | None, base: str
) -> tuple[str, str]:
    if branch is None:
        return ("", "new")

    base_short = _base_short_name(base)

    if branch == _BRANCH_HEAD_SENTINEL:
        head = _current_head_branch(repo)
        if head is None:
            raise UserInputError(
                "HEAD is detached; cannot resolve --branch without a value"
            )
        branch = head
    else:
        if not _local_branch_exists(repo, branch):
            raise UserInputError(
                f"branch '{branch}' does not exist locally\n"
                f"       fetch it first or pick an existing branch"
            )

    if branch == base_short:
        raise UserInputError(
            f"working branch '{branch}' is the configured base; refusing"
        )

    return (branch, "existing")


def _detect_pr_mode(
    branch: str, branch_source: str, skip_pr: bool, repo: str
) -> tuple[str, str | None]:
    if skip_pr:
        return ("skip", None)
    if branch_source == "new":
        return ("open", None)

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
        raise PreflightError(f"failed to query open PRs for '{branch}': {stderr}")
    urls = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(urls) == 0:
        return ("open", None)
    if len(urls) == 1:
        return ("reuse", urls[0])
    lines = [f"branch '{branch}' has multiple open PRs:"]
    lines.extend(f"       {u}" for u in urls)
    raise UserInputError("\n".join(lines))


def _assert_no_active_run_on_branch(project: str, branch: str) -> None:
    existing = runs.find_active_run_on_branch(project, branch)
    if existing is not None:
        run_id = existing.name
        raise UserInputError(
            f"branch '{branch}' is already targeted by an unresolved run '{run_id}'\n"
            f"       resume it: draft continue {run_id}\n"
            f"       or remove it: draft delete {run_id}"
        )


def _compose_active_steps(
    worktree_mode: str,
    pr_mode: str,
    skip_pr: bool,
    delete_worktree: bool = False,
    skip_review: bool = False,
    has_review_cmd: bool = False,
):
    skipped = set()
    if worktree_mode in ("no-worktree", "reuse-existing"):
        skipped.add("create-worktree")
    if skip_pr:
        skipped.update({"push-commits", "open-pr", "babysit-pr"})
    elif pr_mode == "reuse":
        skipped.add("open-pr")
    if not (delete_worktree and worktree_mode in ("worktree", "reuse-existing")):
        skipped.add("delete-worktree")
    if (not has_review_cmd) or skip_review:
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
            if step.name == "create-worktree" and worktree_mode == "reuse-existing":
                suffix = " [skipped, reused]"
            else:
                suffix = " [skipped]"
        else:
            suffix = ""
        print(f"  - {step.name}{suffix}")
    print()


def _validate_create_flags(params: CreateParams) -> None:
    if not params.spec_path and not params.prompt:
        raise UserInputError("provide a spec file or --prompt TEXT")
    if params.branch is not None and params.from_branch is not None:
        raise UserInputError("--branch and --from are mutually exclusive")
    if params.delete_worktree and params.no_worktree:
        raise UserInputError("--delete-worktree cannot be combined with --no-worktree")


def create(params: CreateParams, *, llm: LLMClient) -> CreateResult:
    _validate_create_flags(params)

    if params.spec_path and not params.prompt:
        _assert_spec_readable(params.spec_path)

    _assert_on_path("claude")
    _assert_git_repo()
    _assert_main_clone()
    if not params.skip_pr:
        _assert_on_path("gh")

    repo = _repo_root()
    project_name = _project_name(repo)

    if params.run_id:
        _validate_run_id(params.run_id, project_name)
        run_id = params.run_id
    else:
        run_id = time.strftime("%y%m%d-%H%M%S")

    base_branch = _resolve_base_branch(repo, params.from_branch)

    branch, branch_source = _resolve_working_branch(repo, params.branch, base_branch)

    existing_wt_dir: str | None = None
    existing_worktree_mode: str | None = None
    if branch_source == "existing":
        _assert_no_active_run_on_branch(project_name, branch)
        if params.no_worktree:
            _assert_branch_free_for_in_place(repo, branch)
            if not _is_working_tree_clean(repo):
                raise UserInputError(
                    "working tree is dirty; commit or stash before --no-worktree"
                )
        else:
            branch_was_explicit = (
                params.branch is not None and params.branch != _BRANCH_HEAD_SENTINEL
            )
            existing_wt_dir, existing_worktree_mode = (
                _resolve_worktree_for_existing_branch(
                    repo, project_name, branch, branch_was_explicit
                )
            )
    else:
        if params.no_worktree:
            raise UserInputError("--no-worktree requires --branch (existing branch)")

    pr_mode, pr_url = _detect_pr_mode(branch, branch_source, params.skip_pr, repo)

    run_dir = Path.home() / ".draft" / "runs" / project_name / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    pid_file = run_dir / "draft.pid"
    pid_file.write_text(str(os.getpid()))

    if params.prompt:
        prompt_file = run_dir / "prompt.md"
        prompt_file.write_text(params.prompt)
        spec = str(prompt_file)
        if branch_source == "new":
            branch = _branch_slug_from_claude(params.prompt, run_id)
    else:
        spec = str(Path(params.spec_path).resolve())
        if branch_source == "new":
            stem = Path(spec).stem
            branch = stem.lower().replace("_", "-").replace(" ", "-")[:50]

    if branch_source == "new":
        branch = _unique_branch(repo, branch)

    if params.no_worktree:
        worktree_mode = "no-worktree"
        wt_dir = repo
    elif existing_worktree_mode is not None:
        worktree_mode = existing_worktree_mode
        wt_dir = existing_wt_dir
    else:
        worktree_mode = "worktree"
        wt_dir = str(_canonical_worktree_path(project_name, branch))

    try:
        config = load_config(repo)
    except ConfigError as exc:
        pid_file.unlink(missing_ok=True)
        raise DraftError(str(exc)) from exc
    _validate_overrides(params.overrides)
    config = _apply_overrides(config, params.overrides)
    try:
        validate_config(config)
        config = resolve_prompt_template(config, repo)
        config = resolve_pr_body_template(config, repo)
        validate_review_cmd_argv0(config, repo)
    except ConfigError as exc:
        pid_file.unlink(missing_ok=True)
        raise PreflightError(str(exc)) from exc

    has_review_cmd = bool(
        config.get("steps", {}).get("review-implementation", {}).get("cmd", "").strip()
    )

    step_configs = {
        step.name: step_config(config, step.name, step.defaults())
        for step in PIPELINES["create"].steps
    }

    active_steps, skipped_names = _compose_active_steps(
        worktree_mode,
        pr_mode,
        params.skip_pr,
        params.delete_worktree,
        params.no_review,
        has_review_cmd,
    )

    ctx = RunContext(run_id, run_dir, step_configs)
    ctx.set("branch", branch)
    ctx.set("branch_source", branch_source)
    ctx.set("base_branch", base_branch)
    ctx.set("wt_dir", wt_dir)
    ctx.set("repo", repo)
    ctx.set("spec", spec)
    ctx.set("project", project_name)
    ctx.set("skip_pr", params.skip_pr)
    ctx.set("worktree_mode", worktree_mode)
    ctx.set("pr_mode", pr_mode)
    ctx.set("delete_worktree", params.delete_worktree)
    ctx.set("skip_review", params.no_review)
    ctx.set("has_review_cmd", has_review_cmd)
    ctx.set("pipeline", "create")
    if pr_url is not None:
        ctx.set("pr_url", pr_url)

    if worktree_mode == "no-worktree":
        _checkout_in_place(repo, branch)
    else:
        Path(wt_dir).parent.mkdir(parents=True, exist_ok=True)

    engine = Runner(llm)
    lifecycle = DraftLifecycle(
        HookRunner(config, cwd=wt_dir, run_dir=run_dir, engine=engine)
    )

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

    hb = HeartbeatPulse(ctx.heartbeat).start()
    rc = 0
    try:
        Pipeline(active_steps).run(ctx, engine, lifecycle, session_metrics)
    except StepError as exc:
        rc = 1
        raise StepFailedError(
            f"step '{exc.step_name}' failed (exit {exc.exit_code})"
        ) from exc
    except BaseException:
        rc = -1
        raise
    finally:
        hb.stop()
        session_metrics.end(rc)
        ctx.save()
        pid_file.unlink(missing_ok=True)

    return CreateResult(
        run_id=run_id,
        branch=branch,
        wt_dir=wt_dir,
        run_dir=run_dir,
        worktree_mode=worktree_mode,
    )
