#!/usr/bin/env python3
"""Runs a prompt template against auggie.

Usage: run_agent.py <template> <model>

<template> is a path to a prompt template file — absolute or relative to
DRAFT_REPO_DIR. The template may contain these placeholders which are
substituted from the corresponding environment variables:
  {DRAFT_REPO_DIR}  {DRAFT_BRANCH}  {DRAFT_BASE_BRANCH}  {DRAFT_SPEC_FILE}

<model> is forwarded to auggie (e.g. gpt-4.1).

Verdict contract: stdout empty → approval; stdout non-empty → rejection;
rc != 0 → infra failure.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

TEMPLATE_VARS = ("DRAFT_REPO_DIR", "DRAFT_BRANCH", "DRAFT_BASE_BRANCH", "DRAFT_SPEC_FILE")


def die(msg: str) -> None:
    print(f"run_agent.py: {msg}", file=sys.stderr)
    sys.exit(1)


def resolve_template_path(template: str, repo_dir: str) -> Path:
    p = Path(template)
    return p if p.is_absolute() else Path(repo_dir) / p


def fill_template(text: str, env: dict[str, str]) -> str:
    for var in TEMPLATE_VARS:
        text = text.replace(f"{{{var}}}", env.get(var, ""))
    return text


def check_auggie_auth() -> None:
    result = subprocess.run(
        ["auggie", "token", "print"], capture_output=True, text=True
    )
    out = result.stdout + result.stderr
    if result.returncode != 0 or "SESSION=" not in out:
        print(
            "run_agent.py: auggie pre-flight auth check failed;"
            " run 'auggie login' or export AUGMENT_SESSION_AUTH",
            file=sys.stderr,
        )
        if out.strip():
            print("--- auggie token print output ---", file=sys.stderr)
            print(out, file=sys.stderr)
        sys.exit(1)


def run_auggie(prompt: str, model: str, workspace_root: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(prompt)
        prompt_file = f.name
    try:
        result = subprocess.run(
            [
                "auggie",
                "--print",
                "--quiet",
                "--ask",
                "--workspace-root",
                workspace_root,
                "--allow-indexing",
                "--max-turns",
                "10",
                "--model",
                model,
                "--instruction-file",
                prompt_file,
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
    finally:
        os.unlink(prompt_file)

    if result.returncode != 0:
        print(
            f"run_agent.py: auggie call failed with rc={result.returncode}",
            file=sys.stderr,
        )
        sys.exit(result.returncode)

    return result.stdout


def main() -> None:
    if len(sys.argv) != 3:
        die("usage: run_agent.py <template> <model>")

    template_arg, model = sys.argv[1], sys.argv[2]

    repo_dir = os.environ.get("DRAFT_REPO_DIR", "")
    if not repo_dir:
        die("DRAFT_REPO_DIR is unset or empty")

    env: dict[str, str] = {var: os.environ.get(var, "") for var in TEMPLATE_VARS}

    template_path = resolve_template_path(template_arg, env["DRAFT_REPO_DIR"])
    if not template_path.is_file():
        die(f"template not found: {template_path}")

    template_text = template_path.read_text()
    if not template_text.strip():
        die(f"template is empty: {template_path}")

    prompt = fill_template(template_text, env)

    check_auggie_auth()

    stdout = run_auggie(prompt, model, env["DRAFT_REPO_DIR"])

    if stdout.strip():
        print(stdout, end="")


if __name__ == "__main__":
    main()
