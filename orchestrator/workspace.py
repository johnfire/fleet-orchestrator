"""Per-task git isolation. Each task gets its own worktree on its own branch.

Concurrent tasks must never share a working tree — fleet agents already collide on
shared checkouts. A worktree gives each task an isolated checkout of the target repo
on a dedicated `task/<id>` branch, while sharing the repo's object store and remotes,
so `push` from the worktree reaches the same origin.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

_COMMIT_IDENTITY = ["-c", "user.name=fleet-orchestrator", "-c", "user.email=orchestrator@localhost"]


@dataclass(frozen=True)
class GitOutcome:
    ok: bool
    output: str


def _git(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def create_worktree(repo: str, workspace: Path, branch: str, base: str) -> GitOutcome:
    workspace.parent.mkdir(parents=True, exist_ok=True)
    result = _git(["worktree", "add", "-b", branch, str(workspace), base], cwd=repo)
    return GitOutcome(result.returncode == 0, (result.stdout + result.stderr).strip())


def commit_all(workspace: str | Path, message: str) -> GitOutcome:
    _git(["add", "-A"], cwd=workspace)
    status = _git(["status", "--porcelain"], cwd=workspace)
    if not status.stdout.strip():
        return GitOutcome(True, "nothing to commit")
    result = _git([*_COMMIT_IDENTITY, "commit", "-m", message], cwd=workspace)
    return GitOutcome(result.returncode == 0, (result.stdout + result.stderr).strip())


def push_branch(workspace: str | Path, remote: str, branch: str) -> GitOutcome:
    result = _git(["push", remote, branch], cwd=workspace)
    return GitOutcome(result.returncode == 0, (result.stdout + result.stderr).strip())


def remove_worktree(repo: str, workspace: Path) -> GitOutcome:
    result = _git(["worktree", "remove", "--force", str(workspace)], cwd=repo)
    return GitOutcome(result.returncode == 0, (result.stdout + result.stderr).strip())
