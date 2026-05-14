import json
from unittest.mock import MagicMock, patch

from draft.steps.implement_spec.original_spec import (
    CASE_COMMITS_ONLY,
    CASE_NONE,
    CASE_OPEN_PR,
    CASE_PRIOR_RUN,
    preamble_label,
    render_original_spec,
    resolve_case,
)


def _make_ctx(
    branch_source=None,
    branch="my-branch",
    project="myproject",
    pr_url=None,
    base_branch=None,
):
    ctx = MagicMock()
    data = {
        "branch_source": branch_source,
        "branch": branch,
        "project": project,
        "pr_url": pr_url,
        "base_branch": base_branch,
    }
    ctx.get.side_effect = lambda key, default=None: data.get(key, default)
    return ctx


def _make_run(runs_dir, project, run_id, state_data, sessions=None):
    run_dir = runs_dir / project / run_id
    run_dir.mkdir(parents=True)
    state = {"data": state_data}
    if sessions:
        state["sessions"] = sessions
    (run_dir / "state.json").write_text(json.dumps(state))
    return run_dir


# --- resolve_case ---


def test_resolve_case_new_branch_returns_none():
    ctx = _make_ctx(branch_source="new")
    case, mapping = resolve_case(ctx)
    assert case == CASE_NONE
    assert mapping == {}


def test_resolve_case_no_branch_source_returns_none():
    ctx = _make_ctx(branch_source=None)
    case, mapping = resolve_case(ctx)
    assert case == CASE_NONE


def test_resolve_case_existing_branch_prior_run_readable_spec(tmp_path):
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("my spec")

    runs_dir = tmp_path / "runs"
    _make_run(
        runs_dir,
        "myproject",
        "250101-120000",
        {"branch": "my-branch", "branch_source": "new", "spec": str(spec_file)},
        sessions=[{"started_at": "2025-01-01T12:00:00Z"}],
    )

    ctx = _make_ctx(branch_source="existing", branch="my-branch", project="myproject")

    with patch("draft.runs.runs_base", return_value=runs_dir):
        case, mapping = resolve_case(ctx)

    assert case == CASE_PRIOR_RUN
    assert mapping["ORIGINAL_SPEC_PATH"] == str(spec_file)
    assert mapping["ORIGINAL_RUN_ID"] == "250101-120000"


def test_resolve_case_existing_branch_prior_run_spec_deleted_has_pr(tmp_path):
    runs_dir = tmp_path / "runs"
    _make_run(
        runs_dir,
        "myproject",
        "250101-120000",
        {
            "branch": "my-branch",
            "branch_source": "new",
            "spec": str(tmp_path / "gone.md"),
        },
    )

    pr_url = "https://github.com/org/repo/pull/42"
    ctx = _make_ctx(
        branch_source="existing", branch="my-branch", project="myproject", pr_url=pr_url
    )

    with patch("draft.runs.runs_base", return_value=runs_dir):
        case, mapping = resolve_case(ctx)

    assert case == CASE_OPEN_PR
    assert mapping["PR_URL"] == pr_url


def test_resolve_case_existing_branch_no_prior_run_no_pr(tmp_path):
    runs_dir = tmp_path / "runs"
    (runs_dir / "myproject").mkdir(parents=True)

    ctx = _make_ctx(
        branch_source="existing",
        branch="my-branch",
        project="myproject",
        base_branch="origin/main",
    )

    with patch("draft.runs.runs_base", return_value=runs_dir):
        case, mapping = resolve_case(ctx)

    assert case == CASE_COMMITS_ONLY
    assert mapping["BRANCH"] == "my-branch"
    assert mapping["BASE_BRANCH"] == "origin/main"


def test_resolve_case_commits_only_default_base_branch(tmp_path):
    runs_dir = tmp_path / "runs"
    (runs_dir / "myproject").mkdir(parents=True)

    ctx = _make_ctx(
        branch_source="existing",
        branch="my-branch",
        project="myproject",
        base_branch=None,
    )

    with patch("draft.runs.runs_base", return_value=runs_dir):
        case, mapping = resolve_case(ctx)

    assert case == CASE_COMMITS_ONLY
    assert mapping["BASE_BRANCH"] == "origin/main"


def test_resolve_case_picks_earliest_of_two_prior_runs(tmp_path):
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("spec")

    runs_dir = tmp_path / "runs"
    _make_run(
        runs_dir,
        "myproject",
        "250101-200000",
        {"branch": "my-branch", "branch_source": "new", "spec": str(spec_file)},
        sessions=[{"started_at": "2025-01-01T20:00:00Z"}],
    )
    _make_run(
        runs_dir,
        "myproject",
        "250101-080000",
        {"branch": "my-branch", "branch_source": "new", "spec": str(spec_file)},
        sessions=[{"started_at": "2025-01-01T08:00:00Z"}],
    )

    ctx = _make_ctx(branch_source="existing", branch="my-branch", project="myproject")

    with patch("draft.runs.runs_base", return_value=runs_dir):
        case, mapping = resolve_case(ctx)

    assert case == CASE_PRIOR_RUN
    assert mapping["ORIGINAL_RUN_ID"] == "250101-080000"


def test_resolve_case_corrupt_json_skipped_falls_through_to_commits_only(tmp_path):
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "myproject" / "250101-120000"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("not json{{{{")

    ctx = _make_ctx(branch_source="existing", branch="my-branch", project="myproject")

    with patch("draft.runs.runs_base", return_value=runs_dir):
        case, _ = resolve_case(ctx)

    assert case == CASE_COMMITS_ONLY


def test_resolve_case_missing_branch_source_treated_as_non_match(tmp_path):
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("spec")

    runs_dir = tmp_path / "runs"
    _make_run(
        runs_dir,
        "myproject",
        "250101-120000",
        {"branch": "my-branch", "spec": str(spec_file)},
    )

    ctx = _make_ctx(branch_source="existing", branch="my-branch", project="myproject")

    with patch("draft.runs.runs_base", return_value=runs_dir):
        case, _ = resolve_case(ctx)

    assert case == CASE_COMMITS_ONLY


# --- render_original_spec ---


def test_render_original_spec_none_returns_empty_string():
    ctx = _make_ctx(branch_source="new")
    result = render_original_spec(ctx)
    assert result == ""


def test_render_original_spec_prior_run_contains_required_parts(tmp_path):
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("spec content")

    runs_dir = tmp_path / "runs"
    _make_run(
        runs_dir,
        "myproject",
        "250101-120000",
        {"branch": "my-branch", "branch_source": "new", "spec": str(spec_file)},
        sessions=[{"started_at": "2025-01-01T12:00:00Z"}],
    )

    ctx = _make_ctx(branch_source="existing", branch="my-branch", project="myproject")

    with patch("draft.runs.runs_base", return_value=runs_dir):
        result = render_original_spec(ctx)

    assert "## Original spec" in result
    assert str(spec_file) in result
    assert "250101-120000" in result


def test_render_original_spec_open_pr_contains_gh_pr_view(tmp_path):
    runs_dir = tmp_path / "runs"
    (runs_dir / "myproject").mkdir(parents=True)

    pr_url = "https://github.com/org/repo/pull/42"
    ctx = _make_ctx(
        branch_source="existing", branch="my-branch", project="myproject", pr_url=pr_url
    )

    with patch("draft.runs.runs_base", return_value=runs_dir):
        result = render_original_spec(ctx)

    assert "## Original spec" in result
    assert f"gh pr view {pr_url}" in result
    assert f"PR: {pr_url}" in result


def test_render_original_spec_commits_only_contains_branch_and_git_log(tmp_path):
    runs_dir = tmp_path / "runs"
    (runs_dir / "myproject").mkdir(parents=True)

    ctx = _make_ctx(
        branch_source="existing",
        branch="my-branch",
        project="myproject",
        base_branch="origin/main",
    )

    with patch("draft.runs.runs_base", return_value=runs_dir):
        result = render_original_spec(ctx)

    assert "## Original spec" in result
    assert "my-branch" in result
    assert "git log origin/main..HEAD" in result


def test_render_original_spec_commits_only_literal_commit_token(tmp_path):
    runs_dir = tmp_path / "runs"
    (runs_dir / "myproject").mkdir(parents=True)

    ctx = _make_ctx(branch_source="existing", branch="my-branch", project="myproject")

    with patch("draft.runs.runs_base", return_value=runs_dir):
        result = render_original_spec(ctx)

    assert "<commit>" in result


# --- preamble_label ---


def test_preamble_label_new_branch_returns_none():
    ctx = _make_ctx(branch_source="new")
    assert preamble_label(ctx) is None


def test_preamble_label_commits_only_returns_none(tmp_path):
    runs_dir = tmp_path / "runs"
    (runs_dir / "myproject").mkdir(parents=True)

    ctx = _make_ctx(branch_source="existing", branch="my-branch", project="myproject")

    with patch("draft.runs.runs_base", return_value=runs_dir):
        assert preamble_label(ctx) is None


def test_preamble_label_prior_run_returns_run_id(tmp_path):
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("spec")

    runs_dir = tmp_path / "runs"
    _make_run(
        runs_dir,
        "myproject",
        "250101-120000",
        {"branch": "my-branch", "branch_source": "new", "spec": str(spec_file)},
        sessions=[{"started_at": "2025-01-01T12:00:00Z"}],
    )

    ctx = _make_ctx(branch_source="existing", branch="my-branch", project="myproject")

    with patch("draft.runs.runs_base", return_value=runs_dir):
        label = preamble_label(ctx)

    assert label == "run 250101-120000"


def test_preamble_label_open_pr_returns_pr_url(tmp_path):
    runs_dir = tmp_path / "runs"
    (runs_dir / "myproject").mkdir(parents=True)

    pr_url = "https://github.com/org/repo/pull/42"
    ctx = _make_ctx(
        branch_source="existing", branch="my-branch", project="myproject", pr_url=pr_url
    )

    with patch("draft.runs.runs_base", return_value=runs_dir):
        label = preamble_label(ctx)

    assert label == f"PR {pr_url}"
