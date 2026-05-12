import contextlib
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.heartbeat import Heartbeat

_HUMAN_FMT = "%Y-%m-%d %H:%M:%S UTC"
_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s"


def now_human() -> str:
    return datetime.now(UTC).strftime(_HUMAN_FMT)


def parse_human(s: str) -> datetime:
    dt = datetime.strptime(s, _HUMAN_FMT)
    return dt.replace(tzinfo=UTC)


class KnownMetric(StrEnum):
    """Enumeration of well-known metric keys; extend to add reserved names."""

    LLM_COST_USD = "llm_cost_usd"
    LLM_INPUT_TOKENS = "llm_input_tokens"
    LLM_OUTPUT_TOKENS = "llm_output_tokens"
    LLM_DURATION_MS = "llm_duration_ms"


def _resolve_name(name) -> str:
    if isinstance(name, KnownMetric):
        return name.value
    if isinstance(name, str):
        for member in KnownMetric:
            if member.value == name:
                raise ValueError(
                    f"'{name}' shadows a KnownMetric member; use KnownMetric.{member.name} instead"
                )
        if not _NAME_RE.match(name):
            raise ValueError(f"invalid metric name '{name}'; must match [a-z0-9_]+")
        return name
    raise TypeError(f"name must be str or KnownMetric, not {type(name).__name__}")


class StepMetrics:
    """Mutable view into a single step entry; closed after `end()` is called."""

    def __init__(self, step_dict: dict):
        self._dict = step_dict
        self._closed = False

    def _check_open(self):
        if self._closed:
            raise RuntimeError("handle closed")

    def set(self, name, value):
        self._check_open()
        key = _resolve_name(name)
        self._dict["data"][key] = value

    def add(self, name, value):
        self._check_open()
        if not isinstance(value, (int, float)):
            raise ValueError(f"add() requires int or float, got {type(value).__name__}")
        key = _resolve_name(name)
        existing = self._dict["data"].get(key, 0)
        self._dict["data"][key] = existing + value

    def end(self, exit_code: int):
        self._check_open()
        self._dict["finished_at"] = now_human()
        self._dict["exit_code"] = exit_code
        self._closed = True


class SessionMetrics:
    """Mutable view into a single session entry; produces StepMetrics for each step."""

    def __init__(self, session_dict: dict):
        self._dict = session_dict
        self._closed = False

    def step_begin(self, step_name: str) -> "StepMetrics":
        if self._closed:
            raise RuntimeError("handle closed")
        entry = {
            "name": step_name,
            "started_at": now_human(),
            "finished_at": None,
            "exit_code": None,
            "data": {},
        }
        self._dict["steps"].append(entry)
        return StepMetrics(entry)

    def end(self, exit_code: int):
        if self._closed:
            raise RuntimeError("handle closed")
        self._dict["finished_at"] = now_human()
        self._dict["exit_code"] = exit_code
        self._closed = True


class RunMetrics:
    """Top-level metrics object for a run; owns the sessions list and reconciles crash state."""

    def __init__(self, sessions: list, heartbeat: "Heartbeat"):
        self._sessions = sessions
        self._heartbeat = heartbeat

    def session_begin(self, command: str) -> SessionMetrics:
        self._reconcile_unclosed()
        entry = {
            "command": command,
            "started_at": now_human(),
            "finished_at": None,
            "exit_code": None,
            "steps": [],
        }
        self._sessions.append(entry)
        return SessionMetrics(entry)

    def _infer_finish_for(self, session: dict) -> "datetime | None":
        ts = self._heartbeat.read()
        if ts is not None:
            return ts

        step_times = []
        for step in session.get("steps", []):
            fat = step.get("finished_at")
            if fat is not None:
                with contextlib.suppress(ValueError):
                    step_times.append(parse_human(fat))
        if step_times:
            return max(step_times)

        with contextlib.suppress(ValueError, KeyError):
            return parse_human(session["started_at"])

        return None

    def _reconcile_unclosed(self):
        if not self._sessions or self._sessions[-1]["finished_at"] is not None:
            self._heartbeat.delete()
            return

        last = self._sessions[-1]
        ts = self._infer_finish_for(last)
        ts_str = ts.strftime(_HUMAN_FMT) if ts is not None else now_human()

        last["finished_at"] = ts_str
        last["exit_code"] = -1
        for step in last.get("steps", []):
            if step.get("finished_at") is None:
                step["finished_at"] = ts_str
                step["exit_code"] = -1

        self._heartbeat.delete()

    def aggregates(self) -> dict:
        total = 0.0
        total_cost: float | None = None
        for s in self._sessions:
            try:
                started = parse_human(s["started_at"])
            except (KeyError, ValueError):
                continue
            fa = s.get("finished_at")
            if fa is None:
                finished = self._infer_finish_for(s)
            else:
                try:
                    finished = parse_human(fa)
                except ValueError:
                    continue
            if finished is None:
                continue
            delta = (finished - started).total_seconds()
            if delta >= 0:
                total += delta
            for step in s.get("steps", []) or []:
                data = step.get("data")
                if not isinstance(data, dict):
                    continue
                if "llm_cost_usd" not in data:
                    continue
                val = data["llm_cost_usd"]
                if not isinstance(val, (int, float)):
                    continue
                if total_cost is None:
                    total_cost = 0.0
                total_cost += val
        return {"total_runtime_seconds": total, "total_llm_cost_usd": total_cost}

    def per_step_times(self) -> dict[str, float | None]:
        times: dict[str, float | None] = {}
        for s in self._sessions:
            for step in s.get("steps", []) or []:
                name = step.get("name")
                if name is None:
                    continue
                sat = step.get("started_at")
                fat = step.get("finished_at")
                if sat is None or fat is None:
                    if name not in times:
                        times[name] = None
                    continue
                try:
                    delta = (parse_human(fat) - parse_human(sat)).total_seconds()
                except ValueError:
                    if name not in times:
                        times[name] = None
                    continue
                if delta < 0:
                    if name not in times:
                        times[name] = None
                    continue
                if times.get(name) is None:
                    times[name] = 0.0
                times[name] += delta
        return times

    def per_step_costs(self) -> dict[str, float | None]:
        costs: dict[str, float | None] = {}
        for s in self._sessions:
            for step in s.get("steps", []) or []:
                name = step.get("name")
                if name is None:
                    continue
                data = step.get("data")
                if not isinstance(data, dict) or "llm_cost_usd" not in data:
                    if name not in costs:
                        costs[name] = None
                    continue
                val = data["llm_cost_usd"]
                if not isinstance(val, (int, float)):
                    if name not in costs:
                        costs[name] = None
                    continue
                if costs.get(name) is None:
                    costs[name] = 0.0
                costs[name] += val
        return costs
