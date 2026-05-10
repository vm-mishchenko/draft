import subprocess
from importlib.resources import files
from pathlib import Path

from pipeline import Step

# seconds to wait before the first poll and after a push; not user-configurable, CI needs time to register a new commit
INITIAL_PR_CHECK_DELAY = 15


def _build_claude_cmd(ctx) -> list[str]:
    template = files("draft.steps.pr_babysit").joinpath("pr_babysit.md").read_text()
    pr_url = ctx.get("pr_url", "")
    spec_path = ctx.get("spec", "")
    spec = ""
    if spec_path:
        path = Path(spec_path)
        if path.is_file():
            try:
                spec = path.read_text()
            except OSError:
                spec = ""
    verify_errors = ctx.step_get("babysit-pr", "verify_errors", "")
    if verify_errors:
        verify_section = f"## Test failures\n\n{verify_errors}\n\nFix the above failures before committing."
    else:
        verify_section = ""
    prompt = (
        template
        .replace("{{PR_URL}}", pr_url)
        .replace("{{SPEC}}", spec)
        .replace("{{VERIFY_ERRORS}}", verify_section)
    )
    return ["claude", "-p", prompt, "--allowedTools", "Bash,Edit,Write,Read"]


def _check_ci(pr_url: str) -> dict[str, int]:
    """Returns counts keyed by state group: success, failure, pending."""
    result = subprocess.run(
        ["gh", "pr", "checks", pr_url, "--json", "state", "-q", ".[].state"],
        capture_output=True, text=True,
        timeout=60,
    )
    counts: dict[str, int] = {"success": 0, "failure": 0, "pending": 0}
    for line in result.stdout.splitlines():
        state = line.strip().lower()
        if state in ("success", "completed"):
            counts["success"] += 1
        elif state in ("failure", "failed", "action_required", "timed_out", "cancelled", "startup_failure"):
            counts["failure"] += 1
        else:
            counts["pending"] += 1
    return counts


def _has_unpushed_commits(cwd: str) -> bool:
    result = subprocess.run(
        ["git", "rev-list", "--count", "@{u}..HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    if result.returncode != 0:
        return False
    try:
        return int(result.stdout.strip()) > 0
    except ValueError:
        return False


def _is_branch_clean(cwd: str) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=cwd,
    )
    return result.stdout.strip() == ""


class PrBabysitStep(Step):
    name = "babysit-pr"

    def defaults(self) -> dict:
        return {"max_retries": 100, "timeout": 1200, "checks_delay": 60}

    def run(self, ctx, engine, lifecycle):
        cfg = ctx.config(self.name)
        pr_url = ctx.get("pr_url", "")
        wt_dir = ctx.get("wt_dir")

        engine.sleep(INITIAL_PR_CHECK_DELAY, "waiting before pr-checks")
        with engine.stage(self.name) as s:
            for attempt in range(1, cfg["max_retries"] + 1):
                s.update(f"{attempt}/{cfg['max_retries']}")
                pushed_this_iter = False

                try:
                    counts = _check_ci(pr_url)
                except Exception:
                    counts = {"success": 0, "failure": 0, "pending": 1}

                total = sum(counts.values())
                print(
                    f"CI: {counts['success']}/{total} passed, "
                    f"{counts['failure']} failed, "
                    f"{counts['pending']} pending"
                )

                if counts["failure"] == 0 and counts["pending"] == 0:
                    if _is_branch_clean(wt_dir):
                        ctx.step_set(self.name, "attempts", attempt)
                        ctx.save()
                        s.update(f"green ({attempt} checks)")
                        print(f"PR is green: {pr_url}")
                        return

                if counts["failure"] > 0:
                    engine.run_command(
                        cmd=_build_claude_cmd(ctx),
                        cwd=wt_dir,
                        log_path=ctx.log_path(self.name),
                        attempt=attempt,
                        timeout=cfg["timeout"],
                    )
                    if _is_branch_clean(wt_dir) and _has_unpushed_commits(wt_dir):
                        results = lifecycle.run_hooks(self.name, "verify")
                        failures = [r for r in results if r.rc != 0]
                        if failures:
                            errors = "\n\n".join(f"$ {r.cmd}\n{r.output}" for r in failures)
                            ctx.step_set(self.name, "verify_errors", errors)
                        else:
                            ctx.step_set(self.name, "verify_errors", "")
                            engine.run_command(
                                cmd=["git", "push", "origin", "HEAD"],
                                cwd=wt_dir,
                                log_path=ctx.log_path(self.name),
                                attempt=attempt,
                                timeout=cfg["timeout"],
                            )
                            pushed_this_iter = True

                ctx.step_set(self.name, "attempts", attempt)
                ctx.save()
                next_delay = INITIAL_PR_CHECK_DELAY if pushed_this_iter else cfg["checks_delay"]
                engine.sleep(next_delay, "waiting before pr-checks")

        print(f"babysit-pr: exhausted attempts. PR: {pr_url}")
