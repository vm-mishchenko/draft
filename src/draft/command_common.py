import os
import re
import subprocess
from pathlib import Path

from draft import runs
from draft.config import _FORBIDDEN_STEP_KEYS, _LOOPING_STEPS
from draft.errors import PreflightError, UserInputError

_TIMESTAMP_RE = re.compile(r"^\d{6}-\d{6}$")
_RUN_ID_CHARS_RE = re.compile(r"^[a-z0-9._-]+$")
_RUN_ID_BORDER = frozenset("-_.")


def _assert_git_repo():
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise PreflightError("not inside a git repository")


def _assert_main_clone():
    result = subprocess.run(
        ["git", "rev-parse", "--show-superproject-working-tree"],
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        raise PreflightError("cannot run draft from inside a git worktree")
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
        raise PreflightError(
            "draft must be run from the main worktree, not a linked worktree"
        )


def _assert_on_path(tool: str):
    import shutil

    if not shutil.which(tool):
        raise PreflightError(f"'{tool}' not found on PATH")


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
        raise PreflightError(f"failed to checkout '{branch}': {stderr}")


def _validate_run_id(run_id: str, project_name: str) -> None:
    if not run_id:
        raise UserInputError("--run-id must not be empty")
    if len(run_id) > 64:
        raise UserInputError(f"--run-id '{run_id}' exceeds 64 characters")
    if not _RUN_ID_CHARS_RE.match(run_id):
        raise UserInputError(
            f"--run-id '{run_id}' contains invalid characters (allowed: [a-z0-9._-])"
        )
    if run_id[0] in _RUN_ID_BORDER or run_id[-1] in _RUN_ID_BORDER:
        raise UserInputError(
            f"--run-id '{run_id}' must not start or end with '-', '_', or '.'"
        )
    if ".." in run_id:
        raise UserInputError(f"--run-id '{run_id}' must not contain '..'")
    if _TIMESTAMP_RE.match(run_id):
        raise UserInputError(
            f"--run-id '{run_id}' matches the reserved timestamp format (YYMMDD-HHMMSS)"
        )
    run_dir = runs.runs_base() / project_name / run_id
    if run_dir.exists():
        raise UserInputError(
            f"run '{run_id}' already exists in project '{project_name}'"
        )


def _validate_overrides(overrides: list[str]) -> None:
    for override in overrides:
        if "=" not in override or "." not in override.split("=")[0]:
            continue
        key_path = override.split("=", 1)[0]
        step_name, key = key_path.split(".", 1)
        if key in _FORBIDDEN_STEP_KEYS:
            raise UserInputError(
                f"'{key}' is no longer supported (the pipeline-level retry "
                f"concept was removed). Remove it from steps.{step_name}."
            )
        if key == "max_retries" and step_name not in _LOOPING_STEPS:
            raise UserInputError(
                f"'max_retries' has no effect on steps.{step_name} because "
                f"the step runs once. Remove it."
            )


def _apply_overrides(config: dict, overrides: list[str]) -> dict:
    import copy
    import sys

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
