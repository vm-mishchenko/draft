import os
import re
import subprocess
import sys
from pathlib import Path

from draft import runs
from draft.config import _FORBIDDEN_STEP_KEYS, _LOOPING_STEPS
from draft.types import WorktreeMode

_TIMESTAMP_RE = re.compile(r"^\d{6}-\d{6}$")
_RUN_ID_CHARS_RE = re.compile(r"^[a-z0-9._-]+$")
_RUN_ID_BORDER = frozenset("-_.")


def _assert_git_repo():
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
    )
    if result.returncode != 0:
        print("error: not inside a git repository", file=sys.stderr)
        sys.exit(3)


def _assert_main_clone():
    result = subprocess.run(
        ["git", "rev-parse", "--show-superproject-working-tree"],
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        print("error: cannot run draft from inside a git worktree", file=sys.stderr)
        sys.exit(3)
    result2 = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
    )
    lines = result2.stdout.splitlines()
    cwd = os.getcwd()
    main_wt = ""
    for line in lines:
        if line.startswith("worktree "):
            main_wt = line[len("worktree ") :]
            break
    if cwd != main_wt and main_wt:
        print(
            "error: draft must be run from the main worktree, not a linked worktree",
            file=sys.stderr,
        )
        sys.exit(3)


def _assert_on_path(tool: str):
    import shutil

    if not shutil.which(tool):
        print(f"error: '{tool}' not found on PATH", file=sys.stderr)
        sys.exit(3)


def _repo_root() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _project_name(repo: str) -> str:
    return Path(repo).name


def _sanitize_branch(branch: str) -> str:
    return branch.replace("/", "-")


def _canonical_worktree_path(project: str, branch: str) -> Path:
    return Path.home() / ".draft" / "worktrees" / project / _sanitize_branch(branch)


def _branch_worktrees(repo: str, branch: str) -> list[str]:
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    paths: list[str] = []
    current_path: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line[len("worktree ") :]
        elif line.startswith("branch refs/heads/") and current_path:
            wt_branch = line[len("branch refs/heads/") :]
            if wt_branch == branch:
                paths.append(current_path)
    return paths


def _local_branch_exists(repo: str, branch: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
        cwd=repo,
    )
    return result.returncode == 0


def _current_head_branch(repo: str) -> str | None:
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _is_working_tree_clean(repo: str) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    return result.returncode == 0 and result.stdout.strip() == ""


def _resolve_worktree_for_existing_branch(
    repo: str, project: str, branch: str, branch_was_explicit: bool
) -> tuple[str, str]:
    """Returns (wt_dir, worktree_mode).

    worktree_mode is "worktree" (will be created) or "reuse-existing"
    (already at canonical path, validated, will be reused as-is).
    Calls sys.exit(2) on any refusal."""
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
    """For --no-worktree: branch must not be checked out in any LINKED worktree."""
    paths = [p for p in _branch_worktrees(repo, branch) if p != repo]
    if paths:
        print(
            f"error: branch '{branch}' is currently checked out in another worktree:",
            file=sys.stderr,
        )
        for p in paths:
            print(f"       {p}", file=sys.stderr)
        sys.exit(2)


def _checkout_in_place(repo: str, branch: str) -> None:
    head = _current_head_branch(repo)
    if head == branch:
        return
    result = subprocess.run(
        ["git", "checkout", branch],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        print(f"error: failed to checkout '{branch}': {stderr}", file=sys.stderr)
        sys.exit(3)


def _validate_run_id(run_id: str, project_name: str) -> None:
    if not run_id:
        print("error: --run-id must not be empty", file=sys.stderr)
        sys.exit(2)
    if len(run_id) > 64:
        print(f"error: --run-id '{run_id}' exceeds 64 characters", file=sys.stderr)
        sys.exit(2)
    if not _RUN_ID_CHARS_RE.match(run_id):
        print(
            f"error: --run-id '{run_id}' contains invalid characters (allowed: [a-z0-9._-])",
            file=sys.stderr,
        )
        sys.exit(2)
    if run_id[0] in _RUN_ID_BORDER or run_id[-1] in _RUN_ID_BORDER:
        print(
            f"error: --run-id '{run_id}' must not start or end with '-', '_', or '.'",
            file=sys.stderr,
        )
        sys.exit(2)
    if ".." in run_id:
        print(f"error: --run-id '{run_id}' must not contain '..'", file=sys.stderr)
        sys.exit(2)
    if _TIMESTAMP_RE.match(run_id):
        print(
            f"error: --run-id '{run_id}' matches the reserved timestamp format (YYMMDD-HHMMSS)",
            file=sys.stderr,
        )
        sys.exit(2)
    run_dir = runs.runs_base() / project_name / run_id
    if run_dir.exists():
        print(
            f"error: run '{run_id}' already exists in project '{project_name}'",
            file=sys.stderr,
        )
        sys.exit(2)


def _validate_overrides(overrides: list[str]) -> None:
    for override in overrides:
        if "=" not in override or "." not in override.split("=")[0]:
            continue
        key_path = override.split("=", 1)[0]
        step_name, key = key_path.split(".", 1)
        if key in _FORBIDDEN_STEP_KEYS:
            print(
                f"error: '{key}' is no longer supported (the pipeline-level retry "
                f"concept was removed). Remove it from steps.{step_name}.",
                file=sys.stderr,
            )
            sys.exit(2)
        if key == "max_retries" and step_name not in _LOOPING_STEPS:
            print(
                f"error: 'max_retries' has no effect on steps.{step_name} because "
                f"the step runs once. Remove it.",
                file=sys.stderr,
            )
            sys.exit(2)


def _apply_overrides(config: dict, overrides: list[str]) -> dict:
    import copy

    cfg = copy.deepcopy(config)
    for override in overrides:
        if "=" not in override or "." not in override.split("=")[0]:
            print(
                f"warning: ignoring malformed --set value: {override}", file=sys.stderr
            )
            continue
        key_path, value = override.split("=", 1)
        step_name, key = key_path.split(".", 1)
        cfg.setdefault("steps", {}).setdefault(step_name, {})[key] = value
    return cfg
