import subprocess
from importlib.resources import files

from pipeline import Step, StepError


def _build_claude_cmd(ctx) -> list[str]:
    spec = ctx.get("spec", "")
    template = files("draft.steps.code_spec").joinpath("code_spec.md").read_text()
    prompt = template.replace("{{SPEC}}", spec)
    return ["claude", "-p", prompt, "--allowedTools", "Bash,Edit,Write,Read"]


def _is_branch_clean(cwd: str) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=cwd,
    )
    return result.stdout.strip() == ""


def _commits_ahead(cwd: str) -> int:
    for remote_branch in ("origin/main", "origin/master"):
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{remote_branch}..HEAD"],
            capture_output=True, text=True, cwd=cwd,
        )
        if result.returncode == 0:
            try:
                return int(result.stdout.strip())
            except ValueError:
                continue
    return 0


class CodeSpecStep(Step):
    name = "code-spec"

    def defaults(self) -> dict:
        return {"max_retries": 10, "timeout": 1200, "retry_delay": 0}

    def run(self, ctx, engine):
        cfg = ctx.config(self.name)
        wt_dir = ctx.get("wt_dir")

        for attempt in range(1, cfg["max_retries"] + 1):
            engine.run_stage(
                label=self.name,
                cmd=_build_claude_cmd(ctx),
                cwd=wt_dir,
                log_path=ctx.log_path(self.name),
                attempt=attempt,
                timeout=cfg["timeout"],
            )

            if _is_branch_clean(wt_dir) and _commits_ahead(wt_dir) > 0:
                ctx.save()
                return

            if attempt < cfg["max_retries"]:
                engine.sleep(cfg["retry_delay"])

        raise StepError(self.name, 1)
