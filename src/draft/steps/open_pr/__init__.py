import contextlib
import re
import subprocess
import sys
from pathlib import Path

from pipeline import Step, StepError
from pipeline.runner import TIMEOUT_EXIT

STEP_DIR = Path(__file__).parent


class _ParseError(Exception):
    pass


def _select_body_path(cfg: dict) -> Path:
    raw = cfg.get("pr_body_template")
    return Path(raw) if raw else STEP_DIR / "pull-request-template.md"


def _parse_title_body(text: str) -> tuple[str, str]:
    title_match = re.search(r"<<<PR-TITLE>>>\n(.*?)\n<<</PR-TITLE>>>", text, re.DOTALL)
    if title_match is None:
        raise _ParseError("no PR-TITLE block")
    title = title_match.group(1).strip()
    if not title:
        raise _ParseError("empty title")

    body_match = re.search(r"<<<PR-BODY>>>\n(.*?)\n<<</PR-BODY>>>", text, re.DOTALL)
    if body_match is None:
        raise _ParseError("no PR-BODY block")
    body = body_match.group(1)

    return title, body


def _run_git_capture(cmd: list[str], cwd: str, timeout: float, log_path: Path) -> str:
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        with open(log_path, "ab") as f:
            f.write(f"$ {' '.join(cmd)}\ntimed out after {timeout}s\n".encode())
        print(f"open-pr: {' '.join(cmd)} timed out after {timeout}s", file=sys.stderr)
        raise StepError("open-pr", TIMEOUT_EXIT) from None

    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}\n{stdout}")
        if stderr:
            f.write(stderr)
    if result.returncode != 0:
        print(f"open-pr: {' '.join(cmd)} exited {result.returncode}", file=sys.stderr)
        if stderr:
            print(stderr, file=sys.stderr)
        raise StepError("open-pr", result.returncode)
    return stdout


class OpenPrStep(Step):
    name = "open-pr"

    def defaults(self) -> dict:
        return {"timeout": 300, "title_prefix": ""}

    def run(self, ctx, engine, lifecycle, step_metrics):
        cfg = ctx.config(self.name)
        branch = ctx.get("branch", "")
        base_branch = ctx.get("base_branch", "main")
        title_prefix = cfg.get("title_prefix", "")
        wt_dir = ctx.get("wt_dir")

        body_path = _select_body_path(cfg)
        if not body_path.is_file():
            print(f"open-pr: pr_body_template missing: {body_path}", file=sys.stderr)
            raise StepError(self.name, 1)
        template_text = body_path.read_text(encoding="utf-8", errors="replace")

        claude_log = ctx.log_path("open-pr-claude")
        log_path = ctx.log_path(self.name)
        gh_base = base_branch.removeprefix("origin/")

        with engine.stage(self.name) as s:
            s.update("gathering context")
            git_diff = _run_git_capture(
                ["git", "diff", f"{base_branch}..HEAD"],
                wt_dir,
                cfg["timeout"],
                ctx.log_path("open-pr-git-diff"),
            )
            git_log = _run_git_capture(
                ["git", "log", f"{base_branch}..HEAD", "--format=%s%n%n%b"],
                wt_dir,
                cfg["timeout"],
                ctx.log_path("open-pr-git-log"),
            )

            spec_path = ctx.get("spec", "")
            spec_text = ""
            if spec_path:
                with contextlib.suppress(OSError):
                    spec_text = Path(spec_path).read_text(
                        encoding="utf-8", errors="replace"
                    )

            prompt = (
                (STEP_DIR / "open_pr.md")
                .read_text()
                .replace("{{PR_BODY_TEMPLATE}}", template_text)
                .replace("{{GIT_DIFF}}", git_diff)
                .replace("{{GIT_LOG}}", git_log)
                .replace("{{SPEC}}", spec_text)
            )

            s.update("drafting")
            result = engine.run_llm(
                prompt=prompt,
                cwd=wt_dir,
                log_path=claude_log,
                step_metrics=step_metrics,
                allowed_tools=[],
                extra_args=["--permission-mode", "acceptEdits"],
                timeout=cfg["timeout"],
            )
            if result.rc != 0:
                raise StepError(self.name, result.rc)

            try:
                title, body = _parse_title_body(result.final_text)
            except _ParseError as e:
                print(
                    f"open-pr: agent output unparseable ({e}); falling back to branch-name title",
                    file=sys.stderr,
                )
                title = branch
                body = (STEP_DIR / "pull-request-template.md").read_text()

            s.update("creating PR")
            rc = engine.run_command(
                cmd=[
                    "gh",
                    "pr",
                    "create",
                    "--base",
                    gh_base,
                    "--title",
                    title_prefix + title,
                    "--body",
                    body,
                    "--draft",
                ],
                cwd=wt_dir,
                log_path=log_path,
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
