import importlib.util
from pathlib import Path
from unittest import mock

import pytest

# Load scripts/run_agent.py without installing it as a package.
_script_path = Path(__file__).parents[2] / "scripts" / "run_agent.py"
_spec = importlib.util.spec_from_file_location("run_agent", _script_path)
run_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_agent)


class TestResolveTemplatePath:
    def test_absolute_path_returned_unchanged(self, tmp_path):
        p = tmp_path / "prompt.md"
        result = run_agent.resolve_template_path(str(p), "/some/repo")
        assert result == p

    def test_relative_path_joined_with_repo_dir(self, tmp_path):
        result = run_agent.resolve_template_path("templates/review.md", str(tmp_path))
        assert result == tmp_path / "templates" / "review.md"

    def test_relative_path_basename_only(self, tmp_path):
        result = run_agent.resolve_template_path("prompt.md", str(tmp_path))
        assert result == tmp_path / "prompt.md"


class TestFillTemplate:
    def test_replaces_all_vars(self):
        template = "repo={DRAFT_REPO_DIR} branch={DRAFT_BRANCH} base={DRAFT_BASE_BRANCH} spec={DRAFT_SPEC_FILE}"
        env = {
            "DRAFT_REPO_DIR": "/repo",
            "DRAFT_BRANCH": "feature/x",
            "DRAFT_BASE_BRANCH": "main",
            "DRAFT_SPEC_FILE": "/repo/spec.md",
        }
        result = run_agent.fill_template(template, env)
        assert result == "repo=/repo branch=feature/x base=main spec=/repo/spec.md"

    def test_missing_var_replaced_with_empty_string(self):
        result = run_agent.fill_template("branch={DRAFT_BRANCH}", {})
        assert result == "branch="

    def test_no_placeholders_unchanged(self):
        text = "no placeholders here"
        assert run_agent.fill_template(text, {"DRAFT_BRANCH": "main"}) == text

    def test_partial_replacement(self):
        result = run_agent.fill_template(
            "{DRAFT_BRANCH} on {DRAFT_BASE_BRANCH}",
            {"DRAFT_BRANCH": "feat", "DRAFT_BASE_BRANCH": "main"},
        )
        assert result == "feat on main"

    def test_repeated_placeholder(self):
        result = run_agent.fill_template(
            "{DRAFT_BRANCH} and {DRAFT_BRANCH}",
            {"DRAFT_BRANCH": "dev"},
        )
        assert result == "dev and dev"


class TestCheckAuggieAuth:
    def _run_check(self, returncode, output):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=returncode, stdout=output, stderr=""
            )
            run_agent.check_auggie_auth()
        return mock_run

    def test_passes_when_session_present(self):
        self._run_check(0, "SESSION=abc123")  # should not raise

    def test_exits_when_returncode_nonzero(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=1, stdout="", stderr="error"
            )
            with pytest.raises(SystemExit) as exc:
                run_agent.check_auggie_auth()
        assert exc.value.code == 1

    def test_exits_when_session_missing(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0, stdout="no session here", stderr=""
            )
            with pytest.raises(SystemExit) as exc:
                run_agent.check_auggie_auth()
        assert exc.value.code == 1


class TestRunAuggie:
    def test_returns_stdout_on_success(self, tmp_path):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0, stdout="FOUND_ISSUES\n\n## issue\n"
            )
            result = run_agent.run_auggie("my prompt", "gpt-4.1", str(tmp_path))
        assert result == "FOUND_ISSUES\n\n## issue\n"

    def test_exits_on_auggie_failure(self, tmp_path):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=2, stdout="")
            with pytest.raises(SystemExit) as exc:
                run_agent.run_auggie("prompt", "gpt-4.1", str(tmp_path))
        assert exc.value.code == 2

    def test_passes_correct_args(self, tmp_path):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0, stdout="NO_ISSUES")
            run_agent.run_auggie("prompt", "gpt-4.1", "/repo")

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "auggie"
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "gpt-4.1"
        assert "--workspace-root" in cmd
        ws_idx = cmd.index("--workspace-root")
        assert cmd[ws_idx + 1] == "/repo"


class TestMain:
    def _base_env(self, tmp_path):
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("the spec")
        template_file = tmp_path / "prompt.md"
        template_file.write_text("branch={DRAFT_BRANCH}")
        return {
            "DRAFT_REPO_DIR": str(tmp_path),
            "DRAFT_BRANCH": "feat",
            "DRAFT_BASE_BRANCH": "main",
            "DRAFT_SPEC_FILE": str(spec_file),
        }, template_file

    def test_approval_produces_no_output(self, tmp_path, capsys):
        env, tmpl = self._base_env(tmp_path)
        with (
            mock.patch.dict("os.environ", env),
            mock.patch("sys.argv", ["run_agent.py", str(tmpl), "gpt-4.1"]),
            mock.patch.object(run_agent, "check_auggie_auth"),
            mock.patch.object(run_agent, "run_auggie", return_value="NO_ISSUES"),
        ):
            run_agent.main()
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_rejection_prints_found_issues(self, tmp_path, capsys):
        env, tmpl = self._base_env(tmp_path)
        auggie_out = "FOUND_ISSUES\n\n## bad thing\n"
        with (
            mock.patch.dict("os.environ", env),
            mock.patch("sys.argv", ["run_agent.py", str(tmpl), "gpt-4.1"]),
            mock.patch.object(run_agent, "check_auggie_auth"),
            mock.patch.object(run_agent, "run_auggie", return_value=auggie_out),
        ):
            run_agent.main()
        captured = capsys.readouterr()
        assert "FOUND_ISSUES" in captured.out

    def test_missing_env_var_exits(self, tmp_path):
        env, tmpl = self._base_env(tmp_path)
        env.pop("DRAFT_BRANCH")
        with (
            mock.patch.dict("os.environ", env, clear=True),
            mock.patch("sys.argv", ["run_agent.py", str(tmpl), "gpt-4.1"]),
            pytest.raises(SystemExit),
        ):
            run_agent.main()

    def test_missing_template_exits(self, tmp_path):
        env, _ = self._base_env(tmp_path)
        with (
            mock.patch.dict("os.environ", env),
            mock.patch("sys.argv", ["run_agent.py", "nonexistent.md", "gpt-4.1"]),
            pytest.raises(SystemExit),
        ):
            run_agent.main()

    def test_wrong_arg_count_exits(self, tmp_path):
        env, _ = self._base_env(tmp_path)
        with (
            mock.patch.dict("os.environ", env),
            mock.patch("sys.argv", ["run_agent.py", "only-one-arg"]),
            pytest.raises(SystemExit),
        ):
            run_agent.main()
