"""The append-only event stream — the single source of truth for a task.

Every meaningful thing that happens to a task is one immutable event on a JSONL
file (`runs/<task_id>/events.jsonl`). Task state is never stored directly; it is
folded from these events (see store.py). This one stream doubles as the live feed,
the convergence trace, the audit log, and the model-router cost dataset.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator


class EventType(str, Enum):
    TASK_CREATED = "task_created"
    NODE_ENTERED = "node_entered"
    AGENT_INVOKED = "agent_invoked"
    AGENT_RESULT = "agent_result"
    GATE_RESULT = "gate_result"
    NODE_PASSED = "node_passed"
    NODE_FAILED = "node_failed"
    REWORK_ROUTED = "rework_routed"
    HUMAN_GATE_OPENED = "human_gate_opened"
    HUMAN_DECISION = "human_decision"
    ESCALATED = "escalated"
    ESCALATION_RESOLVED = "escalation_resolved"
    ACTION_RUN = "action_run"
    WORKSPACE_CLEANED = "workspace_cleaned"
    TASK_COMPLETED = "task_completed"
    TASK_ABORTED = "task_aborted"


@dataclass(frozen=True)
class Event:
    """One immutable fact about a task, appended to its stream."""

    task_id: str
    seq: int
    event_type: EventType
    node_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json_line(self) -> str:
        payload = asdict(self)
        payload["event_type"] = self.event_type.value
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def from_json_line(line: str) -> "Event":
        payload = json.loads(line)
        payload["event_type"] = EventType(payload["event_type"])
        return Event(**payload)


class EventStream:
    """Append-only reader/writer for one task's event log."""

    def __init__(self, stream_path: Path) -> None:
        self._path = stream_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def next_seq(self) -> int:
        return sum(1 for _ in self.read()) if self._path.exists() else 0

    def append(self, event: Event) -> Event:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(event.to_json_line() + "\n")
        return event

    def read(self) -> Iterator[Event]:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    yield Event.from_json_line(stripped)
