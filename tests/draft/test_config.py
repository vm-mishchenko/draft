import textwrap
from pathlib import Path

import pytest

from draft.config import ConfigError, load_config, step_config, validate_config


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
