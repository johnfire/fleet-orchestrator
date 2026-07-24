"""Objective gates — the ground truth a node must clear, run as real commands.

A gate is a named shell command (build, test, lint, standards check) resolved from the
flow's `gate_commands` map and run in the task workspace. Gates run BEFORE a reviewer's
judgment counts: a node passes only if every gate passes AND the agent succeeded. A gate
with no configured command is a *visible* skip (recorded in the event stream), never a
silent pass — enforcement is opt-in by configuring the command.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

_OUTPUT_TAIL_CHARS = 2000


@dataclass(frozen=True)
class GateOutcome:
    name: str
    passed: bool
    skipped: bool = False
    exit_code: int | None = None
    output_tail: str = ""
    command: str | None = None

    def as_event_data(self) -> dict:
        return {
            "gate": self.name,
            "passed": self.passed,
            "skipped": self.skipped,
            "exit_code": self.exit_code,
            "output_tail": self.output_tail,
        }


def run_gate(name: str, command: str | None, workspace: str, timeout_seconds: int = 900) -> GateOutcome:
    if command is None:
        return GateOutcome(name=name, passed=True, skipped=True,
                           output_tail="no command configured for this gate")
    try:
        completed = subprocess.run(
            command, shell=True, cwd=workspace, capture_output=True, text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return GateOutcome(name=name, passed=False, exit_code=None, command=command,
                           output_tail=f"gate timed out after {timeout_seconds}s")
    combined = (completed.stdout + completed.stderr)[-_OUTPUT_TAIL_CHARS:]
    return GateOutcome(
        name=name,
        passed=completed.returncode == 0,
        exit_code=completed.returncode,
        output_tail=combined.strip(),
        command=command,
    )
