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
