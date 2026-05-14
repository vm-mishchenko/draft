import os
from importlib.resources import files
from pathlib import Path

from draft.runs import find_original_run_on_branch, load_state

CASE_NONE = "none"
CASE_PRIOR_RUN = "prior_run"
CASE_OPEN_PR = "open_pr"
CASE_COMMITS_ONLY = "commits_only"

_CASE_FILES = {
    CASE_PRIOR_RUN: "original_spec_prior_run.md",
    CASE_OPEN_PR: "original_spec_open_pr.md",
    CASE_COMMITS_ONLY: "original_spec_commits_only.md",
}


def _load_case_template(case: str) -> str:
    return files("draft.steps.implement_spec").joinpath(_CASE_FILES[case]).read_text()


def _render_case(case: str, mapping: dict) -> str:
    template = _load_case_template(case)
    for key, value in mapping.items():
        template = template.replace(f"{{{{{key}}}}}", value)
    return template


def resolve_case(ctx) -> tuple[str, dict]:
    if ctx.get("branch_source") != "existing":
        return (CASE_NONE, {})
    original_run = find_original_run_on_branch(ctx.get("project"), ctx.get("branch"))
    if original_run is not None:
        state = load_state(original_run)
        spec_path = state.get("data", {}).get("spec") if state else None
        if spec_path and Path(spec_path).is_file() and os.access(spec_path, os.R_OK):
            return (
                CASE_PRIOR_RUN,
                {
                    "ORIGINAL_SPEC_PATH": spec_path,
                    "ORIGINAL_RUN_ID": original_run.name,
                },
            )
    pr_url = ctx.get("pr_url")
    if pr_url:
        return (CASE_OPEN_PR, {"PR_URL": pr_url})
    return (
        CASE_COMMITS_ONLY,
        {
            "BRANCH": ctx.get("branch") or "",
            "BASE_BRANCH": ctx.get("base_branch") or "origin/main",
        },
    )


def render_original_spec(ctx) -> str:
    case, mapping = resolve_case(ctx)
    if case == CASE_NONE:
        return ""
    return _render_case(case, mapping)


def preamble_label(ctx) -> str | None:
    case, mapping = resolve_case(ctx)
    if case == CASE_PRIOR_RUN:
        return f"run {mapping['ORIGINAL_RUN_ID']}"
    if case == CASE_OPEN_PR:
        return f"PR {mapping['PR_URL']}"
    return None
