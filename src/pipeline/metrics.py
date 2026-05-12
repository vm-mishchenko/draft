import contextlib
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

_HUMAN_FMT = "%Y-%m-%d %H:%M:%S UTC"
_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def now_human() -> str:
    return datetime.now(UTC).strftime(_HUMAN_FMT)


def parse_human(s: str) -> datetime:
    dt = datetime.strptime(s, _HUMAN_FMT)
    return dt.replace(tzinfo=UTC)


class KnownMetric(StrEnum):
    """Enumeration of well-known metric keys; extend to add reserved names."""

    pass


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

    def __init__(self, sessions: list, run_dir: Path):
        self._sessions = sessions
        self._run_dir = run_dir

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

    def _reconcile_unclosed(self):
        from pipeline.heartbeat import HEARTBEAT_FILENAME

        hb_path = self._run_dir / HEARTBEAT_FILENAME
        if not self._sessions or self._sessions[-1]["finished_at"] is not None:
            with contextlib.suppress(OSError):
                hb_path.unlink(missing_ok=True)
            return

        last = self._sessions[-1]
        ts = None

        with contextlib.suppress(OSError, ValueError):
            ts = parse_human(hb_path.read_text().strip())

        if ts is None:
            step_times = []
            for step in last.get("steps", []):
                fat = step.get("finished_at")
                if fat is not None:
                    with contextlib.suppress(ValueError):
                        step_times.append(parse_human(fat))
            if step_times:
                ts = max(step_times)

        if ts is None:
            with contextlib.suppress(ValueError, KeyError):
                ts = parse_human(last["started_at"])

        ts_str = ts.strftime(_HUMAN_FMT) if ts is not None else now_human()

        last["finished_at"] = ts_str
        last["exit_code"] = -1
        for step in last.get("steps", []):
            if step.get("finished_at") is None:
                step["finished_at"] = ts_str
                step["exit_code"] = -1

        with contextlib.suppress(OSError):
            hb_path.unlink(missing_ok=True)
