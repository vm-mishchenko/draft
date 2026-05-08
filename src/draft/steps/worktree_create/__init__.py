import json
import subprocess
import sys
from pathlib import Path

from pipeline import Step


def _find_run_id_for_branch(project: str, branch: str, exclude_run_id: str | None = None) -> str | None:
    runs_dir = Path.home() / ".draft" / "runs" / project
    if not runs_dir.exists():
        return None
    for state_file in sorted(runs_dir.glob("*/state.json"), reverse=True):
        try:
            data = json.loads(state_file.read_text())
            run_id = data.get("run_id")
            if run_id == exclude_run_id:
                continue
            if data.get("data", {}).get("branch") == branch:
                return run_id
        except Exception:
            continue
    return None


def _branch_exists(repo: str, branch: str) -> bool:
    local = subprocess.run(
        ["git", "branch", "--list", branch],
        capture_output=True, text=True, cwd=repo,
    )
    if local.stdout.strip():
        return True
    remote = subprocess.run(
        ["git", "branch", "-r", "--list", f"*/{branch}"],
        capture_output=True, text=True, cwd=repo,
    )
    return bool(remote.stdout.strip())


class WorktreeCreateStep(Step):
    name = "create-worktree"

    def defaults(self) -> dict:
        return {"max_retries": 1, "timeout": 60, "retry_delay": 0}

    def cmd(self, ctx) -> list[str]:
        if ctx.get("branch_source") == "existing":
            return ["git", "worktree", "add", ctx.get("wt_dir"), ctx.get("branch")]
        return ["git", "worktree", "add", ctx.get("wt_dir"), "-b", ctx.get("branch"), ctx.get("base_branch")]

    def run(self, ctx, engine, lifecycle):
        cfg = ctx.config(self.name)
        from pipeline import StepError

        branch = ctx.get("branch")
        repo = ctx.get("repo")
        branch_source = ctx.get("branch_source", "new")

        if branch_source == "new" and _branch_exists(repo, branch):
            project = ctx.get("project")
            run_id = _find_run_id_for_branch(project, branch, exclude_run_id=ctx.run_id)
            print(f"\nerror: branch '{branch}' already exists", file=sys.stderr)
            if run_id:
                print(f"       created by run: {run_id}", file=sys.stderr)
                print(f"\n       to remove it: draft delete {run_id}", file=sys.stderr)
            else:
                print(f"\n       to remove it: git branch -D {branch}", file=sys.stderr)
            raise StepError(self.name, 255)

        if branch_source == "existing" and not _branch_exists(repo, branch):
            print(f"\nerror: branch '{branch}' no longer exists", file=sys.stderr)
            raise StepError(self.name, 255)

        with engine.stage(self.name):
            rc = engine.run_command(
                cmd=self.cmd(ctx),
                cwd=repo,
                log_path=ctx.log_path(self.name),
                attempt=1,
                timeout=cfg["timeout"],
            )
            if rc != 0:
                raise StepError(self.name, rc)
