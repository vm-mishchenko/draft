import subprocess
import sys
from pathlib import Path

import yaml

from draft.steps import STEPS
from draft.config import ConfigError, _LOOPING_STEPS, load_config, validate_config


def _repo_root() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def register(subparsers):
    p = subparsers.add_parser(
        "init",
        help="Create <repo>/.draft/config.yaml with default timeout and max_retries.",
        description="Create <repo>/.draft/config.yaml with default timeout and max_retries for each step.",
    )
    p.set_defaults(func=run)


def run(args) -> int:
    try:
        repo = _repo_root()
    except subprocess.CalledProcessError:
        print("error: draft init must be run inside a git repository", file=sys.stderr)
        return 1

    draft_dir = Path(repo) / ".draft"
    target = draft_dir / "config.yaml"

    if draft_dir.exists() and not draft_dir.is_dir():
        print(f"error: {draft_dir} exists and is not a directory", file=sys.stderr)
        return 1
    if target.exists():
        print(f"error: {target} already exists; delete it and rerun", file=sys.stderr)
        return 1

    body = {"steps": {}}
    for step in STEPS:
        d = step.defaults()
        cfg = {}
        if "timeout" in d:
            cfg["timeout"] = d["timeout"]
        if step.name in _LOOPING_STEPS and "max_retries" in d:
            cfg["max_retries"] = d["max_retries"]
        body["steps"][step.name] = cfg

    try:
        draft_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(body, sort_keys=False))
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        validate_config(load_config(repo))
    except ConfigError as exc:
        print(f"error: generated config failed validation: {exc}", file=sys.stderr)
        return 1

    print(str(target))
    return 0
