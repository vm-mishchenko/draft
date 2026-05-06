import json
import subprocess
import sys
from pathlib import Path

from pipeline import Step


def _find_run_id_for_branch(project: str, branch: str) -> str | None:
    runs_dir = Path.home() / ".draft" / "runs" / project
    if not runs_dir.exists():
        return None
    for state_file in sorted(runs_dir.glob("*/state.json"), reverse=True):
        try:
            data = json.loads(state_file.read_text())
            if data.get("data", {}).get("branch") == branch:
                return data.get("run_id")
        except Exception:
            continue
    return None


def _branch_exists(repo: str, branch: str) -> bool:
    result = subprocess.run(
        ["git", "branch", "--list", branch],
        capture_output=True, text=True, cwd=repo,
    )
    return bool(result.stdout.strip())


class WorktreeCreateStep(Step):
    name = "worktree-create"

    def defaults(self) -> dict:
        return {"max_retries": 1, "timeout": 60, "retry_delay": 0}

    def cmd(self, ctx) -> list[str]:
        return ["git", "worktree", "add", ctx.get("wt_dir"), "-b", ctx.get("branch"), ctx.get("base_branch")]

    def run(self, ctx, engine, lifecycle):
        cfg = ctx.config(self.name)
        from pipeline import StepError

        branch = ctx.get("branch")
        repo = ctx.get("repo")

        if _branch_exists(repo, branch):
            project = ctx.get("project")
            run_id = _find_run_id_for_branch(project, branch)
            print(f"\nerror: branch '{branch}' already exists", file=sys.stderr)
            if run_id:
                print(f"       created by run: {run_id}", file=sys.stderr)
            print(f"\n       to remove it: git branch -D {branch}", file=sys.stderr)
            raise StepError(self.name, 255)

        rc = engine.run_stage(
            label=self.name,
            cmd=self.cmd(ctx),
            cwd=repo,
            log_path=ctx.log_path(self.name),
            attempt=1,
            timeout=cfg["timeout"],
        )
        if rc != 0:
            raise StepError(self.name, rc)
