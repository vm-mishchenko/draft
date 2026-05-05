import subprocess
from importlib.resources import files

from pipeline import Step, StepError


def _build_claude_cmd(ctx) -> list[str]:
    spec = ctx.get("spec", "")
    verify_errors = ctx.step_get("code-spec", "verify_errors", "")
    template = files("draft.steps.code_spec").joinpath("code_spec.md").read_text()
    if verify_errors:
        verify_section = f"## Test failures\n\n{verify_errors}\n\nFix the above failures before committing."
    else:
        verify_section = ""
    prompt = template.replace("{{SPEC}}", spec).replace("{{VERIFY_ERRORS}}", verify_section)
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

    def run(self, ctx, engine, lifecycle):
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
                results = lifecycle.run_hooks(self.name, "verify")
                failures = [r for r in results if r.rc != 0]
                if failures:
                    errors = "\n\n".join(f"$ {r.cmd}\n{r.output}" for r in failures)
                    ctx.step_set(self.name, "verify_errors", errors)
                    continue
                ctx.step_set(self.name, "verify_errors", "")
                ctx.save()
                return

            if attempt < cfg["max_retries"]:
                engine.sleep(cfg["retry_delay"])

        raise StepError(self.name, 1)
