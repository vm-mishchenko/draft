import re
import sys
from pathlib import Path

from pipeline import Step, StepError

STEP_DIR = Path(__file__).parent


class _ParseError(Exception):
    pass


def _resolve_pr_body_path(repo: str) -> str:
    candidates = [
        Path(repo) / ".draft" / "pull-request-template.md",
        Path.home() / ".draft" / "pull-request-template.md",
        STEP_DIR / "pull-request-template.md",
    ]
    for path in candidates:
        if path.is_file():
            return str(path.resolve())
    raise FileNotFoundError("pull-request-template.md not found")


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


class PrOpenStep(Step):
    name = "open-pr"

    def defaults(self) -> dict:
        return {"max_retries": 1, "timeout": 300, "retry_delay": 0, "title_prefix": ""}

    def run(self, ctx, engine, lifecycle):
        cfg = ctx.config(self.name)
        repo = ctx.get("repo", "")
        branch = ctx.get("branch", "")
        base_branch = ctx.get("base_branch", "main")
        title_prefix = cfg.get("title_prefix", "")
        wt_dir = ctx.get("wt_dir")

        body_path = _resolve_pr_body_path(repo)
        prompt = (
            (STEP_DIR / "open-pr.md")
            .read_text()
            .replace("{{BASE_BRANCH}}", base_branch)
            .replace("{{PR_BODY_TEMPLATE_PATH}}", body_path)
        )

        claude_log = ctx.log_path("open-pr-claude")
        log_path = ctx.log_path(self.name)
        gh_base = base_branch.removeprefix("origin/")

        with engine.stage(self.name) as s:
            s.update("drafting")
            rc = engine.run_command(
                cmd=["claude", "-p", prompt, "--permission-mode", "acceptEdits"],
                cwd=wt_dir,
                log_path=claude_log,
                attempt=1,
                timeout=cfg["timeout"],
            )
            if rc != 0:
                raise StepError(self.name, rc)

            try:
                title, body = _parse_title_body(claude_log.read_text())
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
                    "gh", "pr", "create",
                    "--base", gh_base,
                    "--title", title_prefix + title,
                    "--body", body,
                    "--draft",
                ],
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
