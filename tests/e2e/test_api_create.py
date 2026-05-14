import subprocess
from pathlib import Path

import pytest

from draft.api import CreateParams, create
from draft.errors import DraftError
from pipeline.runner import LLMResult


class FakeLLM:
    """Scripted LLM: each call returns the next response and applies any file edits."""

    def __init__(self, responses: list[dict]):
        self._responses = responses
        self.calls: list[dict] = []

    def run(
        self,
        prompt: str,
        cwd,
        log_path,
        *,
        allowed_tools=(),
        extra_args=(),
        timeout=None,
    ) -> LLMResult:
        idx = len(self.calls)
        self.calls.append({"prompt": prompt, "cwd": str(cwd)})
        resp = self._responses[idx] if idx < len(self._responses) else {}
        for name, content in resp.get("files", {}).items():
            target = Path(cwd) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return LLMResult(rc=0, final_text=resp.get("text", ""))


@pytest.fixture
def git_repo(tmp_path):
    """Fresh git repo in a subdirectory, leaving tmp_path free for spec files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    (repo / "README.md").write_text("# repo\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")

    sha = _git_out(repo, "rev-parse", "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", sha)

    _git(repo, "checkout", "-b", "feature-test")
    _git(repo, "checkout", "main")
    return repo


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _git_out(cwd, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_api_create_with_fake_llm(git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(git_repo)

    spec = tmp_path / "spec.md"
    spec.write_text("Add a hello.txt file with 'hello world'.\n")

    fake_llm = FakeLLM(
        [
            # Call 1: implement-spec — write a file to the worktree
            {"files": {"hello.txt": "hello world\n"}, "text": "Created hello.txt"},
            # Call 2: suggest_checks — return empty list to skip extra checks
            {"text": "[]"},
            # Call 3: commit-message generation
            {"text": "Add hello.txt with hello world content"},
        ]
    )

    params = CreateParams(
        spec_path=str(spec),
        skip_pr=True,
        branch="feature-test",
        no_worktree=True,
        run_id="test-api-create-001",
    )

    result = create(params, llm=fake_llm)

    assert result.run_id == "test-api-create-001"
    assert result.branch == "feature-test"
    assert result.worktree_mode == "no-worktree"
    assert len(fake_llm.calls) == 3

    # Verify commit was created on feature-test
    log = _git_out(git_repo, "log", "--oneline", "feature-test")
    commits = log.strip().splitlines()
    assert len(commits) == 2  # init + our commit
    assert "hello" in commits[0].lower() or "add" in commits[0].lower()

    # Verify hello.txt exists in the committed tree
    files = _git_out(git_repo, "show", "--name-only", "--format=", "feature-test")
    assert "hello.txt" in files


def test_api_create_missing_spec_raises(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)

    params = CreateParams(skip_pr=True)

    with pytest.raises(DraftError) as exc_info:
        create(params, llm=FakeLLM([]))

    assert exc_info.value.exit_code == 2


def test_api_create_branch_from_conflict_raises(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)

    spec = git_repo / "spec.md"
    spec.write_text("spec")

    params = CreateParams(
        spec_path=str(spec),
        branch="feature-test",
        from_branch="main",
        skip_pr=True,
    )

    with pytest.raises(DraftError) as exc_info:
        create(params, llm=FakeLLM([]))

    assert exc_info.value.exit_code == 2
    assert "--branch" in str(exc_info.value)
    assert "--from" in str(exc_info.value)
