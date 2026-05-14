import contextlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from draft.hooks import HookResult, _run_hook_cmd
from draft.steps.implement_spec import original_spec
from draft.steps.implement_spec._live_status import LiveStatusSummarizer
from pipeline import Step, StepError
from pipeline.runner import TIMEOUT_EXIT


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
    template = (
        files("draft.steps.implement_spec").joinpath("verify_commands.md").read_text()
    )
    return template.replace("{{COMMANDS}}", block)


def _render_prompt(ctx, template: str, verify_commands: str) -> str:
    spec = ctx.get("spec", "")
    spec_section = f"## Current Spec\n\n{spec}"
    verify_errors = ctx.step_get("implement-spec", "verify_errors", "")
    if verify_errors:
        verify_template = (
            files("draft.steps.implement_spec").joinpath("verify_errors.md").read_text()
        )
        verify_section = verify_template.replace("{{ERRORS}}", verify_errors)
    else:
        verify_section = ""
    original_spec_section = original_spec.render_original_spec(ctx)
    return (
        template.replace("{{VERIFY_COMMANDS}}", verify_commands)
        .replace("{{ORIGINAL_SPEC}}", original_spec_section)
        .replace("{{SPEC}}", spec_section)
        .replace("{{VERIFY_ERRORS}}", verify_section)
    )


def _log_prompt(log_path, prompt: str, attempt: int, max_attempts: int) -> None:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    block = (
        f"=== implement-spec prompt (attempt {attempt}/{max_attempts}) @ {ts} ===\n"
        f"{prompt}\n"
        f"=== end prompt ===\n\n"
    )
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(block)
    except OSError as exc:
        print(f"warning: could not write prompt log {log_path}: {exc}", file=sys.stderr)


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


def _normalize_cmd(cmd: str) -> str:
    return re.sub(r"\s+", " ", cmd).strip()


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
        timeout_val = entry.get("timeout")
        new_entry: dict = {"cmd": cmd}
        if timeout_val is not None:
            try:
                t = int(timeout_val)
            except (ValueError, TypeError):
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
    ctx,
    engine,
    step_metrics,
    cfg: dict,
    spec: str,
    wt_dir: str,
    static_cmds: list[str],
    suggest_template: str,
) -> list[dict]:
    suggest_log = ctx.run_dir / "implement-spec.suggest.log"
    changed_files = _run_git_capture(
        ["git", "diff", "--name-status", "HEAD"], wt_dir, 60, suggest_log
    )
    if static_cmds:
        static_section = "\n".join(f"- {c}" for c in static_cmds)
    else:
        static_section = "(none)"
    prompt = (
        suggest_template.replace("{{SPEC}}", spec)
        .replace("{{CHANGED_FILES}}", changed_files)
        .replace("{{STATIC_CHECKS}}", static_section)
        .replace("{{PER_CHECK_TIMEOUT}}", str(cfg["per_check_timeout"]))
    )
    result = engine.run_llm(
        prompt=prompt,
        cwd=wt_dir,
        log_path=suggest_log,
        step_metrics=step_metrics,
        allowed_tools=["Read"],
        timeout=cfg["suggester_timeout"],
    )
    parsed = _parse_suggestions(result.final_text)
    filtered = _filter_dupes(parsed, static_cmds)
    return filtered[: cfg["max_checks"]]


def _run_suggested_checks(
    suggested: list[dict],
    wt_dir: str,
    run_dir: Path,
    engine,
    cfg: dict,
    stage,
) -> list[HookResult]:
    log_path = run_dir / "implement-spec.suggested.log"
    failures: list[HookResult] = []
    elapsed = 0.0

    with contextlib.ExitStack() as stack:
        try:
            log_fd = stack.enter_context(open(log_path, "a", encoding="utf-8"))
        except OSError as exc:
            print(
                f"warning: could not write suggested log {log_path}: {exc}",
                file=sys.stderr,
            )
            log_fd = None

        for i, entry in enumerate(suggested):
            if elapsed >= cfg["suggester_total_budget"]:
                if log_fd:
                    log_fd.write("--- skipped (budget exhausted) ---\n\n")
                    log_fd.flush()
                break

            cmd = entry["cmd"]
            timeout = min(
                int(entry.get("timeout") or cfg["per_check_timeout"]),
                cfg["per_check_timeout"],
            )
            if log_fd:
                ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                log_fd.write(f"=== implement-spec.suggested[{i}] @ {ts} ===\n")
                log_fd.write(f"$ {cmd}\n")
                log_fd.flush()

            stage.update(f"suggested check {i + 1}/{len(suggested)}: {cmd}")
            result = _run_hook_cmd(cmd, timeout, wt_dir)

            if log_fd:
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


class ImplementSpecStep(Step):
    name = "implement-spec"

    def defaults(self) -> dict:
        return {
            "max_retries": 10,
            "timeout": 1200,
            "suggest_extra_checks": True,
            "max_checks": 5,
            "per_check_timeout": 120,
            "suggester_timeout": 120,
            "suggester_total_budget": 300,
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

            verify_hook_entries = lifecycle.get_hooks(self.name, "verify")
            verify_commands = _render_verify_commands(verify_hook_entries)
            static_cmds = [
                e["cmd"]
                for e in verify_hook_entries
                if isinstance(e, dict) and e.get("cmd")
            ]

            suggest_template = None
            if cfg["suggest_extra_checks"]:
                suggest_template = _load_suggest_template()

            for attempt in range(1, cfg["max_retries"] + 1):
                prefix = (
                    "" if attempt == 1 else f"attempt {attempt}/{cfg['max_retries']} — "
                )
                s.update(prefix + "implementing")
                summarizer = None
                if sys.stdout.isatty():
                    summarizer = LiveStatusSummarizer(
                        handle=s,
                        engine=engine,
                        step_metrics=step_metrics,
                        log_path=ctx.log_path(self.name),
                        prefix=prefix,
                    ).start()
                try:
                    prompt = _render_prompt(ctx, impl_template, verify_commands)
                    _log_prompt(
                        ctx.log_path(self.name),
                        prompt,
                        attempt=attempt,
                        max_attempts=cfg["max_retries"],
                    )
                    engine.run_llm(
                        prompt=prompt,
                        cwd=wt_dir,
                        log_path=ctx.log_path(self.name),
                        step_metrics=step_metrics,
                        allowed_tools=["Bash", "Edit", "Write", "Read"],
                        timeout=cfg["timeout"],
                    )
                finally:
                    if summarizer is not None:
                        summarizer.stop()

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

                if cfg["suggest_extra_checks"]:
                    s.update(f"{prefix}suggesting checks")
                    suggested = _suggest_checks(
                        ctx,
                        engine,
                        step_metrics,
                        cfg,
                        spec,
                        wt_dir,
                        static_cmds,
                        suggest_template,
                    )
                    ctx.step_set(self.name, "suggested_checks", suggested)
                    ctx.save()

                    s.update(f"{prefix}running suggested checks")
                    suggest_failures = _run_suggested_checks(
                        suggested, wt_dir, ctx.run_dir, engine, cfg, s
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
                if cfg["suggest_extra_checks"]:
                    ctx.step_set(self.name, "suggested_checks", [])
                ctx.save()
                s.update("ok")
                return

            raise StepError(self.name, 1)
