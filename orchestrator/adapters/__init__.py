"""Adapters make every heterogeneous harness look identical to the engine."""

from .base import Adapter, AgentRequest, AgentResult

__all__ = ["Adapter", "AgentRequest", "AgentResult"]
