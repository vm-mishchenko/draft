import json
import os
from pathlib import Path

from pipeline.heartbeat import Heartbeat
from pipeline.metrics import RunMetrics


class RunContext:
    def __init__(
        self, run_id: str, run_dir: str | Path, step_configs: dict | None = None
    ):
        self.run_id = run_id
        self.run_dir = Path(run_dir)
        self._data: dict = {}  # run-level KV store; steps write outputs here via set()/get() and the values persist across retries in state.json
        self._step_data: dict = {}  # per-step KV store; keyed by step name, used by steps to stash intermediate state via step_set()/step_get()
        self._completed: list[str] = []
        self._step_configs: dict = step_configs or {}
        self._sessions: list[dict] = []
        self.heartbeat: Heartbeat = Heartbeat(self.run_dir)
        self.metrics: RunMetrics = RunMetrics(self._sessions, self.heartbeat)

    # --- run-level KV ---

    def set(self, key: str, value):
        self._data[key] = value

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    # --- per-step KV ---

    def step_set(self, step_name: str, key: str, value):
        self._step_data.setdefault(step_name, {})[key] = value

    def step_get(self, step_name: str, key: str, default=None):
        return self._step_data.get(step_name, {}).get(key, default)

    # --- completion tracking ---

    def mark_done(self, step_name: str):
        if step_name not in self._completed:
            self._completed.append(step_name)

    def is_completed(self, step_name: str) -> bool:
        return step_name in self._completed

    # --- paths ---

    def log_path(self, step_name: str) -> Path:
        return self.run_dir / f"{step_name}.log"

    def hook_log_path(self, step_name: str, event: str) -> Path:
        return self.run_dir / f"{step_name}.{event}.log"

    # --- step config ---

    def config(self, step_name: str) -> dict:
        return self._step_configs.get(step_name, {})

    # --- persistence ---

    def save(self):
        payload = {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "completed": self._completed,
            "data": self._data,
            "step_data": self._step_data,
            "step_configs": self._step_configs,
            "sessions": self._sessions,
        }
        state_path = self.run_dir / "state.json"
        tmp_path = self.run_dir / "state.json.tmp"
        tmp_path.write_text(json.dumps(payload, indent=2))
        os.replace(tmp_path, state_path)

    @classmethod
    def load(cls, run_id: str, run_dir: str | Path) -> "RunContext":
        run_dir = Path(run_dir)
        state_path = run_dir / "state.json"
        if not state_path.exists():
            raise FileNotFoundError(f"state.json not found in {run_dir}")
        try:
            payload = json.loads(state_path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"state.json in {run_dir} is corrupt: {exc}") from exc
        ctx = cls(
            run_id=payload["run_id"],
            run_dir=payload["run_dir"],
            step_configs=payload.get("step_configs", {}),
        )
        ctx._data = payload.get("data", {})
        ctx._step_data = payload.get("step_data", {})
        ctx._completed = payload.get("completed", [])
        ctx._sessions = payload.get("sessions", [])
        ctx.metrics = RunMetrics(ctx._sessions, ctx.heartbeat)
        return ctx
