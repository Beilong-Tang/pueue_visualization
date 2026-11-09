"""Read task data from `pueue status --json` and turn it into Task objects."""

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from .cache import CacheResult, TTLCache

COMMAND_TIMEOUT = 10

RUNNING_STATES = {"Running", "Paused"}
PENDING_STATES = {"Queued", "Stashed", "Locked"}
FAILED_STATES = {"Failed", "FailedToSpawn", "Errored", "DependencyFailed"}
CANCELLED_STATES = {"Killed"}

# fractional seconds longer than Python 3.10's fromisoformat accepts (pueue prints nanoseconds)
_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


class PueueError(RuntimeError):
    pass


# ---------------------------------------------------------------- parsing helpers

def parse_time(value) -> datetime | None:
    """'2026-11-07T22:29:09.975630231-05:00' -> aware datetime (naive input is taken as server-local)."""
    if not value or not isinstance(value, str):
        return None
    value = _FRACTION_RE.sub(r"\1", value.strip()).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.astimezone() if dt.tzinfo is None else dt


def now() -> datetime:
    return datetime.now().astimezone()


# ---------------------------------------------------------------- Task model

@dataclass
class Task:
    task_id: int
    command: str = ""
    original_command: str = ""
    path: str = ""
    group: str = "default"
    label: str = ""
    dependencies: list[int] = field(default_factory=list)
    priority: int = 0
    state: str = "Unknown"  # Running, Paused, Queued, Stashed, Locked, Success, Failed, Killed, ...
    exit_code: int | None = None  # Failed(n)
    spawn_error: str = ""  # FailedToSpawn("...")
    created: datetime | None = None
    enqueued: datetime | None = None
    enqueue_at: datetime | None = None  # stashed with --delay
    start: datetime | None = None
    end: datetime | None = None

    @property
    def category(self) -> str:
        """One of 'running', 'pending', 'finished'."""
        if self.state in RUNNING_STATES:
            return "running"
        if self.state in PENDING_STATES:
            return "pending"
        return "finished"

    @property
    def is_active(self) -> bool:
        return self.category != "finished"

    @property
    def state_group(self) -> str:
        """Color group for the UI: running, paused, pending, completed, failed, cancelled."""
        if self.state == "Paused":
            return "paused"
        if self.category in ("running", "pending"):
            return self.category
        if self.state == "Success":
            return "completed"
        if self.state in CANCELLED_STATES:
            return "cancelled"
        return "failed"

    @property
    def state_label(self) -> str:
        """What `pueue status` prints: 'Failed (3)', 'Dependency failed', ..."""
        if self.state == "Failed" and self.exit_code is not None:
            return f"Failed ({self.exit_code})"
        return {"DependencyFailed": "Dependency failed", "FailedToSpawn": "Failed to spawn"}.get(self.state, self.state)

    @property
    def elapsed(self) -> timedelta | None:
        """Run time: start -> end, or start -> now while running/paused."""
        if not self.start:
            return None
        end = self.end or (now() if self.category == "running" else None)
        return max(end - self.start, timedelta(0)) if end else None

    @property
    def waited(self) -> timedelta | None:
        """Time in the queue: enqueued -> start, or enqueued -> now while queued."""
        if not self.enqueued:
            return None
        end = self.start or (now() if self.state in ("Queued", "Locked") else None)
        return max(end - self.enqueued, timedelta(0)) if end else None

    @property
    def name(self) -> str:
        """Label if set, else the command (used for search and the page title)."""
        return self.label or self.command


def _apply_status(task: Task, status, raw: dict) -> None:
    """Fill state and times from pueue's status field.

    pueue >= 4: {"Done": {"enqueued_at", "start", "end", "result"}}, {"Running": {...}}, ...
    pueue 3:    "Running" / {"Done": "Success"}, with start/end/enqueued_at on the task itself.
    """
    if isinstance(status, str):
        kind, info = status, {}
    elif isinstance(status, dict) and len(status) == 1:
        kind, info = next(iter(status.items()))
    else:
        kind, info = "Unknown", {}
    if kind == "Locked" and isinstance(info, dict):  # keep the times of the status it came from
        prev = info.get("previous_status")
        if prev is not None:
            _apply_status(task, prev, raw)
    # pueue 3 Done: the value is the result itself ("Success" or {"Failed": 2})
    if not isinstance(info, dict) or (kind == "Done" and "result" not in info and "start" not in info):
        info = {"result": info}
    times = {**raw, **info}
    task.enqueued = parse_time(times.get("enqueued_at")) or task.enqueued
    task.enqueue_at = parse_time(times.get("enqueue_at")) or task.enqueue_at
    task.start = parse_time(times.get("start")) or task.start
    task.end = parse_time(times.get("end")) or task.end

    if kind != "Done":
        task.state = kind
        return
    result = info.get("result", "Unknown")
    if isinstance(result, dict) and len(result) == 1:
        kind, value = next(iter(result.items()))
        task.state = kind
        if kind == "Failed":
            task.exit_code = value if isinstance(value, int) else None
        elif kind == "FailedToSpawn":
            task.spawn_error = str(value)
    else:
        task.state = str(result)


def parse_status(text: str) -> tuple[list[Task], dict[str, dict]]:
    """`pueue status --json` -> (tasks newest first, groups by name).

    Env variables in the output are dropped right here so they can never reach a page.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PueueError(f"pueue status --json: invalid JSON ({exc})")
    if not isinstance(data, dict):
        raise PueueError("pueue status --json: unexpected output")
    tasks = []
    raw_tasks = data.get("tasks") or {}
    for raw in (raw_tasks.values() if isinstance(raw_tasks, dict) else raw_tasks):
        try:
            task = Task(
                task_id=int(raw["id"]),
                command=str(raw.get("command") or ""),
                original_command=str(raw.get("original_command") or ""),
                path=str(raw.get("path") or ""),
                group=str(raw.get("group") or "default"),
                label=str(raw.get("label") or ""),
                dependencies=[int(d) for d in raw.get("dependencies") or []],
                priority=int(raw.get("priority") or 0),
                created=parse_time(raw.get("created_at")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PueueError(f"pueue status --json: unexpected task entry ({exc})")
        _apply_status(task, raw.get("status"), raw)
        tasks.append(task)
    groups = {}
    for name, g in (data.get("groups") or {}).items():
        g = g if isinstance(g, dict) else {}
        groups[name] = {"status": str(g.get("status", "?")), "parallel": int(g.get("parallel_tasks") or 0)}
    for t in tasks:  # a task's group should always exist, but don't crash if it doesn't
        groups.setdefault(t.group, {"status": "?", "parallel": 0})
    tasks.sort(key=lambda t: t.task_id, reverse=True)
    return tasks, groups


# ---------------------------------------------------------------- running commands

def run_command(args: list[str], timeout: float = COMMAND_TIMEOUT) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, errors="replace")
    except FileNotFoundError:
        raise PueueError(f"{args[0]} not found on PATH")
    except subprocess.TimeoutExpired:
        raise PueueError(f"{args[0]} timed out after {timeout}s")
    if proc.returncode != 0:
        err = re.sub(r"\x1b\[[0-9;]*m", "", proc.stderr).strip()
        raise PueueError(f"{args[0]} failed ({proc.returncode}): {err}")
    return proc.stdout


@dataclass
class Snapshot:
    tasks: list[Task]
    groups: dict[str, dict]


@dataclass
class PueueClient:
    config: str = ""  # passed to `pueue -c` when set
    ttl: float = 5.0
    pueue: str = "pueue"
    runner: Callable[[list[str]], str] = run_command

    def __post_init__(self):
        self.cache = TTLCache(self.ttl)

    def status_args(self) -> list[str]:
        return [self.pueue, *(["-c", self.config] if self.config else []), "status", "--json"]

    def snapshot(self) -> CacheResult:
        """Tasks and groups, cached for `ttl` seconds; keeps the last good data if pueue fails."""
        return self.cache.get("status", lambda: Snapshot(*parse_status(self.runner(self.status_args()))))

    def task(self, task_id: int) -> Task | None:
        for t in self.snapshot().value.tasks:
            if t.task_id == task_id:
                return t
        return None
