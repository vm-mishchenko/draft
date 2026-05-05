import subprocess
from importlib.resources import files
from pathlib import Path

from pipeline import Step, StepError


def _resolve_pr_template(repo: str) -> str:
    candidates = [
        Path(repo) / ".draft" / "pull-request-template.md",
        Path.home() / ".draft" / "pull-request-template.md",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text()
    return files("draft.steps.pr_open").joinpath("pull-request-template.md").read_text()


def _git_diff(wt_dir: str) -> str:
    for base in ("origin/main", "origin/master"):
        result = subprocess.run(
            ["git", "diff", f"{base}...HEAD"],
            capture_output=True, text=True, cwd=wt_dir,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    return ""


def _generate_pr_body(ctx, wt_dir: str) -> str | None:
    template = files("draft.steps.pr_open").joinpath("pr_description.md").read_text()
    spec = ctx.get("spec", "")
    diff = _git_diff(wt_dir)
    prompt = template.replace("{{SPEC}}", spec).replace("{{DIFF}}", diff)
    result = subprocess.run(
        ["claude", "-p", prompt, "--allowedTools", ""],
        capture_output=True, text=True,
        timeout=120,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


class PrOpenStep(Step):
    name = "pr-open"

    def defaults(self) -> dict:
        return {"max_retries": 1, "timeout": 60, "retry_delay": 0}

    def run(self, ctx, engine):
        cfg = ctx.config(self.name)
        repo = ctx.get("repo", "")
        branch = ctx.get("branch", "")
        wt_dir = ctx.get("wt_dir")

        body = _generate_pr_body(ctx, wt_dir)
        if body is None:
            body = _resolve_pr_template(repo)

        log_path = ctx.log_path(self.name)
        rc = engine.run_stage(
            label=self.name,
            cmd=["gh", "pr", "create", "--title", branch, "--body", body, "--draft"],
            cwd=wt_dir,
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
