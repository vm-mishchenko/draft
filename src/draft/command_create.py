import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from draft.config import ConfigError, load_config, step_config
from draft.hooks import DraftLifecycle, HookRunner
from draft.steps import STEPS
from pipeline import Engine, RunContext, StepError


def register(subparsers):
    p = subparsers.add_parser("create", help="Start a fresh run from a spec file or prompt.")
    p.add_argument("spec_path", nargs="?", help="Path to spec file.")
    p.add_argument("--prompt", metavar="TEXT", help="Inline prompt text instead of a spec file.")
    p.add_argument(
        "--set",
        metavar="STEP.KEY=VALUE",
        action="append",
        dest="overrides",
        default=[],
        help="Override a step config value (repeatable).",
    )
    p.add_argument(
        "--skip-pr",
        action="store_true",
        default=False,
        help="Stop after code generation; skip push and PR.",
    )
    p.set_defaults(func=run)


# --- pre-flight helpers ---

def _assert_git_repo():
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
    )
    if result.returncode != 0:
        print("error: not inside a git repository", file=sys.stderr)
        sys.exit(3)


def _assert_main_clone():
    result = subprocess.run(
        ["git", "rev-parse", "--show-superproject-working-tree"],
        capture_output=True, text=True,
    )
    if result.stdout.strip():
        print("error: cannot run draft from inside a git worktree", file=sys.stderr)
        sys.exit(3)
    result2 = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True, text=True,
    )
    lines = result2.stdout.splitlines()
    # first worktree entry is the main clone; second would be a linked worktree
    # detect by checking if current dir equals main worktree path
    cwd = os.getcwd()
    main_wt = ""
    for line in lines:
        if line.startswith("worktree "):
            main_wt = line[len("worktree "):]
            break
    if cwd != main_wt and main_wt:
        print("error: draft must be run from the main worktree, not a linked worktree", file=sys.stderr)
        sys.exit(3)


def _assert_on_path(tool: str):
    if not shutil.which(tool):
        print(f"error: '{tool}' not found on PATH", file=sys.stderr)
        sys.exit(3)


def _repo_root() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _project_name(repo: str) -> str:
    return Path(repo).name


def _sanitize_branch(branch: str) -> str:
    return branch.replace("/", "-")


def _branch_slug_from_claude(prompt_text: str, run_id: str) -> str:
    from importlib.resources import files
    try:
        template = files("draft.steps.code_spec").joinpath("branch_slug.md").read_text()
        full_prompt = template.replace("{{PROMPT}}", prompt_text)
        result = subprocess.run(
            ["claude", "-p", full_prompt],
            capture_output=True, text=True, timeout=60,
        )
        slug = result.stdout.strip().lower()
        slug = "".join(c if c.isalnum() or c == "-" else "-" for c in slug)
        slug = slug.strip("-")[:50]
        if slug:
            return slug
    except Exception:
        pass
    return f"draft-{run_id}"


def _apply_overrides(config: dict, overrides: list[str]) -> dict:
    import copy
    cfg = copy.deepcopy(config)
    for override in overrides:
        if "=" not in override or "." not in override.split("=")[0]:
            print(f"warning: ignoring malformed --set value: {override}", file=sys.stderr)
            continue
        key_path, value = override.split("=", 1)
        step_name, key = key_path.split(".", 1)
        cfg.setdefault("steps", {}).setdefault(step_name, {})[key] = value
    return cfg


def _print_preamble(run_id, branch, wt_dir, run_dir, started_at, all_steps, skipped):
    print(f"run-id:   {run_id}")
    print(f"branch:   {branch}")
    print(f"worktree: {wt_dir}")
    print(f"logs:     {run_dir}")
    print(f"started:  {started_at}")
    print("stages:")
    for step in all_steps:
        suffix = " [skipped]" if step.name in skipped else ""
        print(f"  - {step.name}{suffix}")
    print()


def run(args) -> int:
    if not args.spec_path and not args.prompt:
        print("error: provide a spec file or --prompt TEXT", file=sys.stderr)
        return 1

    # 1. Pre-flight
    _assert_git_repo()
    _assert_main_clone()
    _assert_on_path("claude")
    if not args.skip_pr:
        _assert_on_path("gh")

    # 2. Run ID (dir created after project_name is known)
    run_id = time.strftime("%y%m%d-%H%M%S")

    repo = _repo_root()
    project_name = _project_name(repo)

    run_dir = Path.home() / ".draft" / "runs" / project_name / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 3. PID
    (run_dir / "draft.pid").write_text(str(os.getpid()))

    # 4. Branch + spec
    if args.prompt:
        prompt_file = run_dir / "prompt.md"
        prompt_file.write_text(args.prompt)
        spec = str(prompt_file)
        branch = _branch_slug_from_claude(args.prompt, run_id)
    else:
        spec = str(Path(args.spec_path).resolve())
        stem = Path(spec).stem
        branch = stem.lower().replace("_", "-").replace(" ", "-")[:50]

    wt_dir = str(Path.home() / ".draft" / "worktrees" / project_name / _sanitize_branch(branch))

    # 5. Config
    try:
        config = load_config(repo)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    config = _apply_overrides(config, args.overrides)

    # 6. Step configs
    step_configs = {
        step.name: step_config(config, step.name, step.defaults())
        for step in STEPS
    }

    # 7. Active steps
    PR_STEPS = {"push", "pr-open", "pr-view", "pr-babysit"}
    skip_pr = args.skip_pr
    if skip_pr:
        active_steps = [s for s in STEPS if s.name not in PR_STEPS]
        skipped_names = PR_STEPS
    else:
        active_steps = STEPS
        skipped_names = set()

    # 8. Context
    ctx = RunContext(run_id, run_dir, step_configs)
    ctx.set("branch", branch)
    ctx.set("wt_dir", wt_dir)
    ctx.set("repo", repo)
    ctx.set("spec", spec)
    ctx.set("project", project_name)
    ctx.set("started_at", ctx.started_at)
    ctx.set("skip_pr", skip_pr)

    # 9. Save initial state
    ctx.save()

    # 10. Worktree parent dir
    Path(wt_dir).parent.mkdir(parents=True, exist_ok=True)

    # 11. Preamble
    _print_preamble(run_id, branch, wt_dir, run_dir, ctx.started_at, STEPS, skipped_names)

    # 12. Lifecycle + engine
    lifecycle = DraftLifecycle(HookRunner(config, cwd=wt_dir))
    engine = Engine()

    # 13. Run pipeline
    try:
        from pipeline import Pipeline
        Pipeline(active_steps).run(ctx, engine, lifecycle)
    except StepError as exc:
        print(f"\nerror: step '{exc.step_name}' failed (exit {exc.exit_code})", file=sys.stderr)
        _exit_code = {
            "code-spec": 4,
            "push": 5,
            "pr-open": 6,
            "pr-view": 6,
        }.get(exc.step_name, 1)
        (run_dir / "draft.pid").unlink(missing_ok=True)
        return _exit_code

    # 14. Success
    if skip_pr:
        print(f"done. (push and PR skipped; worktree left at {wt_dir})")
    else:
        print("done.")
    (run_dir / "draft.pid").unlink(missing_ok=True)
    return 0
