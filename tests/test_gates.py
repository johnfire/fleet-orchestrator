"""Gate execution tests: real commands actually gate node progression.

Covers the gate runner directly and end to end through the engine — a passing gate
lets the node through, a failing gate drives the rework loop to escalation. Uses shell
`true`/`false` so the tests are hermetic (no build system, no tokens).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.adapters.mock import MockAdapter
from orchestrator.engine import Engine
from orchestrator.gates import run_gate
from orchestrator.store import TaskStatus

GATE_FLOW = """
flow: {{name: gate-test, version: 1, start: build}}
bindings:
  builder:  {{agent: mock}}
  reviewer: {{agent: mock}}
gate_commands:
  check: "{command}"
nodes:
  - id: build
    role: builder
    on_pass: review
    on_fail: {{route: build, max_iterations: 1}}
  - id: review
    role: reviewer
    gates: [check]
    on_pass: end
    on_fail: {{route: build, max_iterations: 1, on_exhausted: escalate}}
"""


def _project(tmp_path: Path, command: str) -> Path:
    (tmp_path / "flows").mkdir()
    (tmp_path / "flows" / "gate-test.yaml").write_text(GATE_FLOW.format(command=command), encoding="utf-8")
    return tmp_path


def _engine(project: Path) -> Engine:
    adapter = MockAdapter()
    return Engine(project, {"mock": adapter})


def test_run_gate_pass(tmp_path: Path) -> None:
    outcome = run_gate("check", "true", str(tmp_path))
    assert outcome.passed and not outcome.skipped and outcome.exit_code == 0


def test_run_gate_fail_captures_output(tmp_path: Path) -> None:
    outcome = run_gate("check", "echo boom >&2; false", str(tmp_path))
    assert outcome.passed is False
    assert "boom" in outcome.output_tail


def test_run_gate_unconfigured_is_visible_skip(tmp_path: Path) -> None:
    outcome = run_gate("check", None, str(tmp_path))
    assert outcome.passed is True and outcome.skipped is True


def test_passing_gate_lets_node_through(tmp_path: Path) -> None:
    engine = _engine(_project(tmp_path, "true"))
    task_id = engine.start_task("gate-test", "do it")
    assert engine.store.state(task_id).status is TaskStatus.COMPLETED


def test_failing_gate_drives_rework_to_escalation(tmp_path: Path) -> None:
    engine = _engine(_project(tmp_path, "false"))
    task_id = engine.start_task("gate-test", "do it")
    state = engine.store.state(task_id)
    assert state.status is TaskStatus.ESCALATED  # gate never passes → rework exhausts
    assert state.rework_counts["build"] == 1
