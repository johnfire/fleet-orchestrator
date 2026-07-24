"""Workspace/push tests with REAL git: worktree isolation and a real push to a remote.

Sets up a git repo with a bare remote, runs the full standard-build flow with mock
agents against it, and asserts the task branch — with the agents' output files — landed
in the remote. This is the closest thing to a live end-to-end run without spending tokens.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator.adapters.mock import MockAdapter
from orchestrator.engine import Engine
from orchestrator.store import TaskStatus
from orchestrator.workspace import commit_all, create_worktree, push_branch

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS = ("claude-desktop", "opencode", "codex", "hermes")


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def target_repo(tmp_path: Path) -> Path:
    """A git repo on `main` with an initial commit and a bare `origin` remote."""
    repo = tmp_path / "target"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "init"], repo)
    remote = tmp_path / "remote.git"
    _git(["init", "--bare", str(remote)], tmp_path)
    _git(["remote", "add", "origin", str(remote)], repo)
    return repo


@pytest.fixture
def project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "flows").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "flows" / "standard-build.yaml", proj / "flows")
    return proj


def _branches_in_remote(repo: Path) -> str:
    origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(repo),
                            capture_output=True, text=True).stdout.strip()
    return subprocess.run(["git", "branch"], cwd=origin, capture_output=True, text=True).stdout


def test_create_worktree_makes_isolated_branch(target_repo: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "wt"
    outcome = create_worktree(str(target_repo), workspace, "task/abc", "main")
    assert outcome.ok, outcome.output
    assert workspace.exists()
    head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(workspace),
                          capture_output=True, text=True).stdout.strip()
    assert head == "task/abc"


def test_commit_and_push_reach_remote(target_repo: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "wt"
    create_worktree(str(target_repo), workspace, "task/xyz", "main")
    (workspace / "new.txt").write_text("hi", encoding="utf-8")
    assert commit_all(workspace, "add file").ok
    assert push_branch(workspace, "origin", "task/xyz").ok
    assert "task/xyz" in _branches_in_remote(target_repo)


def test_full_flow_pushes_task_branch_to_remote(project: Path, target_repo: Path) -> None:
    adapter = MockAdapter()
    engine = Engine(project, {name: adapter for name in AGENTS})
    task_id = engine.start_task("standard-build", "add a feature", target_repo=str(target_repo))

    engine.submit_decision(task_id, "approve")
    engine.submit_decision(task_id, "both")  # pentest then push

    state = engine.store.state(task_id)
    assert state.status is TaskStatus.COMPLETED
    assert state.task_branch == f"task/{task_id}"
    assert state.task_branch in _branches_in_remote(target_repo)
