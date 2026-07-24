"""Task state — derived, never stored. Fold the event stream into a snapshot.

Keeping state as a pure fold over immutable events means a crash mid-pipeline loses
nothing: restart, replay, continue. It also makes the whole system testable by
asserting over events rather than mutable objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

from .events import Event, EventStream, EventType


class TaskStatus(str, Enum):
    RUNNING = "running"
    BLOCKED_ON_HUMAN = "blocked_on_human"
    ESCALATED = "escalated"
    COMPLETED = "completed"
    ABORTED = "aborted"


@dataclass(frozen=True)
class TaskState:
    """A snapshot folded from a task's event stream."""

    task_id: str
    flow_name: str
    status: TaskStatus = TaskStatus.RUNNING
    current_node: str | None = None
    request: str = ""
    workspace: str | None = None
    rework_counts: dict[str, int] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    open_gate: dict[str, Any] | None = None
    artifacts: dict[str, str] = field(default_factory=dict)


def fold(events: list[Event]) -> TaskState | None:
    """Rebuild current state by applying each event in order."""
    state: TaskState | None = None
    for event in events:
        state = _apply(state, event)
    return state


def _apply(state: TaskState | None, event: Event) -> TaskState:
    if event.event_type is EventType.TASK_CREATED:
        return TaskState(
            task_id=event.task_id,
            flow_name=event.data.get("flow_name", ""),
            request=event.data.get("request", ""),
            workspace=event.data.get("workspace"),
        )
    assert state is not None, "first event must be TASK_CREATED"

    if event.event_type is EventType.NODE_ENTERED:
        return replace(state, current_node=event.node_id, status=TaskStatus.RUNNING, open_gate=None)
    if event.event_type is EventType.AGENT_RESULT:
        cost = float(event.data.get("cost_usd", 0.0) or 0.0)
        artifacts = dict(state.artifacts)
        artifacts.update(event.data.get("artifacts", {}))
        return replace(state, total_cost_usd=round(state.total_cost_usd + cost, 6), artifacts=artifacts)
    if event.event_type is EventType.REWORK_ROUTED:
        if event.data.get("exhausted"):
            return state  # the cap was hit; this routes to on_exhausted, not another attempt
        target = event.data["route"]
        counts = dict(state.rework_counts)
        counts[target] = counts.get(target, 0) + 1
        return replace(state, rework_counts=counts)
    if event.event_type is EventType.HUMAN_GATE_OPENED:
        return replace(state, status=TaskStatus.BLOCKED_ON_HUMAN, open_gate=event.data)
    if event.event_type is EventType.HUMAN_DECISION:
        return replace(state, open_gate=None)
    if event.event_type is EventType.ESCALATED:
        return replace(state, status=TaskStatus.ESCALATED, open_gate=event.data)
    if event.event_type is EventType.TASK_COMPLETED:
        return replace(state, status=TaskStatus.COMPLETED, current_node=None, open_gate=None)
    if event.event_type is EventType.TASK_ABORTED:
        return replace(state, status=TaskStatus.ABORTED, current_node=None, open_gate=None)
    return state


class TaskStore:
    """Locates each task's event stream and folds it on demand."""

    def __init__(self, runs_dir: Path) -> None:
        self._runs_dir = runs_dir
        self._runs_dir.mkdir(parents=True, exist_ok=True)

    def stream(self, task_id: str) -> EventStream:
        return EventStream(self._runs_dir / task_id / "events.jsonl")

    def state(self, task_id: str) -> TaskState | None:
        return fold(list(self.stream(task_id).read()))

    def list_task_ids(self) -> list[str]:
        return sorted(child.name for child in self._runs_dir.iterdir() if child.is_dir())
