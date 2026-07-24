"""Flow definition loader — the human-authored recipe, read fresh every transition.

A flow is plain data: a set of nodes plus role→agent bindings. The engine reads it
anew at each step so a human can rewrite the flow mid-run and the change takes effect
on the next transition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class NodeType(str, Enum):
    AGENT = "agent"
    HUMAN_GATE = "human_gate"
    ACTION = "action"


@dataclass(frozen=True)
class OnFail:
    """Rework routing: send the task backward with findings attached, but bounded."""

    route: str
    carry: str | None = None
    max_iterations: int = 1
    on_exhausted: str = "escalate"


@dataclass(frozen=True)
class Node:
    node_id: str
    node_type: NodeType
    role: str | None = None
    instruction: str | None = None
    inputs: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()
    action: str | None = None
    prompt: str | None = None
    options: dict[str, str] = field(default_factory=dict)
    default: str | None = None
    on_pass: str | None = None
    on_fail: OnFail | None = None


@dataclass(frozen=True)
class Binding:
    agent: str
    model: str | None = None
    effort: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Flow:
    name: str
    version: int
    start: str
    nodes: dict[str, Node]
    bindings: dict[str, Binding]
    human_channel: dict[str, Any] = field(default_factory=dict)
    workspace: dict[str, Any] = field(default_factory=dict)
    escalate: dict[str, Any] = field(default_factory=dict)
    gate_commands: dict[str, str] = field(default_factory=dict)

    def node(self, node_id: str) -> Node:
        if node_id not in self.nodes:
            raise KeyError(f"flow '{self.name}' has no node '{node_id}'")
        return self.nodes[node_id]


def load_flow(flow_path: Path) -> Flow:
    raw = yaml.safe_load(flow_path.read_text(encoding="utf-8"))
    meta = raw["flow"]
    node_list = [_parse_node(entry) for entry in raw["nodes"]]
    nodes = {node.node_id: node for node in node_list}
    bindings = {role: _parse_binding(spec) for role, spec in raw.get("bindings", {}).items()}
    return Flow(
        name=meta["name"],
        version=int(meta.get("version", 1)),
        start=meta.get("start", node_list[0].node_id),
        nodes=nodes,
        bindings=bindings,
        human_channel=raw.get("human_channel", {}),
        workspace=raw.get("workspace", {}),
        escalate=raw.get("escalate", {}),
        gate_commands=dict(raw.get("gate_commands", {})),
    )


def _parse_binding(spec: dict[str, Any]) -> Binding:
    known = {"agent", "model", "effort"}
    return Binding(
        agent=spec["agent"],
        model=spec.get("model"),
        effort=spec.get("effort"),
        extra={key: value for key, value in spec.items() if key not in known},
    )


def _parse_node(entry: dict[str, Any]) -> Node:
    node_type = NodeType(entry.get("type", NodeType.AGENT.value))
    on_fail_spec = entry.get("on_fail")
    on_fail = None
    if isinstance(on_fail_spec, dict):
        on_fail = OnFail(
            route=on_fail_spec["route"],
            carry=on_fail_spec.get("carry"),
            max_iterations=int(on_fail_spec.get("max_iterations", 1)),
            on_exhausted=on_fail_spec.get("on_exhausted", "escalate"),
        )
    return Node(
        node_id=entry["id"],
        node_type=node_type,
        role=entry.get("role"),
        instruction=entry.get("instruction"),
        inputs=tuple(entry.get("inputs", [])),
        produces=tuple(entry.get("produces", [])),
        gates=tuple(entry.get("gates", [])),
        action=entry.get("run"),
        prompt=entry.get("prompt"),
        options=dict(entry.get("options", {})),
        default=entry.get("default"),
        on_pass=entry.get("on_pass"),
        on_fail=on_fail,
    )
