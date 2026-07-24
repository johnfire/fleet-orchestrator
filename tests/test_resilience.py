"""Anti-fragility: a crashing adapter escalates its task, it does not crash the engine.

A single failing task must be isolated — the orchestrator stays up, the event trail
survives, and the task is left in a state a human can resolve.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from orchestrator.adapters.base import AgentRequest, AgentResult
from orchestrator.adapters.mock import MockAdapter
from orchestrator.engine import Engine
from orchestrator.store import TaskStatus

REPO_ROOT = Path(__file__).resolve().parents[1]
PASS_VERDICTS = {
    "review.json": '{"verdict":"pass","findings":""}',
    "pentest.json": '{"verdict":"pass","findings":""}',
}


class CrashingAdapter:
    name = "crashing"

    def invoke(self, request: AgentRequest) -> AgentResult:
        raise RuntimeError("adapter subprocess exploded")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "flows").mkdir()
    shutil.copy(REPO_ROOT / "flows" / "standard-build.yaml", tmp_path / "flows")
    return tmp_path


def test_crashing_adapter_escalates_not_crashes(project: Path) -> None:
    # planner crashes on the very first node.
    agents = {"claude-desktop": CrashingAdapter()}
    agents.update({n: MockAdapter(writes_files=PASS_VERDICTS) for n in ("opencode", "codex", "hermes")})
    engine = Engine(project, agents)

    task_id = engine.start_task("standard-build", "add a feature")  # must not raise
    state = engine.store.state(task_id)
    assert state.status is TaskStatus.ESCALATED
    assert "exploded" in state.open_gate["reason"]


def test_engine_survives_and_other_tasks_run(project: Path) -> None:
    agents = {"claude-desktop": CrashingAdapter()}
    agents.update({n: MockAdapter(writes_files=PASS_VERDICTS) for n in ("opencode", "codex", "hermes")})
    engine = Engine(project, agents)
    crashed = engine.start_task("standard-build", "one")

    # A healthy engine still serves other tasks after the crash.
    healthy_agents = {n: MockAdapter(writes_files=PASS_VERDICTS)
                      for n in ("claude-desktop", "opencode", "codex", "hermes")}
    engine2 = Engine(project, healthy_agents)
    ok = engine2.start_task("standard-build", "two")
    assert engine.store.state(crashed).status is TaskStatus.ESCALATED
    assert engine2.store.state(ok).status is TaskStatus.BLOCKED_ON_HUMAN
