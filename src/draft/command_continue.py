import os
import subprocess
import sys
from pathlib import Path

from draft import runs
from draft.config import ConfigError, load_config, validate_config
from draft.hooks import DraftLifecycle, HookRunner
from draft.steps import STEPS
from pipeline import Runner, Pipeline, RunContext, StepError


def register(subparsers):
    p = subparsers.add_parser("continue", help="Resume a stopped run.")
    p.add_argument("run_id", nargs="?", help="Run ID to resume (defaults to most recent).")
    p.set_defaults(func=run)


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _print_preamble(ctx, steps):
    completed = set(ctx._completed)
    print(f"run-id:   {ctx.run_id}")
    print(f"branch:   {ctx.get('branch', '-')}")
    print(f"worktree: {ctx.get('wt_dir', '-')}")
    print(f"logs:     {ctx.run_dir}")
    print(f"started:  {ctx.started_at}")
    print("stages:")
    for step in steps:
        marker = "x" if step.name in completed else "*" if _next_step(ctx, steps) == step.name else " "
        print(f"  [{marker}] {step.name}")
    print()


def _next_step(ctx, steps) -> str | None:
    for step in steps:
        if not ctx.is_completed(step.name):
            return step.name
    return None


def _branch_at(path: str) -> str | None:
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"],
        capture_output=True, text=True, cwd=path,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def run(args) -> int:
    if args.run_id:
        run_dir = runs.find_run_dir(args.run_id)
        if run_dir is None:
            print(f"error: run '{args.run_id}' not found", file=sys.stderr)
            return 1
        run_id = args.run_id
    else:
        run_dir = runs.find_latest_run_dir()
        if run_dir is None:
            print("error: no runs found", file=sys.stderr)
            return 1
        run_id = run_dir.name

    # Load context
    try:
        ctx = RunContext.load(run_id, run_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Check PID
    pid_file = run_dir / "draft.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _is_pid_alive(pid):
                print(f"error: run '{run_id}' is currently active (pid {pid})", file=sys.stderr)
                return 3
        except ValueError:
            pass

    # State for finished-run / drift checks
    state_for_check = {
        "completed": ctx._completed,
        "data": dict(ctx._data),
    }
    finished = runs.is_run_finished(state_for_check)
    worktree_mode = ctx.get("worktree_mode", "worktree")
    delete_worktree = bool(ctx.get("delete_worktree", False))
    saved_branch = ctx.get("branch", "")
    repo = ctx.get("repo", "")
    wt_dir = ctx.get("wt_dir", "")

    # Finished + worktree gone (--delete-worktree happy path): nothing to do
    if (
        finished
        and worktree_mode == "worktree"
        and delete_worktree
        and wt_dir
        and not Path(wt_dir).exists()
    ):
        print(f"run '{run_id}' is already complete; worktree was deleted.")
        return 0

    # Drift check: current branch context vs saved branch
    if saved_branch:
        if worktree_mode == "no-worktree":
            current = _branch_at(repo) if repo else None
            if current is not None and current != saved_branch:
                print(
                    f"error: branch drift; run '{run_id}' targets '{saved_branch}' but {repo} is on '{current}'",
                    file=sys.stderr,
                )
                return 2
        elif wt_dir and Path(wt_dir).exists():
            current = _branch_at(wt_dir)
            if current is not None and current != saved_branch:
                print(
                    f"error: branch drift; run '{run_id}' targets '{saved_branch}' but worktree is on '{current}'",
                    file=sys.stderr,
                )
                return 2

    # Recover deleted worktree (only for unfinished worktree-mode runs)
    if (
        worktree_mode == "worktree"
        and ctx.is_completed("worktree-create")
        and wt_dir
        and not Path(wt_dir).exists()
    ):
        ctx._completed.remove("worktree-create")
        ctx.save()

    # New PID
    pid_file.write_text(str(os.getpid()))

    try:
        config = load_config(repo)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        validate_config(config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    expected = set(runs.expected_steps(state_for_check))
    active_steps = [s for s in STEPS if s.name in expected]

    _print_preamble(ctx, active_steps)

    engine = Runner()
    lifecycle = DraftLifecycle(HookRunner(config, cwd=wt_dir, run_dir=run_dir, engine=engine))

    try:
        Pipeline(active_steps).run(ctx, engine, lifecycle)
    except StepError as exc:
        print(f"\nerror: step '{exc.step_name}' failed (exit {exc.exit_code})", file=sys.stderr)
        _exit_code = {
            "code-spec": 4,
            "push": 5,
            "pr-open": 6,
            "pr-view": 6,
        }.get(exc.step_name, 1)
        pid_file.unlink(missing_ok=True)
        return _exit_code

    print("done.")
    pid_file.unlink(missing_ok=True)
    return 0
