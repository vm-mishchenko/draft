import json
import os
import shutil
import subprocess
from pathlib import Path

from draft.types import BranchSource
from pipeline.metrics import parse_human

FULL_PIPELINE_STEPS = (
    "create-worktree",
    "implement-spec",
    "push-commits",
    "open-pr",
    "babysit-pr",
)
SKIP_PR_STEPS = ("create-worktree", "implement-spec")


def runs_base() -> Path:
    return Path.home() / ".draft" / "runs"


def find_run_dir(run_id: str) -> Path | None:
    base = runs_base()
    if not base.exists():
        return None
    for project_dir in base.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / run_id
        if candidate.is_dir():
            return candidate
    return None


def _run_started_at(run_dir: Path) -> float | None:
    state = load_state(run_dir)
    if state is None:
        return None
    sessions = state.get("sessions", [])
    if not sessions:
        return None
    started = sessions[0].get("started_at")
    if not started:
        return None
    try:
        return parse_human(started).timestamp()
    except (ValueError, TypeError):
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def find_latest_run_dir() -> Path | None:
    base = runs_base()
    if not base.exists():
        return None
    candidates = []
    for project_dir in base.iterdir():
        if not project_dir.is_dir():
            continue
        for run_dir in project_dir.iterdir():
            if not (run_dir.is_dir() and (run_dir / "state.json").exists()):
                continue
            started = _run_started_at(run_dir) or run_dir.stat().st_mtime
            candidates.append((started, run_dir))
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_run_active(run_dir: Path) -> bool:
    pid_file = run_dir / "draft.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        return is_pid_alive(pid)
    except (ValueError, OSError):
        return False


def load_state(run_dir: Path) -> dict | None:
    state_path = run_dir / "state.json"
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text())
    except Exception:
        return None


def expected_steps(state: dict) -> tuple[str, ...]:
    """Return the expected step names for the given state.

    Raises CorruptStateError if state["data"]["pipeline"] is missing or unknown.
    """
    from draft.pipelines import CorruptStateError, get_pipeline

    data = state.get("data", {})
    pipeline_name = data.get("pipeline", "")
    if not pipeline_name:
        raise CorruptStateError("state is missing required 'data.pipeline' field")
    pipeline = get_pipeline(pipeline_name)
    return pipeline.expected_steps(data)


def is_run_finished(state: dict) -> bool:
    """Return True if all expected steps are completed.

    Raises CorruptStateError if the pipeline field is missing or unknown.
    """
    return all(s in state.get("completed", []) for s in expected_steps(state))


def find_original_run_on_branch(project: str, branch: str) -> Path | None:
    candidates = []
    for run_dir in project_runs(project):
        state = load_state(run_dir)
        if state is None:
            continue
        data = state.get("data", {})
        if data.get("branch") != branch:
            continue
        if data.get("branch_source") != BranchSource.NEW:
            continue
        started = _run_started_at(run_dir) or 0.0
        candidates.append((started, run_dir))
    if not candidates:
        return None
    return min(candidates, key=lambda x: x[0])[1]


def find_active_run_on_branch(project: str, branch: str) -> Path | None:
    from draft.pipelines import CorruptStateError

    project_dir = runs_base() / project
    if not project_dir.exists():
        return None
    for run_dir in sorted(project_dir.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        state = load_state(run_dir)
        if state is None:
            continue
        if state.get("data", {}).get("branch") != branch:
            continue
        try:
            finished = is_run_finished(state)
        except CorruptStateError:
            continue
        if is_run_active(run_dir) or not finished:
            return run_dir
    return None


def project_runs(project_name: str) -> list[Path]:
    project_dir = runs_base() / project_name
    if not project_dir.exists():
        return []
    return [d for d in project_dir.iterdir() if d.is_dir()]


def all_project_names() -> list[str]:
    base = runs_base()
    if not base.exists():
        return []
    return [d.name for d in base.iterdir() if d.is_dir()]


def current_project_name() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).name


def delete_run(run_dir: Path, *, delete_branch: bool = False) -> dict:
    result = {
        "run_id": run_dir.name,
        "status": "deleted",
        "branch": None,
        "branch_deleted": False,
        "warnings": [],
        "pid": None,
    }

    pid_file = run_dir / "draft.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if is_pid_alive(pid):
                result["status"] = "active"
                result["pid"] = pid
                return result
        except (ValueError, OSError):
            pass

    state = load_state(run_dir)
    wt_dir = None
    branch = None
    repo = None
    if state is not None:
        data = state.get("data", {})
        wt_dir = data.get("wt_dir")
        branch = data.get("branch")
        repo = data.get("repo")
        result["branch"] = branch

    if wt_dir and Path(wt_dir).exists():
        subprocess.run(
            ["git", "worktree", "remove", wt_dir, "--force"],
            capture_output=True,
        )

    if delete_branch:
        if branch and repo and Path(repo).exists():
            r = subprocess.run(
                ["git", "branch", "-D", branch],
                capture_output=True,
                text=True,
                cwd=repo,
            )
            if r.returncode == 0:
                result["branch_deleted"] = True
            else:
                stderr = r.stderr.strip() or r.stdout.strip()
                result["warnings"].append(
                    f"failed to delete branch '{branch}': {stderr}"
                )
        else:
            result["warnings"].append(
                "--delete-branch requested but branch or repo missing from state"
            )

    shutil.rmtree(run_dir)
    result["status"] = "deleted"
    return result
