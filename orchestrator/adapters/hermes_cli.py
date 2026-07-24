"""hermes adapter, driven headless via `hermes -z` (one-shot).

hermes prints the response to stdout and, with `--usage-file`, writes a JSON usage
record after the run. Usage schema verified live (2026-07-24): keys include
`estimated_cost_usd`, `input_tokens`, `output_tokens`, `session_id`, `model`,
`provider`, `completed`, `failed`. We run in the task workspace (cwd) rather than
`--worktree`, since the engine owns worktree isolation.

Note: an autonomous pipeline may need `--yolo`/`--accept-hooks` so hermes does not
block on permission prompts. Left off by default; enable per deployment.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .base import AgentRequest, AgentResult


class HermesAdapter:
    name = "hermes"

    def __init__(self, binary: str = "hermes", auto_approve: bool = False,
                 timeout_seconds: int = 1800) -> None:
        self._binary = binary
        self._auto_approve = auto_approve
        self._timeout_seconds = timeout_seconds

    def invoke(self, request: AgentRequest) -> AgentResult:
        usage_path = request.workspace / "hermes_usage.json"
        command = [self._binary, "-z", request.instruction, "--usage-file", str(usage_path)]
        if request.model:
            command += ["-m", request.model]
        if self._auto_approve:
            command.append("--yolo")
        completed = subprocess.run(
            command, cwd=str(request.workspace), capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=self._timeout_seconds,
        )
        return self._parse(request, completed.stdout, completed.returncode, usage_path)

    @classmethod
    def _parse(cls, request: AgentRequest, stdout: str, returncode: int,
               usage_path: Path) -> AgentResult:
        usage = cls._read_usage(usage_path)
        output = stdout.strip()
        failed = bool(usage.get("failed"))
        completed_flag = usage.get("completed")
        succeeded = returncode == 0 and not failed and completed_flag is not False
        return AgentResult(
            succeeded=succeeded,
            output=output,
            session_id=usage.get("session_id") or request.session_id,
            cost_usd=float(usage.get("estimated_cost_usd") or 0.0),
            findings=None if succeeded else (output or "hermes run failed"),
            raw={"usage": usage},
        )

    @staticmethod
    def _read_usage(usage_path: Path) -> dict:
        if not usage_path.exists():
            return {}
        try:
            return json.loads(usage_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
