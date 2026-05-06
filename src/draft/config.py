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


_HOOK_ALLOWED_KEYS = frozenset({"cmd", "timeout"})


def validate_config(config: dict) -> None:
    """Validate hook entries.

    Recognised hook fields are exactly `cmd` and `timeout`. Any other key
    (e.g. `retry`, `name`) is rejected. `cmd` is required and must be a
    non-empty string. Raises ConfigError on first violation.
    """
    steps = config.get("steps")
    if steps is None:
        return
    if not isinstance(steps, dict):
        raise ConfigError("'steps' must be a mapping")

    for step_name, step_cfg in steps.items():
        if not isinstance(step_cfg, dict):
            continue
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
