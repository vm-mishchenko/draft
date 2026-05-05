from importlib.resources import files
from pathlib import Path

from pipeline import Step, StepError


def _resolve_pr_body(repo: str) -> str:
    candidates = [
        Path(repo) / ".draft" / "pull-request-template.md",
        Path.home() / ".draft" / "pull-request-template.md",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text()
    return files("draft.steps.pr_open").joinpath("pull-request-template.md").read_text()


class PrOpenStep(Step):
    name = "pr-open"

    def defaults(self) -> dict:
        return {"max_retries": 1, "timeout": 60, "retry_delay": 0}

    def run(self, ctx, engine, lifecycle):
        cfg = ctx.config(self.name)
        repo = ctx.get("repo", "")
        branch = ctx.get("branch", "")
        body = _resolve_pr_body(repo)

        log_path = ctx.log_path(self.name)
        rc = engine.run_stage(
            label=self.name,
            cmd=["gh", "pr", "create", "--title", branch, "--body", body, "--draft"],
            cwd=ctx.get("wt_dir"),
            log_path=log_path,
            attempt=1,
            timeout=cfg["timeout"],
        )
        if rc != 0:
            raise StepError(self.name, rc)

        for line in log_path.read_text().splitlines():
            if line.startswith("https://"):
                print(line)
                ctx.set("pr_url", line)
                ctx.save()
                break
