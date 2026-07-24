"""The no-spend demo runs the whole flow to completion with mock agents."""

from __future__ import annotations

import shutil
from pathlib import Path

from orchestrator.demo import run_demo
from orchestrator.store import TaskStatus

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_demo_runs_to_completion(tmp_path: Path) -> None:
    (tmp_path / "flows").mkdir()
    shutil.copy(REPO_ROOT / "flows" / "standard-build.yaml", tmp_path / "flows")
    engine, task_id = run_demo(tmp_path)
    state = engine.store.state(task_id)
    assert state.status is TaskStatus.COMPLETED
    assert state.current_node is None
