"""Dashboard read-model tests: the data the UI renders, and a decision round-trip.

The HTTP layer is a thin shell; these test the functions behind it plus the fact that a
decision applied through the engine shows up in the read model.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import base64

from orchestrator.adapters.mock import MockAdapter
from orchestrator.dashboard import _Handler, task_detail, task_summaries
from orchestrator.engine import Engine

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS = ("claude-desktop", "opencode", "codex", "hermes")
PASS_VERDICTS = {
    "review.json": '{"verdict":"pass","findings":""}',
    "pentest.json": '{"verdict":"pass","findings":""}',
}


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    (tmp_path / "flows").mkdir()
    shutil.copy(REPO_ROOT / "flows" / "standard-build.yaml", tmp_path / "flows")
    return Engine(tmp_path, {name: MockAdapter(writes_files=PASS_VERDICTS) for name in AGENTS})


def test_summaries_expose_gate_for_inbox(engine: Engine) -> None:
    task_id = engine.start_task("standard-build", "add a feature")
    summaries = task_summaries(engine.store)
    assert len(summaries) == 1
    row = summaries[0]
    assert row["task_id"] == task_id
    assert row["status"] == "blocked_on_human"
    assert row["open_gate"]["options"]["approve"] == "build"  # inbox renders these as buttons


def test_detail_includes_event_stream(engine: Engine) -> None:
    task_id = engine.start_task("standard-build", "add a feature")
    detail = task_detail(engine.store, task_id)
    types = [event["type"] for event in detail["events"]]
    assert "task_created" in types and "human_gate_opened" in types


def test_decision_is_reflected_in_read_model(engine: Engine) -> None:
    task_id = engine.start_task("standard-build", "add a feature")
    engine.submit_decision(task_id, "approve")  # what the dashboard POST does
    row = task_summaries(engine.store)[0]
    assert row["current_node"] == "decide_next"  # advanced past build + review


class _FakeServer:
    def __init__(self, auth):
        self.auth = auth


class _AuthProbe(_Handler):
    def __init__(self, header):  # bypass BaseHTTPRequestHandler.__init__ (no socket)
        self.headers = {"Authorization": header} if header else {}
        self.server = _FakeServer(("chris", "s3cret"))


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def test_auth_accepts_correct_credentials() -> None:
    assert _AuthProbe(_basic("chris", "s3cret"))._authorized() is True


def test_auth_rejects_wrong_and_missing_credentials() -> None:
    assert _AuthProbe(_basic("chris", "wrong"))._authorized() is False
    assert _AuthProbe(None)._authorized() is False


def test_no_auth_configured_allows_all() -> None:
    probe = _AuthProbe(None)
    probe.server = _FakeServer(None)  # no credentials configured
    assert probe._authorized() is True
