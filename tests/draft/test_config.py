import textwrap
from pathlib import Path

import pytest

from draft.config import ConfigError, load_config, resolve_prompt_template, step_config, validate_config


def test_load_config_merges_global_and_project(tmp_path):
    global_dir = tmp_path / "home" / ".draft"
    global_dir.mkdir(parents=True)
    (global_dir / "config.yaml").write_text(textwrap.dedent("""\
        steps:
          code-spec:
            max_retries: 3
          push:
            timeout: 60
    """))

    repo_dir = tmp_path / "repo"
    project_dir = repo_dir / ".draft"
    project_dir.mkdir(parents=True)
    (project_dir / "config.yaml").write_text(textwrap.dedent("""\
        steps:
          code-spec:
            max_retries: 7
    """))

    # patch Path.home() won't work easily; test _deep_merge directly via load_config internals
    from draft import config as cfg_module
    import unittest.mock as mock

    with mock.patch.object(Path, "home", return_value=tmp_path / "home"):
        result = load_config(str(repo_dir))

    # project wins
    assert result["steps"]["code-spec"]["max_retries"] == 7
    # global-only key preserved
    assert result["steps"]["push"]["timeout"] == 60


def test_load_config_malformed_yaml_raises(tmp_path):
    repo_dir = tmp_path / "repo"
    project_dir = repo_dir / ".draft"
    project_dir.mkdir(parents=True)
    (project_dir / "config.yaml").write_text("steps: [invalid: yaml: here")

    import unittest.mock as mock
    with mock.patch.object(Path, "home", return_value=tmp_path / "nonexistent"):
        with pytest.raises(ConfigError):
            load_config(str(repo_dir))


def test_step_config_merges_defaults_and_overrides():
    config = {"steps": {"code-spec": {"max_retries": 5, "timeout": 600}}}
    defaults = {"max_retries": 10, "timeout": 1200, "retry_delay": 0}
    result = step_config(config, "code-spec", defaults)
    assert result == {"max_retries": 5, "timeout": 600, "retry_delay": 0}


def test_step_config_strips_hooks():
    config = {
        "steps": {
            "code-spec": {
                "max_retries": 5,
                "hooks": {"pre": [{"cmd": "echo hi"}]},
            }
        }
    }
    defaults = {"max_retries": 10, "timeout": None, "retry_delay": 0}
    result = step_config(config, "code-spec", defaults)
    assert "hooks" not in result
    assert result["max_retries"] == 5


def test_step_config_no_overrides_uses_defaults():
    config = {}
    defaults = {"max_retries": 1, "timeout": None, "retry_delay": 0}
    result = step_config(config, "missing-step", defaults)
    assert result == defaults


# --- validate_config ---

def test_validate_config_accepts_cmd_only():
    validate_config({"steps": {"s": {"hooks": {"pre": [{"cmd": "echo hi"}]}}}})


def test_validate_config_accepts_cmd_and_timeout():
    validate_config(
        {"steps": {"s": {"hooks": {"pre": [{"cmd": "echo", "timeout": 5}]}}}}
    )


def test_validate_config_no_steps_is_ok():
    validate_config({})
    validate_config({"unrelated": "value"})


def test_validate_config_no_hooks_is_ok():
    validate_config({"steps": {"s": {"max_retries": 3}}})


def test_validate_config_rejects_retry():
    with pytest.raises(ConfigError) as exc:
        validate_config(
            {"steps": {"code-spec": {"hooks": {"pre": [{"cmd": "x", "retry": 2}]}}}}
        )
    msg = str(exc.value)
    assert "'retry'" in msg
    assert "code-spec" in msg
    assert "pre" in msg


def test_validate_config_rejects_unknown_field():
    with pytest.raises(ConfigError) as exc:
        validate_config(
            {"steps": {"s": {"hooks": {"post": [{"cmd": "x", "name": "foo"}]}}}}
        )
    assert "'name'" in str(exc.value)


def test_validate_config_requires_cmd():
    with pytest.raises(ConfigError) as exc:
        validate_config({"steps": {"s": {"hooks": {"pre": [{"timeout": 5}]}}}})
    assert "'cmd'" in str(exc.value)


def test_validate_config_rejects_empty_cmd():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"s": {"hooks": {"pre": [{"cmd": ""}]}}}})


def test_validate_config_rejects_non_dict_entry():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"s": {"hooks": {"pre": ["echo hi"]}}}})


def test_validate_config_rejects_non_list_event():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"s": {"hooks": {"pre": "echo hi"}}}})


# --- resolve_prompt_template ---

_VALID_TEMPLATE = "{{SPEC}}\n{{VERIFY_ERRORS}}\n"


def _make_config(path_value):
    return {"steps": {"implement-spec": {"prompt_template": path_value}}}


def test_resolve_prompt_template_no_key_returns_unchanged():
    config = {"steps": {"implement-spec": {"max_retries": 5}}}
    result = resolve_prompt_template(config, "/some/repo")
    assert result == config


def test_resolve_prompt_template_relative_path_resolved(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tpl = repo / "my_prompt.md"
    tpl.write_text(_VALID_TEMPLATE)

    config = _make_config("my_prompt.md")
    result = resolve_prompt_template(config, str(repo))
    assert result["steps"]["implement-spec"]["prompt_template"] == str(tpl.resolve())


def test_resolve_prompt_template_tilde_path_expanded(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    tpl = home / "prompt.md"
    tpl.write_text(_VALID_TEMPLATE)
    monkeypatch.setenv("HOME", str(home))

    config = _make_config("~/prompt.md")
    result = resolve_prompt_template(config, str(tmp_path / "repo"))
    assert result["steps"]["implement-spec"]["prompt_template"] == str(tpl.resolve())


def test_resolve_prompt_template_path_is_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    config = _make_config(str(repo))
    with pytest.raises(ConfigError, match=str(repo.resolve())):
        resolve_prompt_template(config, str(repo))


def test_resolve_prompt_template_path_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    missing = repo / "nonexistent.md"
    config = _make_config(str(missing))
    with pytest.raises(ConfigError, match=str(missing.resolve())):
        resolve_prompt_template(config, str(repo))


def test_resolve_prompt_template_empty_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tpl = repo / "empty.md"
    tpl.write_text("")
    config = _make_config(str(tpl))
    with pytest.raises(ConfigError):
        resolve_prompt_template(config, str(repo))


def test_resolve_prompt_template_non_utf8(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tpl = repo / "bad.md"
    tpl.write_bytes(b"\xff\xfe invalid utf-8 \x80\x81")
    config = _make_config(str(tpl))
    with pytest.raises(ConfigError, match="UTF-8"):
        resolve_prompt_template(config, str(repo))


def test_resolve_prompt_template_missing_spec_marker(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tpl = repo / "prompt.md"
    tpl.write_text("{{VERIFY_ERRORS}}\nno spec marker here\n")
    config = _make_config(str(tpl))
    with pytest.raises(ConfigError, match="SPEC"):
        resolve_prompt_template(config, str(repo))


def test_resolve_prompt_template_missing_verify_errors_warns(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    tpl = repo / "prompt.md"
    tpl.write_text("{{SPEC}}\nno verify errors marker\n")
    config = _make_config(str(tpl))
    resolve_prompt_template(config, str(repo))
    captured = capsys.readouterr()
    assert "warning" in captured.err
    assert "VERIFY_ERRORS" in captured.err


def test_resolve_prompt_template_non_string_value(tmp_path):
    config = _make_config(42)
    with pytest.raises(ConfigError):
        resolve_prompt_template(config, str(tmp_path))


def test_resolve_prompt_template_empty_string_value(tmp_path):
    config = _make_config("")
    with pytest.raises(ConfigError):
        resolve_prompt_template(config, str(tmp_path))
