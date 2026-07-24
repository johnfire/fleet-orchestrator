"""Notifier tests: request building, action buttons, anti-fragility, engine hookup.

No network — NtfyNotifier takes an injected sender, and the engine test uses a recording
notifier. The load-bearing guarantee under test: a notifier failure never breaks the run.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from orchestrator.adapters.mock import MockAdapter
from orchestrator.engine import Engine
from orchestrator.notify import NtfyNotifier
from orchestrator.store import TaskStatus

REPO_ROOT = Path(__file__).resolve().parents[1]
PASS_VERDICTS = {
    "review.json": '{"verdict":"pass","findings":""}',
    "pentest.json": '{"verdict":"pass","findings":""}',
}


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def notify(self, kind, task_id, title, message, choices) -> None:
        self.calls.append((kind, task_id, title, message, tuple(choices)))


def test_ntfy_builds_action_buttons_when_dashboard_set() -> None:
    captured = {}
    notifier = NtfyNotifier(topic="fleet", dashboard_url="https://dash.example",
                            sender=lambda req: captured.update(req=req))
    notifier.notify("human_gate_opened", "task-1", "needs you", "approve?", ["approve", "abort"])
    req = captured["req"]
    assert req.full_url == "https://ntfy.sh/fleet"
    actions = req.headers["Actions"]
    assert "view, dashboard, https://dash.example" in actions
    assert "/api/tasks/task-1/decide" in actions and "approve" in actions


def test_ntfy_swallows_send_errors() -> None:
    def boom(_req):
        raise ConnectionError("network down")
    notifier = NtfyNotifier(topic="fleet", sender=boom)
    notifier.notify("escalation", "task-1", "t", "m", ["retry"])  # must not raise


def test_engine_notifies_on_gate_and_escalation(tmp_path: Path) -> None:
    (tmp_path / "flows").mkdir()
    shutil.copy(REPO_ROOT / "flows" / "standard-build.yaml", tmp_path / "flows")
    recorder = RecordingNotifier()
    agents = {n: MockAdapter(writes_files=PASS_VERDICTS)
              for n in ("claude-desktop", "opencode", "codex", "hermes")}
    engine = Engine(tmp_path, agents, notifier=recorder)
    engine.start_task("standard-build", "add a feature")
    kinds = [call[0] for call in recorder.calls]
    assert "human_gate_opened" in kinds  # fired at approve_plan


def test_failing_notifier_does_not_break_run(tmp_path: Path) -> None:
    class Broken:
        def notify(self, *a, **k):
            raise RuntimeError("boom")

    (tmp_path / "flows").mkdir()
    shutil.copy(REPO_ROOT / "flows" / "standard-build.yaml", tmp_path / "flows")
    agents = {n: MockAdapter(writes_files=PASS_VERDICTS)
              for n in ("claude-desktop", "opencode", "codex", "hermes")}
    engine = Engine(tmp_path, agents, notifier=Broken())
    task_id = engine.start_task("standard-build", "add a feature")
    assert engine.store.state(task_id).status is TaskStatus.BLOCKED_ON_HUMAN  # run survived
