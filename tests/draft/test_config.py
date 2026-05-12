import textwrap
from pathlib import Path

import pytest

from draft.config import (
    ConfigError,
    load_config,
    resolve_pr_body_template,
    resolve_prompt_template,
    step_config,
    validate_config,
)


def test_load_config_merges_global_and_project(tmp_path):
    global_dir = tmp_path / "home" / ".draft"
    global_dir.mkdir(parents=True)
    (global_dir / "config.yaml").write_text(
        textwrap.dedent("""\
        steps:
          implement-spec:
            max_retries: 3
          push-commits:
            timeout: 60
    """)
    )

    repo_dir = tmp_path / "repo"
    project_dir = repo_dir / ".draft"
    project_dir.mkdir(parents=True)
    (project_dir / "config.yaml").write_text(
        textwrap.dedent("""\
        steps:
          implement-spec:
            max_retries: 7
    """)
    )

    import unittest.mock as mock

    with mock.patch.object(Path, "home", return_value=tmp_path / "home"):
        result = load_config(str(repo_dir))

    # project wins
    assert result["steps"]["implement-spec"]["max_retries"] == 7
    # global-only key preserved
    assert result["steps"]["push-commits"]["timeout"] == 60


def test_load_config_malformed_yaml_raises(tmp_path):
    repo_dir = tmp_path / "repo"
    project_dir = repo_dir / ".draft"
    project_dir.mkdir(parents=True)
    (project_dir / "config.yaml").write_text("steps: [invalid: yaml: here")

    import unittest.mock as mock

    with (
        mock.patch.object(Path, "home", return_value=tmp_path / "nonexistent"),
        pytest.raises(ConfigError),
    ):
        load_config(str(repo_dir))


def test_step_config_merges_defaults_and_overrides():
    config = {"steps": {"implement-spec": {"max_retries": 5, "timeout": 600}}}
    defaults = {"max_retries": 10, "timeout": 1200}
    result = step_config(config, "implement-spec", defaults)
    assert result == {"max_retries": 5, "timeout": 600}


def test_step_config_strips_hooks():
    config = {
        "steps": {
            "implement-spec": {
                "max_retries": 5,
                "hooks": {"pre": [{"cmd": "echo hi"}]},
            }
        }
    }
    defaults = {"max_retries": 10, "timeout": None}
    result = step_config(config, "implement-spec", defaults)
    assert "hooks" not in result
    assert result["max_retries"] == 5


def test_step_config_no_overrides_uses_defaults():
    config = {}
    defaults = {"timeout": None}
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
    validate_config({"steps": {"implement-spec": {"max_retries": 3}}})


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


def test_validate_config_rejects_retry_delay_on_any_step():
    for step in ("implement-spec", "babysit-pr", "create-worktree"):
        with pytest.raises(ConfigError) as exc:
            validate_config({"steps": {step: {"retry_delay": 0}}})
        assert step in str(exc.value)
        assert "retry_delay" in str(exc.value)


def test_validate_config_rejects_max_retries_on_single_shot_steps():
    for step in ("create-worktree", "push-commits", "open-pr", "delete-worktree"):
        with pytest.raises(ConfigError) as exc:
            validate_config({"steps": {step: {"max_retries": 2}}})
        assert step in str(exc.value)
        assert "runs once" in str(exc.value)


def test_validate_config_accepts_max_retries_on_looping_steps():
    validate_config({"steps": {"implement-spec": {"max_retries": 5}}})
    validate_config({"steps": {"babysit-pr": {"max_retries": 50}}})


def test_validate_config_non_mapping_step_value_is_skipped():
    validate_config({"steps": {"create-worktree": None}})
    validate_config({"steps": {"create-worktree": ["list"]}})


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


# --- resolve_pr_body_template ---


def _make_pr_config(path_value):
    return {"steps": {"open-pr": {"pr_body_template": path_value}}}


def test_resolve_pr_body_template_no_key_returns_unchanged():
    config = {"steps": {"open-pr": {"title_prefix": "foo"}}}
    result = resolve_pr_body_template(config, "/some/repo")
    assert result == config


def test_resolve_pr_body_template_no_steps_returns_unchanged():
    config = {}
    result = resolve_pr_body_template(config, "/some/repo")
    assert result == config


def test_resolve_pr_body_template_relative_path_resolved(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tpl = repo / "pr_template.md"
    tpl.write_text("## Summary\n")

    config = _make_pr_config("pr_template.md")
    result = resolve_pr_body_template(config, str(repo))
    assert result["steps"]["open-pr"]["pr_body_template"] == str(tpl.resolve())


def test_resolve_pr_body_template_tilde_path_expanded(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    tpl = home / "pr_template.md"
    tpl.write_text("## Summary\n")
    monkeypatch.setenv("HOME", str(home))

    config = _make_pr_config("~/pr_template.md")
    result = resolve_pr_body_template(config, str(tmp_path / "repo"))
    assert result["steps"]["open-pr"]["pr_body_template"] == str(tpl.resolve())


def test_resolve_pr_body_template_absolute_path(tmp_path):
    tpl = tmp_path / "pr_template.md"
    tpl.write_text("## Summary\n")

    config = _make_pr_config(str(tpl))
    result = resolve_pr_body_template(config, "/unrelated/repo")
    assert result["steps"]["open-pr"]["pr_body_template"] == str(tpl.resolve())


def test_resolve_pr_body_template_path_is_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    config = _make_pr_config(str(repo))
    with pytest.raises(ConfigError, match=str(repo.resolve())):
        resolve_pr_body_template(config, str(repo))


def test_resolve_pr_body_template_path_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    missing = repo / "nonexistent.md"
    config = _make_pr_config(str(missing))
    with pytest.raises(ConfigError, match=str(missing.resolve())):
        resolve_pr_body_template(config, str(repo))


def test_resolve_pr_body_template_empty_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tpl = repo / "empty.md"
    tpl.write_text("")
    config = _make_pr_config(str(tpl))
    with pytest.raises(ConfigError):
        resolve_pr_body_template(config, str(repo))


def test_resolve_pr_body_template_non_utf8(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tpl = repo / "bad.md"
    tpl.write_bytes(b"\xff\xfe invalid utf-8 \x80\x81")
    config = _make_pr_config(str(tpl))
    with pytest.raises(ConfigError, match="UTF-8"):
        resolve_pr_body_template(config, str(repo))


def test_resolve_pr_body_template_non_string_value(tmp_path):
    config = _make_pr_config(42)
    with pytest.raises(ConfigError):
        resolve_pr_body_template(config, str(tmp_path))


def test_resolve_pr_body_template_empty_string_value(tmp_path):
    config = _make_pr_config("")
    with pytest.raises(ConfigError):
        resolve_pr_body_template(config, str(tmp_path))


def test_resolve_pr_body_template_valid_rewrites_to_absolute(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tpl = repo / "template.md"
    tpl.write_text("## Summary\n")

    config = _make_pr_config("template.md")
    result = resolve_pr_body_template(config, str(repo))
    assert result["steps"]["open-pr"]["pr_body_template"] == str(tpl.resolve())


# --- validate_config: implement-spec suggester keys ---


def test_validate_suggest_extra_checks_string_rejected():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"implement-spec": {"suggest_extra_checks": "yes"}}})


def test_validate_suggest_extra_checks_int_rejected():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"implement-spec": {"suggest_extra_checks": 1}}})


def test_validate_suggest_extra_checks_none_rejected():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"implement-spec": {"suggest_extra_checks": None}}})


def test_validate_suggest_extra_checks_false_accepted():
    validate_config({"steps": {"implement-spec": {"suggest_extra_checks": False}}})


def test_validate_suggest_extra_checks_true_accepted():
    validate_config({"steps": {"implement-spec": {"suggest_extra_checks": True}}})


def test_validate_max_checks_negative_rejected():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"implement-spec": {"max_checks": -1}}})


def test_validate_max_checks_over_limit_rejected():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"implement-spec": {"max_checks": 21}}})


def test_validate_max_checks_zero_accepted():
    validate_config({"steps": {"implement-spec": {"max_checks": 0}}})


def test_validate_max_checks_twenty_accepted():
    validate_config({"steps": {"implement-spec": {"max_checks": 20}}})


def test_validate_per_check_timeout_over_limit_rejected():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"implement-spec": {"per_check_timeout": 181}}})


def test_validate_per_check_timeout_zero_rejected():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"implement-spec": {"per_check_timeout": 0}}})


def test_validate_per_check_timeout_valid_accepted():
    validate_config({"steps": {"implement-spec": {"per_check_timeout": 120}}})


def test_validate_suggester_timeout_string_rejected():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"implement-spec": {"suggester_timeout": "120"}}})


def test_validate_suggester_timeout_over_limit_rejected():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"implement-spec": {"suggester_timeout": 601}}})


def test_validate_suggester_timeout_valid_accepted():
    validate_config({"steps": {"implement-spec": {"suggester_timeout": 120}}})


def test_validate_suggester_total_budget_valid_accepted():
    validate_config({"steps": {"implement-spec": {"suggester_total_budget": 600}}})


def test_validate_suggester_total_budget_zero_rejected():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"implement-spec": {"suggester_total_budget": 0}}})


def test_validate_suggester_total_budget_over_limit_rejected():
    with pytest.raises(ConfigError):
        validate_config({"steps": {"implement-spec": {"suggester_total_budget": 3601}}})


def test_validate_suggester_keys_on_other_step_silently_allowed():
    validate_config(
        {"steps": {"babysit-pr": {"max_checks": 99, "suggest_extra_checks": "yes"}}}
    )
