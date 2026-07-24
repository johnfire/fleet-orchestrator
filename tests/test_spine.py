"""Spine tests: prove a flow executes end to end over the event stream.

Uses the mock adapter so nothing is spent and the reviewer can be made to fail on
demand to exercise the rework loop. Runs the real flows/standard-build.yaml, so these
also prove the shipped recipe parses and routes correctly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from orchestrator.adapters.mock import MockAdapter
from orchestrator.engine import Engine
from orchestrator.events import EventType
from orchestrator.store import TaskStatus

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS = ("claude-desktop", "opencode", "codex", "hermes")
PASS_VERDICTS = {
    "review.json": '{"verdict":"pass","findings":""}',
    "pentest.json": '{"verdict":"pass","findings":""}',
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "flows").mkdir()
    shutil.copy(REPO_ROOT / "flows" / "standard-build.yaml", tmp_path / "flows")
    return tmp_path


def _engine(project: Path, fail_roles: set[str] | None = None,
            writes_files: dict[str, str] | None = None) -> tuple[Engine, MockAdapter]:
    adapter = MockAdapter(fail_roles=fail_roles,
                          writes_files=PASS_VERDICTS if writes_files is None else writes_files)
    return Engine(project, {name: adapter for name in AGENTS}), adapter


def _event_types(engine: Engine, task_id: str) -> list[EventType]:
    return [event.event_type for event in engine.store.stream(task_id).read()]


def test_flow_blocks_at_first_human_gate(project: Path) -> None:
    engine, _ = _engine(project)
    task_id = engine.start_task("standard-build", "add a feature")
    state = engine.store.state(task_id)
    assert state.status is TaskStatus.BLOCKED_ON_HUMAN
    assert state.current_node == "approve_plan"
    assert state.open_gate["options"]["approve"] == "build"
    assert state.total_cost_usd == pytest.approx(0.01)  # planner ran once


def test_happy_path_runs_to_completion(project: Path) -> None:
    engine, adapter = _engine(project)
    task_id = engine.start_task("standard-build", "add a feature")

    engine.submit_decision(task_id, "approve")
    mid = engine.store.state(task_id)
    assert mid.status is TaskStatus.BLOCKED_ON_HUMAN
    assert mid.current_node == "decide_next"  # build + review passed

    engine.submit_decision(task_id, "both")  # pentest, then push
    final = engine.store.state(task_id)
    assert final.status is TaskStatus.COMPLETED
    assert final.current_node is None

    roles_run = [call.role for call in adapter.calls]
    assert roles_run == ["planner", "builder", "reviewer", "pentester"]  # 'both' → pentest then push
    assert EventType.ACTION_RUN in _event_types(engine, task_id)
    assert final.total_cost_usd == pytest.approx(0.04)


def test_review_failure_reworks_then_escalates(project: Path) -> None:
    engine, adapter = _engine(project, fail_roles={"reviewer"})
    task_id = engine.start_task("standard-build", "add a feature")
    engine.submit_decision(task_id, "approve")

    state = engine.store.state(task_id)
    assert state.status is TaskStatus.ESCALATED
    assert state.rework_counts["build"] == 3  # max_iterations on the review node

    builder_runs = sum(1 for call in adapter.calls if call.role == "builder")
    reviewer_runs = sum(1 for call in adapter.calls if call.role == "reviewer")
    assert builder_runs == 4 and reviewer_runs == 4  # initial + 3 reworks
    rework_events = [t for t in _event_types(engine, task_id) if t is EventType.REWORK_ROUTED]
    assert len(rework_events) == 4  # 3 routed + 1 exhausted


def test_reviewer_verdict_fail_reworks_and_carries_findings(project: Path) -> None:
    fail_verdict = {
        "review.json": '{"verdict":"fail","findings":"missing acceptance test AC3"}',
        "pentest.json": '{"verdict":"pass","findings":""}',
    }
    engine, adapter = _engine(project, writes_files=fail_verdict)
    task_id = engine.start_task("standard-build", "add a feature")
    engine.submit_decision(task_id, "approve")

    state = engine.store.state(task_id)
    assert state.status is TaskStatus.ESCALATED  # agent succeeds, but verdict fails every pass
    assert state.rework_counts["build"] == 3
    # The reviewer's findings were carried to the builder as an input artifact.
    assert "review_notes.md" in state.artifacts
    assert "missing acceptance test AC3" in Path(state.artifacts["review_notes.md"]).read_text()


def test_fresh_session_per_invocation(project: Path) -> None:
    engine, adapter = _engine(project)
    task_id = engine.start_task("standard-build", "add a feature")
    engine.submit_decision(task_id, "approve")
    session_ids = [call.session_id for call in adapter.calls]
    assert len(session_ids) == len(set(session_ids))  # never reused — fresh every pass
