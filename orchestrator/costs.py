"""Cost and token reporting — a pure fold over a task's agent_result events.

Every agent_result carries cost, normalized tokens, the model, the agent, the node,
and latency. This rolls them up per model (the primary axis — one harness like opencode
can run any of many models), per node (so rework cost is visible via invocation counts),
and per agent, plus run totals.

Honesty: a $0 cost with nonzero tokens means an unmetered subscription (ChatGPT/Claude
plan), NOT free. Such models are flagged so the numbers don't mislead model-router calls.
"""

from __future__ import annotations

from .events import Event, EventType

_TOKEN_KEYS = ("input", "output", "reasoning", "cache_read", "cache_write")


def _empty_tokens() -> dict[str, int]:
    return {key: 0 for key in _TOKEN_KEYS}


def _add_tokens(into: dict[str, int], more: dict) -> None:
    for key in _TOKEN_KEYS:
        into[key] += int(more.get(key, 0) or 0)


def _new_bucket() -> dict:
    return {"cost_usd": 0.0, "tokens": _empty_tokens(), "invocations": 0, "seconds": 0.0}


def _accumulate(bucket: dict, cost: float, tokens: dict, seconds: float) -> None:
    bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 8)
    _add_tokens(bucket["tokens"], tokens)
    bucket["invocations"] += 1
    bucket["seconds"] = round(bucket["seconds"] + seconds, 2)


def cost_report(events: list[Event]) -> dict:
    """Roll up cost/tokens/latency for one task from its agent_result events."""
    by_model: dict[str, dict] = {}
    by_node: dict[str, dict] = {}
    by_agent: dict[str, dict] = {}
    total = _new_bucket()

    for event in events:
        if event.event_type is not EventType.AGENT_RESULT:
            continue
        data = event.data
        cost = float(data.get("cost_usd", 0.0) or 0.0)
        tokens = data.get("tokens", {}) or {}
        seconds = float(data.get("elapsed_seconds", 0.0) or 0.0)
        model = data.get("model") or "unknown"
        agent = data.get("agent") or "unknown"
        node = event.node_id or "unknown"

        _accumulate(by_model.setdefault(model, _new_bucket()), cost, tokens, seconds)
        _accumulate(by_node.setdefault(node, _new_bucket()), cost, tokens, seconds)
        _accumulate(by_agent.setdefault(agent, _new_bucket()), cost, tokens, seconds)
        _accumulate(total, cost, tokens, seconds)
        by_model[model].setdefault("agent", agent)

    unmetered = [
        model for model, bucket in by_model.items()
        if bucket["cost_usd"] == 0.0 and sum(bucket["tokens"].values()) > 0
    ]
    return {
        "total": total,
        "by_model": by_model,
        "by_node": by_node,
        "by_agent": by_agent,
        "unmetered_models": unmetered,  # $0 = subscription, not free
    }
