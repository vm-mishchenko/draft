import sys
from pathlib import Path

import yaml


class ConfigError(Exception):
    pass


def _load_yaml(path: Path) -> dict:
    try:
        text = path.read_text()
    except OSError:
        return {}
    try:
        result = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in {path}: {exc}") from exc
    return result or {}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(repo: str) -> dict:
    """Merge: defaults → ~/.draft/config.yaml → <repo>/.draft/config.yaml."""
    global_path = Path.home() / ".draft" / "config.yaml"
    project_path = Path(repo) / ".draft" / "config.yaml"

    global_cfg = _load_yaml(global_path)
    project_cfg = _load_yaml(project_path)

    return _deep_merge(global_cfg, project_cfg)


def step_config(config: dict, step_name: str, step_defaults: dict) -> dict:
    overrides = config.get("steps", {}).get(step_name, {})
    # strip "hooks" sub-key — it's not a step config field
    overrides = {k: v for k, v in overrides.items() if k != "hooks"}
    return {**step_defaults, **overrides}


def load_hook_config(config: dict) -> dict:
    return config.get("steps", {})


def resolve_prompt_template(config: dict, repo: str) -> dict:
    raw = config.get("steps", {}).get("implement-spec", {}).get("prompt_template")
    if raw is None:
        return config
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("steps.implement-spec.prompt_template must be a non-empty string")

    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path(repo) / p
    abs_path = p.resolve()

    if not abs_path.is_file():
        raise ConfigError(f"prompt_template not a regular file: {abs_path}")
    try:
        text = abs_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"prompt_template is not UTF-8: {abs_path}: {exc}")
    except OSError as exc:
        raise ConfigError(f"cannot read prompt_template {abs_path}: {exc}")
    if not text:
        raise ConfigError(f"prompt_template is empty: {abs_path}")
    if "{{SPEC}}" not in text:
        raise ConfigError(f"prompt_template missing required marker {{{{SPEC}}}}: {abs_path}")
    if "{{VERIFY_ERRORS}}" not in text:
        print(
            f"warning: prompt_template lacks {{{{VERIFY_ERRORS}}}}; "
            f"retries will not receive verify feedback: {abs_path}",
            file=sys.stderr,
        )

    config["steps"]["implement-spec"]["prompt_template"] = str(abs_path)
    return config


def resolve_pr_body_template(config: dict, repo: str) -> dict:
    raw = config.get("steps", {}).get("open-pr", {}).get("pr_body_template")
    if raw is None:
        return config
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("steps.open-pr.pr_body_template must be a non-empty string")

    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path(repo) / p
    abs_path = p.resolve()

    if not abs_path.is_file():
        raise ConfigError(f"pr_body_template not a regular file: {abs_path}")
    try:
        text = abs_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"pr_body_template is not UTF-8: {abs_path}: {exc}")
    except OSError as exc:
        raise ConfigError(f"cannot read pr_body_template {abs_path}: {exc}")
    if not text:
        raise ConfigError(f"pr_body_template is empty: {abs_path}")

    config["steps"]["open-pr"]["pr_body_template"] = str(abs_path)
    return config


_HOOK_ALLOWED_KEYS = frozenset({"cmd", "timeout"})
_FORBIDDEN_STEP_KEYS = frozenset({"retry_delay"})
_LOOPING_STEPS = frozenset({"implement-spec", "babysit-pr"})


def _validate_step_keys(step_name: str, step_cfg: dict) -> None:
    for key in step_cfg.keys():
        if key in _FORBIDDEN_STEP_KEYS:
            raise ConfigError(
                f"'{key}' is no longer supported (the pipeline-level retry "
                f"concept was removed). Remove it from steps.{step_name}."
            )
        if key == "max_retries" and step_name not in _LOOPING_STEPS:
            raise ConfigError(
                f"'max_retries' has no effect on steps.{step_name} because "
                f"the step runs once. Remove it."
            )


def validate_config(config: dict) -> None:
    steps = config.get("steps")
    if steps is None:
        return
    if not isinstance(steps, dict):
        raise ConfigError("'steps' must be a mapping")

    for step_name, step_cfg in steps.items():
        if not isinstance(step_cfg, dict):
            continue
        _validate_step_keys(step_name, {k: v for k, v in step_cfg.items() if k != "hooks"})
        hooks = step_cfg.get("hooks")
        if hooks is None:
            continue
        if not isinstance(hooks, dict):
            raise ConfigError(
                f"'hooks' for step '{step_name}' must be a mapping"
            )
        for event, entries in hooks.items():
            if entries is None:
                continue
            if not isinstance(entries, list):
                raise ConfigError(
                    f"hooks for step '{step_name}' event '{event}' "
                    f"must be a list"
                )
            for i, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise ConfigError(
                        f"hook entry {i} for step '{step_name}' "
                        f"event '{event}' must be a mapping"
                    )
                if "cmd" not in entry:
                    raise ConfigError(
                        f"hook entry {i} for step '{step_name}' "
                        f"event '{event}' is missing required key 'cmd'"
                    )
                if not isinstance(entry["cmd"], str) or not entry["cmd"]:
                    raise ConfigError(
                        f"'cmd' for step '{step_name}' event '{event}' "
                        f"entry {i} must be a non-empty string"
                    )
                unknown = set(entry.keys()) - _HOOK_ALLOWED_KEYS
                if unknown:
                    bad = sorted(unknown)[0]
                    raise ConfigError(
                        f"unknown hook option '{bad}' for step "
                        f"'{step_name}' event '{event}' entry {i}"
                    )
