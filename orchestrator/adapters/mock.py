"""A deterministic adapter for testing the spine without spending tokens.

Scriptable per role so a test can make the reviewer fail on demand and exercise the
rework loop. It writes any `produces` artifacts to the workspace so downstream nodes
and assertions see real files.
"""

from __future__ import annotations

from pathlib import Path

from .base import AgentRequest, AgentResult


class MockAdapter:
    name = "mock"

    def __init__(self, fail_roles: set[str] | None = None, cost_usd: float = 0.01) -> None:
        self._fail_roles = fail_roles or set()
        self._cost_usd = cost_usd
        self.calls: list[AgentRequest] = []

    def invoke(self, request: AgentRequest) -> AgentResult:
        self.calls.append(request)
        artifact_path = request.workspace / f"{request.role}.out.md"
        artifact_path.write_text(
            f"# mock {request.role} output\nsession={request.session_id}\n",
            encoding="utf-8",
        )
        succeeded = request.role not in self._fail_roles
        return AgentResult(
            succeeded=succeeded,
            output=f"mock {request.role} ran",
            session_id=request.session_id,
            cost_usd=self._cost_usd,
            artifacts={request.role: str(artifact_path)},
            findings=None if succeeded else f"mock {request.role} found problems",
        )
