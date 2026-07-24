"""codex adapter, driven headless via `codex exec --json`.

Output schema verified live (codex 0.145, ChatGPT auth, 2026-07-24): newline-delimited
JSON events. `thread.started` carries `thread_id` (the session); `item.completed` with
`item.type == "agent_message"` carries the output text; `turn.completed` carries a token
`usage` block. No USD cost is reported under ChatGPT auth, so cost_usd stays 0 and tokens
are kept in `raw` for the dataset. Fresh session per call — we never resume.
"""

from __future__ import annotations

import json
import subprocess

from .base import AgentRequest, AgentResult, normalized_tokens


class CodexAdapter:
    name = "codex"

    def __init__(self, binary: str = "codex", sandbox: str = "workspace-write",
                 timeout_seconds: int = 1800) -> None:
        self._binary = binary
        self._sandbox = sandbox
        self._timeout_seconds = timeout_seconds

    def invoke(self, request: AgentRequest) -> AgentResult:
        command = [self._binary, "exec", "--json", "-s", self._sandbox,
                   "--skip-git-repo-check", "-C", str(request.workspace)]
        if request.model:
            command += ["-m", request.model]
        if request.effort:
            command += ["-c", f"model_reasoning_effort={request.effort}"]
        command.append(request.instruction)
        completed = subprocess.run(
            command, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=self._timeout_seconds,
        )
        return self._parse(request, completed.stdout, completed.returncode)

    @classmethod
    def _parse(cls, request: AgentRequest, stdout: str, returncode: int) -> AgentResult:
        text_parts: list[str] = []
        session_id = request.session_id
        usage: dict = {}
        saw_turn_complete = False
        for event in _iter_json(stdout):
            kind = event.get("type")
            if kind == "thread.started":
                session_id = event.get("thread_id", session_id)
            elif kind == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    text_parts.append(item.get("text", ""))
            elif kind == "turn.completed":
                saw_turn_complete = True
                usage = event.get("usage", {})
        output = "".join(text_parts).strip()
        succeeded = returncode == 0 and saw_turn_complete
        tokens = normalized_tokens(
            input=usage.get("input_tokens", 0),
            output=usage.get("output_tokens", 0),
            reasoning=usage.get("reasoning_output_tokens", 0),
            cache_read=usage.get("cached_input_tokens", 0),
            cache_write=usage.get("cache_write_input_tokens", 0),
        )
        return AgentResult(
            succeeded=succeeded,
            output=output,
            session_id=session_id,
            cost_usd=0.0,
            findings=None if succeeded else (output or "codex exec failed"),
            tokens=tokens,
            raw={"usage": usage},
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
