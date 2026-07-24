"""Escalation-resolution tests: retry, reassign, take_over, abort.

Drives a task to escalation (reviewer verdict fails until the rework cap), then exercises
each human resolution. Reassign proves the mid-flow "swap the agent for a role" lever.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from orchestrator.adapters.mock import MockAdapter
from orchestrator.engine import Engine
from orchestrator.store import TaskStatus

REPO_ROOT = Path(__file__).resolve().parents[1]
FAIL = '{"verdict":"fail","findings":"missing AC3"}'
PASS = '{"verdict":"pass","findings":""}'
PENTEST_PASS = '{"verdict":"pass","findings":""}'


@pytest.fixture
def escalated(tmp_path: Path):
    (tmp_path / "flows").mkdir()
    shutil.copy(REPO_ROOT / "flows" / "standard-build.yaml", tmp_path / "flows")
    fail_mock = MockAdapter(writes_files={"review.json": FAIL, "pentest.json": PENTEST_PASS})
    good_reviewer = MockAdapter(writes_files={"review.json": PASS, "pentest.json": PENTEST_PASS})
    adapters = {name: fail_mock for name in ("claude-desktop", "opencode", "codex", "hermes")}
    adapters["good-reviewer"] = good_reviewer
    engine = Engine(tmp_path, adapters)
    task_id = engine.start_task("standard-build", "add a feature")
    engine.submit_decision(task_id, "approve")
    assert engine.store.state(task_id).status is TaskStatus.ESCALATED
    return engine, task_id, fail_mock, good_reviewer


def test_abort(escalated) -> None:
    engine, task_id, *_ = escalated
    engine.resolve_escalation(task_id, "abort")
    assert engine.store.state(task_id).status is TaskStatus.ABORTED


def test_take_over(escalated) -> None:
    engine, task_id, *_ = escalated
    engine.resolve_escalation(task_id, "take_over")
    assert engine.store.state(task_id).status is TaskStatus.HANDED_OVER


def test_retry_reruns_and_re_escalates(escalated) -> None:
    engine, task_id, fail_mock, _ = escalated
    before = len(fail_mock.calls)
    engine.resolve_escalation(task_id, "retry")
    state = engine.store.state(task_id)
    assert state.status is TaskStatus.ESCALATED  # same failing reviewer → escalates again
    assert len(fail_mock.calls) > before  # but it really ran the loop again
    assert state.rework_counts["build"] == 3  # counter reset then re-exhausted


def test_reassign_to_working_agent_completes_review(escalated) -> None:
    engine, task_id, _, good_reviewer = escalated
    engine.resolve_escalation(task_id, "reassign", role="reviewer", agent="good-reviewer")
    state = engine.store.state(task_id)
    assert state.status is TaskStatus.BLOCKED_ON_HUMAN
    assert state.current_node == "decide_next"  # reassigned reviewer passed → advanced
    assert state.binding_overrides["reviewer"]["agent"] == "good-reviewer"
    assert any(call.role == "reviewer" for call in good_reviewer.calls)


def test_reassign_requires_role_and_agent(escalated) -> None:
    engine, task_id, *_ = escalated
    with pytest.raises(ValueError):
        engine.resolve_escalation(task_id, "reassign", agent=None, role=None)
