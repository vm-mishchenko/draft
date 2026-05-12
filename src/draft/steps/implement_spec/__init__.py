import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from pipeline import Step, StepError
from pipeline.runner import TIMEOUT_EXIT


def _load_template(cfg: dict) -> str:
    path = cfg.get("prompt_template")
    if path:
        return Path(path).read_text(encoding="utf-8")
    return files("draft.steps.implement_spec").joinpath("implement_spec.md").read_text()


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


def _render_prompt(ctx, template: str, verify_commands: str) -> str:
    spec = ctx.get("spec", "")
    verify_errors = ctx.step_get("implement-spec", "verify_errors", "")
    if verify_errors:
        verify_section = f"## Test failures\n\n{verify_errors}\n\nFix the above failures before committing."
    else:
        verify_section = ""
    return (
        template.replace("{{SPEC}}", spec)
        .replace("{{VERIFY_COMMANDS}}", verify_commands)
        .replace("{{VERIFY_ERRORS}}", verify_section)
    )


def _has_changes(cwd: str) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return result.stdout.strip() != ""


def _run_git_capture(cmd: list[str], cwd: str, timeout: float, log_path: Path) -> str:
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        with open(log_path, "ab") as f:
            f.write(f"$ {' '.join(cmd)}\ntimed out after {timeout}s\n".encode())
        raise StepError("implement-spec", TIMEOUT_EXIT) from None

    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}\n{stdout}")
        if stderr:
            f.write(stderr)
    if result.returncode != 0:
        raise StepError("implement-spec", result.returncode)
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


def _generate_commit_message(
    spec: str,
    wt_dir: str,
    log_path: Path,
    timeout: float,
    max_attempts: int,
    engine,
    step_metrics,
) -> tuple[str, bool]:
    template = (
        files("draft.steps.implement_spec").joinpath("commit_message.md").read_text()
    )
    diff = _run_git_capture(["git", "diff", "HEAD"], wt_dir, 60, log_path)
    status = _run_git_capture(["git", "status", "--porcelain"], wt_dir, 60, log_path)
    diff_section = f"### git diff HEAD\n{diff}\n\n### git status --porcelain\n{status}"
    prompt = template.replace("{{SPEC}}", spec).replace("{{DIFF}}", diff_section)

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

    fallback = "Implement spec"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(
            f"--- commit-message agent exhausted {max_attempts} attempts; falling back to '{fallback}' ---\n\n"
        )
    print(
        f"implement-spec: commit-message agent failed {max_attempts} times; using fallback '{fallback}'",
        file=sys.stderr,
    )
    return fallback, True


def _format_pre_commit_errors(stdout: str, stderr: str) -> str:
    return (
        "## Pre-commit hook failures\n\n$ git commit\n"
        + (stdout + stderr).strip()
        + "\n"
    )


class ImplementSpecStep(Step):
    name = "implement-spec"

    def defaults(self) -> dict:
        return {"max_retries": 10, "timeout": 1200}

    def run(self, ctx, engine, lifecycle, step_metrics):
        cfg = ctx.config(self.name)
        wt_dir = ctx.get("wt_dir")
        spec = ctx.get("spec", "")
        commit_msg_log = ctx.run_dir / "implement-spec-commit-msg.log"

        with engine.stage(self.name) as s:
            try:
                impl_template = _load_template(cfg)
            except OSError as exc:
                print(f"error: cannot read prompt_template: {exc}", file=sys.stderr)
                raise StepError(self.name, 1) from exc

            verify_commands = _render_verify_commands(
                lifecycle.get_hooks(self.name, "verify")
            )

            for attempt in range(1, cfg["max_retries"] + 1):
                s.update(f"attempt {attempt}/{cfg['max_retries']} — implementing")
                engine.run_llm(
                    prompt=_render_prompt(ctx, impl_template, verify_commands),
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
                        "implementation was skipped or the agent committed despite "
                        "the prompt instruction (the implementation prompt forbids commits)",
                    )
                    ctx.save()
                    continue

                s.update(f"attempt {attempt}/{cfg['max_retries']} — verifying")
                results = lifecycle.run_hooks(self.name, "verify")
                failures = [r for r in results if r.rc != 0]
                if failures:
                    ctx.step_set(
                        self.name,
                        "verify_errors",
                        "\n\n".join(f"$ {r.cmd}\n{r.output}" for r in failures),
                    )
                    ctx.save()
                    continue

                s.update(f"attempt {attempt}/{cfg['max_retries']} — writing commit")
                message, used_fallback = _generate_commit_message(
                    spec=spec,
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
                    ctx.save()
                    continue

                sha = _run_git_capture(
                    ["git", "rev-parse", "HEAD"], wt_dir, 30, commit_msg_log
                ).strip()
                ctx.step_set(self.name, "commit_sha", sha)
                ctx.step_set(self.name, "commit_message_fallback", used_fallback)
                ctx.step_set(self.name, "verify_errors", "")
                ctx.save()
                return

            raise StepError(self.name, 1)
