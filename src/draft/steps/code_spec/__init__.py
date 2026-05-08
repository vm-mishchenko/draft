import json
import subprocess
from importlib.resources import files

from pipeline import Step, StepError


def _format_event(line):
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return line.rstrip("\n") or None

    kind = event.get("type")

    if kind == "assistant":
        parts = []
        for block in event.get("message", {}).get("content", []):
            bt = block.get("type")
            if bt == "text":
                text = (block.get("text") or "").strip()
                if text:
                    parts.append(f"[text] {_first_line(text)}")
            elif bt == "thinking":
                thought = (block.get("thinking") or "").strip()
                if thought:
                    parts.append(f"[think] {_first_line(thought)}")
            elif bt == "tool_use":
                name = block.get("name", "?")
                summary = _summarize_tool_input(name, block.get("input") or {})
                parts.append(f"[tool] {name}({summary})")
        return "\n".join(parts) or None

    if kind == "user":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict)
                    )
                text = str(content).strip()
                if text:
                    return f"[ok]   {_first_line(text)[:120]}"
        return None

    if kind == "system":
        subtype = event.get("subtype") or "event"
        return f"[sys]  {subtype}"

    if kind == "result":
        cost = event.get("total_cost_usd")
        duration = event.get("duration_ms")
        bits = []
        if duration is not None:
            bits.append(f"{duration / 1000:.1f}s")
        if cost is not None:
            bits.append(f"${cost:.4f}")
        return "[done] " + " ".join(bits) if bits else "[done]"

    return None


def _first_line(text):
    return text.splitlines()[0]


def _summarize_tool_input(name, inp):
    if name in ("Read", "Write", "Edit"):
        return inp.get("file_path", "")
    if name == "Bash":
        cmd = (inp.get("command") or "")
        return _first_line(cmd)[:100] if cmd else ""
    if name == "Grep":
        return repr(inp.get("pattern", ""))
    if name == "Glob":
        return repr(inp.get("pattern", ""))
    if name == "TodoWrite":
        todos = inp.get("todos") or []
        return f"{len(todos)} todos"
    compact = json.dumps(inp, ensure_ascii=False)
    return compact[:100]


def _build_claude_cmd(ctx) -> list[str]:
    spec = ctx.get("spec", "")
    verify_errors = ctx.step_get("implement-spec", "verify_errors", "")
    template = files("draft.steps.code_spec").joinpath("code_spec.md").read_text()
    if verify_errors:
        verify_section = f"## Test failures\n\n{verify_errors}\n\nFix the above failures before committing."
    else:
        verify_section = ""
    prompt = template.replace("{{SPEC}}", spec).replace("{{VERIFY_ERRORS}}", verify_section)
    return ["claude", "-p", prompt, "--allowedTools", "Bash,Edit,Write,Read", "--output-format", "stream-json", "--verbose"]


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
    name = "implement-spec"

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
                line_formatter=_format_event,
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
