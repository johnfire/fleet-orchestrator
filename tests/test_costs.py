"""cost_report tests: group by model/node/agent, count rework invocations, flag unmetered."""

from __future__ import annotations

from orchestrator.costs import cost_report
from orchestrator.events import Event, EventType


def _result(seq: int, node: str, agent: str, model: str, cost: float,
            tokens: dict, seconds: float) -> Event:
    return Event(task_id="t", seq=seq, event_type=EventType.AGENT_RESULT, node_id=node,
                 data={"cost_usd": cost, "agent": agent, "model": model,
                       "tokens": tokens, "elapsed_seconds": seconds})


def test_groups_by_model_node_agent_and_totals() -> None:
    events = [
        Event(task_id="t", seq=0, event_type=EventType.TASK_CREATED),  # ignored
        _result(1, "build", "opencode", "deepseek/deepseek-chat", 0.006,
                {"input": 100, "output": 50, "reasoning": 0, "cache_read": 10, "cache_write": 0}, 2.0),
        _result(2, "build", "opencode", "deepseek/deepseek-chat", 0.006,  # a rework pass
                {"input": 100, "output": 50}, 2.0),
        _result(3, "review", "codex", "gpt-5.6-terra", 0.0,
                {"input": 200, "output": 5, "reasoning": 3}, 5.0),
    ]
    report = cost_report(events)

    assert report["total"]["cost_usd"] == 0.012
    assert report["total"]["invocations"] == 3

    # model is the primary axis (opencode can be any of many models)
    deepseek = report["by_model"]["deepseek/deepseek-chat"]
    assert deepseek["invocations"] == 2
    assert deepseek["tokens"]["input"] == 200
    assert deepseek["agent"] == "opencode"

    # rework is visible as invocation count on the node
    assert report["by_node"]["build"]["invocations"] == 2
    assert report["by_node"]["build"]["tokens"]["output"] == 100

    # $0 with tokens = subscription, flagged not-free
    assert "gpt-5.6-terra" in report["unmetered_models"]
    assert "deepseek/deepseek-chat" not in report["unmetered_models"]


def test_empty_stream_is_zeroed() -> None:
    report = cost_report([])
    assert report["total"]["cost_usd"] == 0.0
    assert report["by_model"] == {}
    assert report["unmetered_models"] == []
