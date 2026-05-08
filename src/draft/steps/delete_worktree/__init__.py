import subprocess
from pathlib import Path

from pipeline import Step, StepError

_IDEMPOTENT_SIGNATURES = (
    "is not a working tree",
    "is not a linked working tree",
    "not registered",
)


class DeleteWorktreeStep(Step):
    name = "delete-worktree"

    def defaults(self) -> dict:
        return {"max_retries": 1, "timeout": 60}

    def run(self, ctx, engine, lifecycle):
        with engine.stage(self.name):
            wt_dir = ctx.get("wt_dir", "")
            if not wt_dir:
                raise StepError(self.name, 1)

            if not Path(wt_dir).exists():
                return

            result = subprocess.run(
                ["git", "worktree", "remove", wt_dir, "--force"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return

            stderr = (result.stderr.strip() or result.stdout.strip()).lower()
            if any(sig in stderr for sig in _IDEMPOTENT_SIGNATURES):
                return

            raise StepError(self.name, result.returncode)
