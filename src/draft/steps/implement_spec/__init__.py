import contextlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from draft.hooks import HookResult, _run_hook_cmd, _status_text
from pipeline import Step, StepError
from pipeline.runner import TIMEOUT_EXIT

_MAX_CHECKS = 5
_PER_CHECK_TIMEOUT = 120
_SUGGESTER_TIMEOUT = 120
_SUGGESTER_TOTAL_BUDGET = 300


def _load_template(cfg: dict) -> str:
    path = cfg.get("prompt_template")
    if path:
        return Path(path).read_text(encoding="utf-8")
    return files("draft.steps.implement_spec").joinpath("implement_spec.md").read_text()


def _load_suggest_template() -> str:
    return files("draft.steps.implement_spec").joinpath("suggest_checks.md").read_text()


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


def _normalize_cmd(cmd: str) -> str:
    return " ".join(cmd.split())


def _parse_suggestions(text: str) -> list[dict]:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    result = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        cmd = entry.get("cmd")
        if not isinstance(cmd, str) or not cmd:
            continue
        new_entry: dict = {"cmd": cmd}
        timeout = entry.get("timeout")
        if timeout is not None:
            try:
                t = int(timeout)
            except (TypeError, ValueError):
                continue
            if t <= 0:
                continue
            new_entry["timeout"] = t
        result.append(new_entry)
    return result


def _filter_dupes(suggested: list[dict], static_cmds: list[str]) -> list[dict]:
    normalized_static = {_normalize_cmd(c) for c in static_cmds}
    return [e for e in suggested if _normalize_cmd(e["cmd"]) not in normalized_static]


def _format_suggested_failures(failures: list[HookResult]) -> str:
    parts = "\n\n".join(f"$ {r.cmd}\n{r.output}" for r in failures)
    return f"## Suggested check failures\n\n{parts}"


def _suggest_checks(
    ctx, engine, step_metrics, spec, wt_dir, static_cmds, suggest_template: str
) -> list[dict]:
    suggest_log = ctx.run_dir / "implement-spec.suggest.log"
    changed_files = _run_git_capture(
        ["git", "diff", "--name-status", "HEAD"], wt_dir, 60, suggest_log
    )
    static_bullets = "\n".join(f"- {cmd}" for cmd in static_cmds) or "(none)"
    prompt = (
        suggest_template.replace("{{SPEC}}", spec)
        .replace("{{CHANGED_FILES}}", changed_files.strip())
        .replace("{{STATIC_CHECKS}}", static_bullets)
    )
    result = engine.run_llm(
        prompt=prompt,
        cwd=wt_dir,
        log_path=suggest_log,
        step_metrics=step_metrics,
        allowed_tools=["Read"],
        timeout=_SUGGESTER_TIMEOUT,
    )
    return _filter_dupes(_parse_suggestions(result.final_text), static_cmds)[
        :_MAX_CHECKS
    ]


def _run_suggested_checks(
    suggested: list[dict], wt_dir: str, run_dir: Path, engine
) -> list[HookResult]:
    log_path = run_dir / "implement-spec.suggested.log"
    failures: list[HookResult] = []
    elapsed = 0.0

    with contextlib.ExitStack() as stack:
        try:
            log_fd = stack.enter_context(open(log_path, "a"))
        except OSError as exc:
            print(
                f"warning: could not write suggested log {log_path}: {exc}",
                file=sys.stderr,
            )
            log_fd = None

        for i, entry in enumerate(suggested):
            if elapsed >= _SUGGESTER_TOTAL_BUDGET:
                if log_fd is not None:
                    log_fd.write("--- skipped (budget exhausted) ---\n")
                    log_fd.flush()
                break

            timeout = min(
                int(entry.get("timeout") or _PER_CHECK_TIMEOUT),
                _PER_CHECK_TIMEOUT,
            )
            label = f"implement-spec.suggested[{i}] {entry['cmd']}"

            if log_fd is not None:
                ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                log_fd.write(f"=== implement-spec.suggested[{i}] @ {ts} ===\n")
                log_fd.write(f"$ {entry['cmd']}\n")
                log_fd.flush()

            with engine.tty_ticker(label) as set_status:
                result = _run_hook_cmd(entry["cmd"], timeout, wt_dir)
                set_status(_status_text(result.rc))

            if log_fd is not None:
                if result.output:
                    log_fd.write(result.output)
                    if not result.output.endswith("\n"):
                        log_fd.write("\n")
                log_fd.write(f"--- exit {result.rc} in {result.duration:.1f}s ---\n\n")
                log_fd.flush()

            elapsed += result.duration

            if result.rc != 0:
                failures.append(result)
                break

    return failures


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
        return {
            "max_retries": 10,
            "timeout": 1200,
        }

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

            suggest_template = _load_suggest_template()

            verify_hook_entries = lifecycle.get_hooks(self.name, "verify")
            verify_commands = _render_verify_commands(verify_hook_entries)
            static_cmds = [
                e["cmd"]
                for e in verify_hook_entries
                if isinstance(e, dict) and e.get("cmd")
            ]

            for attempt in range(1, cfg["max_retries"] + 1):
                prefix = (
                    f"attempt {attempt}/{cfg['max_retries']} — " if attempt > 1 else ""
                )
                s.update(f"{prefix}implementing")
                engine.run_llm(
                    prompt=_render_prompt(ctx, impl_template, verify_commands),
                    cwd=wt_dir,
                    log_path=ctx.log_path(self.name),
                    step_metrics=step_metrics,
                    allowed_tools=["Bash", "Edit", "Write", "Read"],
                    timeout=cfg["timeout"],
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

                s.update(f"{prefix}verifying")
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

                s.update(f"{prefix}suggesting checks")
                suggested = _suggest_checks(
                    ctx,
                    engine,
                    step_metrics,
                    spec,
                    wt_dir,
                    static_cmds,
                    suggest_template,
                )
                ctx.step_set(self.name, "suggested_checks", suggested)
                ctx.save()

                s.update(f"{prefix}running suggested checks")
                suggest_failures = _run_suggested_checks(
                    suggested, wt_dir, ctx.run_dir, engine
                )
                if suggest_failures:
                    ctx.step_set(
                        self.name,
                        "verify_errors",
                        _format_suggested_failures(suggest_failures),
                    )
                    ctx.save()
                    continue

                s.update(f"{prefix}writing commit")
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
                ctx.step_set(self.name, "suggested_checks", [])
                ctx.save()
                return

            raise StepError(self.name, 1)
