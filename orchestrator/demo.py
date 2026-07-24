"""A no-spend end-to-end demo: run a flow with mock agents, auto-answering gates.

Lets you watch the whole pipeline execute — plan → build → review → pentest → push —
without invoking any real model or spending anything. Also doubles as a living smoke
test of the wired flow.
"""

from __future__ import annotations

from pathlib import Path

from .adapters.mock import MockAdapter
from .engine import Engine
from .store import TaskState, TaskStatus

_AGENTS = ("claude-desktop", "opencode", "codex", "hermes")
_PASS_VERDICTS = {
    "review.json": '{"verdict":"pass","findings":""}',
    "pentest.json": '{"verdict":"pass","findings":""}',
}
_PREFERRED = ("approve", "both", "push")


def run_demo(project_root: Path, flow_name: str = "standard-build") -> tuple[Engine, str]:
    engine = Engine(project_root, {name: MockAdapter(writes_files=_PASS_VERDICTS) for name in _AGENTS})
    task_id = engine.start_task(flow_name, "DEMO: mock task exercising the pipeline")
    while True:
        state = engine.store.state(task_id)
        if state is None or state.status is not TaskStatus.BLOCKED_ON_HUMAN:
            return engine, task_id
        gate = state.open_gate or {}
        engine.submit_decision(task_id, _auto_choice(gate.get("options", {}), gate.get("default")))


def _auto_choice(options: dict[str, str], default: str | None) -> str:
    if default and default in options:
        return default
    for preferred in _PREFERRED:
        if preferred in options:
            return preferred
    return next(iter(options))
