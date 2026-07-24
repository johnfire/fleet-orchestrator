"""The adapter contract. One shape for every agent, whatever the harness.

    invoke(request) -> AgentResult

Concrete adapters (claude_code, opencode, codex, hermes) differ only in how they
build the command line and parse the JSON the tool emits. Everything above this
layer treats them identically, which is what makes heterogeneity cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AgentRequest:
    """Everything an agent needs for one invocation."""

    role: str
    instruction: str
    workspace: Path
    session_id: str
    model: str | None = None
    effort: str | None = None
    inputs: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResult:
    """The uniform return. `succeeded` gates node progression; `findings` feed rework."""

    succeeded: bool
    output: str
    session_id: str
    cost_usd: float = 0.0
    artifacts: dict[str, str] = field(default_factory=dict)
    findings: str | None = None
    tokens: dict[str, int] = field(default_factory=dict)  # normalized: input/output/reasoning/cache_read/cache_write
    model: str | None = None  # actual model the harness used, if it self-reports (opencode runs 1-of-many)
    raw: dict = field(default_factory=dict)


# Canonical token keys every adapter maps its provider's counts onto.
TOKEN_KEYS = ("input", "output", "reasoning", "cache_read", "cache_write")


def normalized_tokens(**counts: int) -> dict[str, int]:
    """Build a token dict with all canonical keys present (missing -> 0)."""
    return {key: int(counts.get(key, 0) or 0) for key in TOKEN_KEYS}


class Adapter(Protocol):
    name: str

    def invoke(self, request: AgentRequest) -> AgentResult:
        ...
