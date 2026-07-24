"""opencode adapter, driven headless via `opencode run --format json`.

Output schema verified live (opencode, deepseek-chat, 2026-07-24): newline-delimited
JSON events. `text` events carry `part.text` (the assistant output); the terminating
`step_finish` event carries `part.cost` and `part.tokens`; every event has `sessionID`.
`opencode run` starts a fresh session by default, which is exactly our per-pass model.
"""

from __future__ import annotations

import json
import subprocess

from .base import AgentRequest, AgentResult


class OpencodeAdapter:
    name = "opencode"

    def __init__(self, binary: str = "opencode", timeout_seconds: int = 1800) -> None:
        self._binary = binary
        self._timeout_seconds = timeout_seconds

    def invoke(self, request: AgentRequest) -> AgentResult:
        command = [self._binary, "run", request.instruction, "--format", "json",
                   "--dir", str(request.workspace)]
        if request.model:
            command += ["-m", request.model]
        if request.effort:
            command += ["--variant", request.effort]
        completed = subprocess.run(
            command, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=self._timeout_seconds,
        )
        return self._parse(request, completed.stdout, completed.returncode)

    @classmethod
    def _parse(cls, request: AgentRequest, stdout: str, returncode: int) -> AgentResult:
        text_parts: list[str] = []
        cost_usd = 0.0
        session_id = request.session_id
        saw_finish = False
        for event in _iter_json(stdout):
            session_id = event.get("sessionID", session_id)
            part = event.get("part", {})
            if event.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif event.get("type") == "step_finish":
                saw_finish = True
                cost_usd += float(part.get("cost", 0.0) or 0.0)
        output = "".join(text_parts).strip()
        succeeded = returncode == 0 and saw_finish
        return AgentResult(
            succeeded=succeeded,
            output=output,
            session_id=session_id,
            cost_usd=round(cost_usd, 8),
            findings=None if succeeded else (output or "opencode run failed"),
        )


def _iter_json(stdout: str):
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            yield json.loads(stripped)
        except json.JSONDecodeError:
            continue
