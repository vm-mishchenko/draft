import os
import sys
from pathlib import Path

from draft.config import ConfigError, load_config
from draft.hooks import DraftLifecycle, HookRunner
from draft.steps import STEPS
from pipeline import Engine, Pipeline, RunContext, StepError


def register(subparsers):
    p = subparsers.add_parser("continue", help="Resume a stopped run.")
    p.add_argument("run_id", nargs="?", help="Run ID to resume (defaults to most recent).")
    p.set_defaults(func=run)


def _find_latest_run_dir() -> Path | None:
    base = Path("/tmp/draft")
    if not base.exists():
        return None
    dirs = sorted(
        [d for d in base.iterdir() if d.is_dir() and (d / "state.json").exists()],
        key=lambda d: d.name,
        reverse=True,
    )
    return dirs[0] if dirs else None


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


def run(args) -> int:
    if args.run_id:
        run_dir = Path("/tmp/draft") / args.run_id
        if not run_dir.exists():
            print(f"error: run '{args.run_id}' not found", file=sys.stderr)
            return 1
        run_id = args.run_id
    else:
        run_dir = _find_latest_run_dir()
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

    # Recover deleted worktree
    wt_dir = ctx.get("wt_dir", "")
    if ctx.is_completed("worktree-create") and wt_dir and not Path(wt_dir).exists():
        ctx._completed.remove("worktree-create")
        ctx.save()

    # New PID
    pid_file.write_text(str(os.getpid()))

    repo = ctx.get("repo", "")
    try:
        config = load_config(repo)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _print_preamble(ctx, STEPS)

    lifecycle = DraftLifecycle(HookRunner(config, cwd=wt_dir))
    engine = Engine()

    try:
        Pipeline(STEPS).run(ctx, engine, lifecycle)
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
