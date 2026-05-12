import json
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from pipeline import Step, StepError
from pipeline.runner import TIMEOUT_EXIT

# seconds to wait before the first poll and after a push; not user-configurable, CI needs time to register a new commit
INITIAL_PR_CHECK_DELAY = 15

FAILURE_STATES = frozenset(
    {
        "failure",
        "failed",
        "action_required",
        "timed_out",
        "cancelled",
        "startup_failure",
    }
)


def _normalise_state(raw: str) -> str:
    s = raw.strip().lower()
    if s in ("success", "completed"):
        return "success"
    if s in FAILURE_STATES:
        return "failure"
    return "pending"


def _render_verify_commands(entries: list[dict]) -> str:
    cmds = [e["cmd"] for e in entries if isinstance(e, dict) and e.get("cmd")]
    if not cmds:
        return ""
    block = "\n".join(cmds)
    return (
        "## Verify commands\n\n"
        "Draft will run the following after your changes. "
        "Run them yourself before finishing if practical.\n\n"
        f"```bash\n{block}\n```"
    )


def _render_check_failures(entries: list[dict]) -> str:
    failed = [e for e in entries if e["state"] == "failure"]
    if not failed:
        return ""
    lines = []
    for e in failed:
        name = " ".join(str(e["name"]).split())
        if e["link"]:
            lines.append(f"- {name} ({e['conclusion']}) {e['link']}")
        else:
            lines.append(f"- {name} ({e['conclusion']})")
    return "## Failing checks\n\n" + "\n".join(lines)


def _build_prompt(ctx, verify_commands: str, failed_checks: list[dict]) -> str:
    template = files("draft.steps.babysit_pr").joinpath("babysit_pr.md").read_text()
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
    return (
        template.replace("{{PR_URL}}", pr_url)
        .replace("{{CHECK_FAILURES}}", _render_check_failures(failed_checks))
        .replace("{{SPEC}}", spec)
        .replace("{{VERIFY_COMMANDS}}", verify_commands)
        .replace("{{VERIFY_ERRORS}}", verify_section)
    )


def _check_ci(pr_url: str) -> tuple[dict[str, int], list[dict]]:
    """Returns (counts, entries); counts keyed by state group: success, failure, pending."""
    result = subprocess.run(
        ["gh", "pr", "checks", pr_url, "--json", "name,state,link"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    raw_entries = json.loads(result.stdout or "[]")
    entries = [
        {
            "name": str(x.get("name", "")),
            "state": _normalise_state(str(x.get("state", ""))),
            "conclusion": str(x.get("state", "")).strip().lower(),
            "link": str(x.get("link", "")),
        }
        for x in raw_entries
    ]
    counts: dict[str, int] = {"success": 0, "failure": 0, "pending": 0}
    for e in entries:
        counts[e["state"]] += 1
    return counts, entries


def _has_changes(cwd: str) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return result.stdout.strip() != ""


def _is_branch_clean(cwd: str) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return result.stdout.strip() == ""


def _run_git_capture(cmd: list[str], cwd: str, timeout: float, log_path: Path) -> str:
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        with open(log_path, "ab") as f:
            f.write(f"$ {' '.join(cmd)}\ntimed out after {timeout}s\n".encode())
        raise StepError("babysit-pr", TIMEOUT_EXIT) from None

    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}\n{stdout}")
        if stderr:
            f.write(stderr)
    if result.returncode != 0:
        raise StepError("babysit-pr", result.returncode)
    return stdout


def _run_git_capture_allow_fail(
    cmd: list[str], cwd: str, timeout: float, log_path: Path
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        with open(log_path, "ab") as f:
            f.write(f"$ {' '.join(cmd)}\ntimed out after {timeout}s\n".encode())
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=TIMEOUT_EXIT,
            stdout=b"",
            stderr=f"timed out after {timeout}s\n".encode(),
        )

    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}\n{stdout}")
        if stderr:
            f.write(stderr)
    return result


def _format_pre_commit_errors(stdout: str, stderr: str) -> str:
    return (
        "## Pre-commit hook failures\n\n$ git commit\n"
        + (stdout + stderr).strip()
        + "\n"
    )


def _generate_commit_message(
    verify_errors: str,
    wt_dir: str,
    log_path: Path,
    timeout: float,
    max_attempts: int,
    engine,
    step_metrics,
) -> tuple[str, bool]:
    template = files("draft.steps.babysit_pr").joinpath("commit_message.md").read_text()
    diff = _run_git_capture(["git", "diff", "HEAD"], wt_dir, 60, log_path)
    status = _run_git_capture(["git", "status", "--porcelain"], wt_dir, 60, log_path)
    diff_section = f"### git diff HEAD\n{diff}\n\n### git status --porcelain\n{status}"
    verify_errors_section = (
        f"### previous verify failures\n{verify_errors}" if verify_errors else ""
    )
    prompt = template.replace("{{DIFF}}", diff_section).replace(
        "{{VERIFY_ERRORS}}", verify_errors_section
    )

    for attempt in range(1, max_attempts + 1):
        result = engine.run_llm(
            prompt=prompt,
            cwd=wt_dir,
            log_path=log_path,
            step_metrics=step_metrics,
            allowed_tools=["Read", "Bash"],
            timeout=timeout,
            attempt=attempt,
        )
        msg = result.final_text.strip()
        if result.rc == 0 and msg:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"--- selected commit message (attempt {attempt}) ---\n{msg}\n\n"
                )
            return msg, False

    fallback = "Fix CI checks"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(
            f"--- commit-message agent exhausted {max_attempts} attempts; falling back to '{fallback}' ---\n\n"
        )
    print(
        f"babysit-pr: commit-message agent failed {max_attempts} times; using fallback '{fallback}'",
        file=sys.stderr,
    )
    return fallback, True


class BabysitPrStep(Step):
    name = "babysit-pr"

    def defaults(self) -> dict:
        return {"max_retries": 100, "timeout": 1200, "checks_delay": 60}

    def run(self, ctx, engine, lifecycle, step_metrics):
        cfg = ctx.config(self.name)
        pr_url = ctx.get("pr_url", "")
        wt_dir = ctx.get("wt_dir")

        engine.sleep(INITIAL_PR_CHECK_DELAY, "waiting before pr-checks")
        with engine.stage(self.name) as s:
            commit_msg_log = ctx.run_dir / "babysit-pr-commit-msg.log"
            verify_commands = _render_verify_commands(
                lifecycle.get_hooks(self.name, "verify")
            )

            for attempt in range(1, cfg["max_retries"] + 1):
                s.update(f"{attempt}/{cfg['max_retries']}")
                pushed_this_iter = False

                try:
                    counts, entries = _check_ci(pr_url)
                except Exception:
                    counts = {"success": 0, "failure": 0, "pending": 1}
                    entries = []

                total = sum(counts.values())
                print(
                    f"CI: {counts['success']}/{total} passed, "
                    f"{counts['failure']} failed, "
                    f"{counts['pending']} pending"
                )

                if (
                    counts["failure"] == 0
                    and counts["pending"] == 0
                    and _is_branch_clean(wt_dir)
                ):
                    ctx.step_set(self.name, "attempts", attempt)
                    ctx.save()
                    s.update(f"green ({attempt} checks)")
                    print(f"PR is green: {pr_url}")
                    return

                if counts["failure"] > 0:
                    failed = [e for e in entries if e["state"] == "failure"]
                    engine.run_llm(
                        prompt=_build_prompt(ctx, verify_commands, failed),
                        cwd=wt_dir,
                        log_path=ctx.log_path(self.name),
                        step_metrics=step_metrics,
                        allowed_tools=["Bash", "Edit", "Write", "Read"],
                        timeout=cfg["timeout"],
                        attempt=attempt,
                    )

                    if not _has_changes(wt_dir):
                        ctx.step_set(
                            self.name,
                            "verify_errors",
                            "agent produced no changes in the working tree; either the "
                            "fix was skipped or the agent committed despite the prompt "
                            "instruction (the babysit prompt forbids commits)",
                        )
                        ctx.step_set(self.name, "attempts", attempt)
                        ctx.save()
                        engine.sleep(cfg["checks_delay"], "waiting before pr-checks")
                        continue

                    results = lifecycle.run_hooks(self.name, "verify")
                    failures = [r for r in results if r.rc != 0]
                    if failures:
                        ctx.step_set(
                            self.name,
                            "verify_errors",
                            "\n\n".join(f"$ {r.cmd}\n{r.output}" for r in failures),
                        )
                        ctx.step_set(self.name, "attempts", attempt)
                        ctx.save()
                        engine.sleep(cfg["checks_delay"], "waiting before pr-checks")
                        continue

                    last_errors = ctx.step_get(self.name, "verify_errors", "")
                    message, used_fallback = _generate_commit_message(
                        verify_errors=last_errors,
                        wt_dir=wt_dir,
                        log_path=commit_msg_log,
                        timeout=120,
                        max_attempts=3,
                        engine=engine,
                        step_metrics=step_metrics,
                    )

                    _run_git_capture(["git", "add", "-A"], wt_dir, 60, commit_msg_log)
                    commit = _run_git_capture_allow_fail(
                        ["git", "commit", "-m", message],
                        wt_dir,
                        60,
                        commit_msg_log,
                    )
                    if commit.returncode != 0:
                        stdout_str = (
                            commit.stdout
                            if isinstance(commit.stdout, str)
                            else commit.stdout.decode("utf-8", errors="replace")
                        )
                        stderr_str = (
                            commit.stderr
                            if isinstance(commit.stderr, str)
                            else commit.stderr.decode("utf-8", errors="replace")
                        )
                        ctx.step_set(
                            self.name,
                            "verify_errors",
                            _format_pre_commit_errors(stdout_str, stderr_str),
                        )
                        ctx.step_set(self.name, "attempts", attempt)
                        ctx.save()
                        engine.sleep(cfg["checks_delay"], "waiting before pr-checks")
                        continue

                    sha = _run_git_capture(
                        ["git", "rev-parse", "HEAD"], wt_dir, 30, commit_msg_log
                    ).strip()
                    ctx.step_set(self.name, "commit_sha", sha)
                    ctx.step_set(self.name, "commit_message_fallback", used_fallback)
                    ctx.step_set(self.name, "verify_errors", "")
                    ctx.step_set(self.name, "attempts", attempt)
                    ctx.save()
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
                next_delay = (
                    INITIAL_PR_CHECK_DELAY if pushed_this_iter else cfg["checks_delay"]
                )
                engine.sleep(next_delay, "waiting before pr-checks")

        print(f"babysit-pr: exhausted attempts. PR: {pr_url}")
