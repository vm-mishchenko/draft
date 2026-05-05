from pathlib import Path


def runs_base() -> Path:
    return Path.home() / ".draft" / "runs"


def find_run_dir(run_id: str) -> Path | None:
    base = runs_base()
    if not base.exists():
        return None
    for project_dir in base.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / run_id
        if candidate.is_dir():
            return candidate
    return None


def find_latest_run_dir() -> Path | None:
    base = runs_base()
    if not base.exists():
        return None
    all_runs = []
    for project_dir in base.iterdir():
        if not project_dir.is_dir():
            continue
        for run_dir in project_dir.iterdir():
            if run_dir.is_dir() and (run_dir / "state.json").exists():
                all_runs.append(run_dir)
    if not all_runs:
        return None
    return sorted(all_runs, key=lambda d: d.name, reverse=True)[0]
