"""Default agent-name → adapter binding.

The flow's `bindings` block names an `agent` per role; this maps those names to the
concrete adapter that drives them. Kept in one place so a deployment can swap or add
agents without touching the engine.
"""

from __future__ import annotations

from .base import Adapter
from .claude_code import ClaudeCodeAdapter
from .codex_cli import CodexAdapter
from .hermes_cli import HermesAdapter
from .opencode_cli import OpencodeAdapter


def default_adapters() -> dict[str, Adapter]:
    return {
        "claude-desktop": ClaudeCodeAdapter(),
        "opencode": OpencodeAdapter(),
        "codex": CodexAdapter(),
        "hermes": HermesAdapter(),
    }
