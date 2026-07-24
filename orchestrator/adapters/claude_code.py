"""Real adapter for Claude Code, driven headless via `claude -p`.

Verified flags (Claude Code 2.1.x):
    claude -p "<instruction>" --model <model> --session-id <uuid>
           --output-format stream-json --verbose --add-dir <workspace>
           --permission-mode acceptEdits

stream-json emits one JSON object per line; the terminating object has
type == "result" and carries total_cost_usd and the final text.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .base import AgentRequest, AgentResult


class ClaudeCodeAdapter:
    name = "claude-code"

    def __init__(self, binary: str = "claude", timeout_seconds: int = 1800) -> None:
        self._binary = binary
        self._timeout_seconds = timeout_seconds

    def invoke(self, request: AgentRequest) -> AgentResult:
        command = self._build_command(request)
        completed = subprocess.run(
            command,
            cwd=str(request.workspace),
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
        )
        return self._parse(request, completed)

    def _build_command(self, request: AgentRequest) -> list[str]:
        command = [
            self._binary,
            "-p",
            request.instruction,
            "--session-id",
            request.session_id,
            "--output-format",
            "stream-json",
            "--verbose",
            "--add-dir",
            str(request.workspace),
            "--permission-mode",
            "acceptEdits",
        ]
        if request.model:
            command += ["--model", request.model]
        return command

    def _parse(self, request: AgentRequest, completed: subprocess.CompletedProcess) -> AgentResult:
        result_event = self._final_result_event(completed.stdout)
        if result_event is None:
            return AgentResult(
                succeeded=False,
                output=completed.stderr.strip() or "no result event from claude",
                session_id=request.session_id,
                raw={"returncode": completed.returncode},
            )
        is_error = bool(result_event.get("is_error")) or result_event.get("subtype") != "success"
        return AgentResult(
            succeeded=completed.returncode == 0 and not is_error,
            output=result_event.get("result", ""),
            session_id=result_event.get("session_id", request.session_id),
            cost_usd=float(result_event.get("total_cost_usd", 0.0) or 0.0),
            raw=result_event,
        )

    @staticmethod
    def _final_result_event(stdout: str) -> dict | None:
        result_event: dict | None = None
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                result_event = event
        return result_event
