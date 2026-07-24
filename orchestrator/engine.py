"""The deterministic flow engine — the control plane.

It walks a flow node by node, invoking agents through adapters, running gates, and
routing on pass/fail. It never guesses at a human gate: it opens the gate, records
that the task is blocked, and stops until a decision arrives. Every transition is
recorded as an event, so the engine holds no hidden state and can resume after a
crash by re-reading the stream.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from .adapters.base import Adapter, AgentRequest
from .events import Event, EventStream, EventType
from .flow import Flow, Node, NodeType, OnFail, load_flow
from .gates import run_gate
from .store import TaskState, TaskStatus, TaskStore

_TERMINAL = "end"
_ESCALATE = "escalate"


class Engine:
    def __init__(self, project_root: Path, adapters: dict[str, Adapter]) -> None:
        self._root = project_root
        self._flows_dir = project_root / "flows"
        self._runs_dir = project_root / "runs"
        self._adapters = adapters
        self.store = TaskStore(self._runs_dir)

    def start_task(self, flow_name: str, request: str) -> str:
        flow = self._load(flow_name)
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        workspace = self._runs_dir / task_id / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        stream = self.store.stream(task_id)
        self._emit(stream, task_id, EventType.TASK_CREATED, data={
            "flow_name": flow_name,
            "request": request,
            "workspace": str(workspace),
        })
        self._enter(stream, task_id, flow.start)
        self.run(task_id)
        return task_id

    def run(self, task_id: str) -> TaskState:
        """Advance the task until it blocks on a human or reaches a terminal state."""
        while True:
            state = self.store.state(task_id)
            assert state is not None
            if state.status in (TaskStatus.BLOCKED_ON_HUMAN, TaskStatus.ESCALATED,
                                TaskStatus.COMPLETED, TaskStatus.ABORTED):
                return state
            flow = self._load(state.flow_name)
            node = flow.node(state.current_node)
            self._process(flow, state, node)

    def submit_decision(self, task_id: str, choice: str) -> TaskState:
        state = self.store.state(task_id)
        assert state is not None and state.open_gate is not None, "no open gate to decide"
        node = self._load(state.flow_name).node(state.current_node)
        options = state.open_gate.get("options", {})
        target = options.get(choice)
        if target is None:
            raise ValueError(f"invalid choice '{choice}'; options: {sorted(options)}")
        stream = self.store.stream(task_id)
        self._emit(stream, task_id, EventType.HUMAN_DECISION, node.node_id, {"choice": choice, "target": target})
        self._goto(stream, task_id, node, target)
        return self.run(task_id)

    def _process(self, flow: Flow, state: TaskState, node: Node) -> None:
        stream = self.store.stream(state.task_id)
        if node.node_type is NodeType.HUMAN_GATE:
            self._open_gate(stream, state.task_id, node)
        elif node.node_type is NodeType.ACTION:
            self._run_action(stream, state, node)
        else:
            self._run_agent(flow, state, node, stream)

    def _run_agent(self, flow: Flow, state: TaskState, node: Node, stream: EventStream) -> None:
        binding = flow.bindings.get(node.role)
        if binding is None:
            self._escalate(stream, state.task_id, node, f"no binding for role '{node.role}'")
            return
        adapter = self._adapters.get(binding.agent)
        if adapter is None:
            self._escalate(stream, state.task_id, node, f"no adapter for agent '{binding.agent}'")
            return

        session_id = str(uuid.uuid4())
        request = AgentRequest(
            role=node.role,
            instruction=self._build_instruction(node, state),
            workspace=Path(state.workspace),
            session_id=session_id,
            model=binding.model,
            effort=binding.effort,
            inputs=self._gather_inputs(node, state),
        )
        self._emit(stream, state.task_id, EventType.AGENT_INVOKED, node.node_id, {
            "role": node.role, "agent": binding.agent, "model": binding.model, "session_id": session_id,
        })
        result = adapter.invoke(request)
        self._emit(stream, state.task_id, EventType.AGENT_RESULT, node.node_id, {
            "succeeded": result.succeeded, "cost_usd": result.cost_usd,
            "artifacts": result.artifacts, "findings": result.findings,
        })

        gates_passed = self._run_gates(stream, state.task_id, flow, node, state.workspace)
        if result.succeeded and gates_passed:
            self._emit(stream, state.task_id, EventType.NODE_PASSED, node.node_id)
            self._goto(stream, state.task_id, node, node.on_pass or _TERMINAL)
        else:
            reason = result.findings or "gate failed"
            self._emit(stream, state.task_id, EventType.NODE_FAILED, node.node_id, {"reason": reason})
            self._route_fail(stream, state, node, reason)

    def _route_fail(self, stream: EventStream, state: TaskState, node: Node, reason: str) -> None:
        on_fail = node.on_fail
        if on_fail is None:
            self._escalate(stream, state.task_id, node, reason)
            return
        attempts = state.rework_counts.get(on_fail.route, 0)
        if attempts >= on_fail.max_iterations:
            self._emit(stream, state.task_id, EventType.REWORK_ROUTED, node.node_id, {
                "route": on_fail.route, "exhausted": True, "attempts": attempts,
            })
            self._goto(stream, state.task_id, node, on_fail.on_exhausted)
            return
        self._emit(stream, state.task_id, EventType.REWORK_ROUTED, node.node_id, {
            "route": on_fail.route, "carry": on_fail.carry, "reason": reason, "attempts": attempts + 1,
        })
        self._enter(stream, state.task_id, on_fail.route)

    def _run_gates(self, stream: EventStream, task_id: str, flow: Flow, node: Node,
                   workspace: str | None) -> bool:
        all_passed = True
        for gate in node.gates:
            outcome = run_gate(gate, flow.gate_commands.get(gate), workspace or ".")
            self._emit(stream, task_id, EventType.GATE_RESULT, node.node_id, outcome.as_event_data())
            all_passed = all_passed and outcome.passed
        return all_passed

    def _run_action(self, stream: EventStream, state: TaskState, node: Node) -> None:
        # TODO(increment 2): real git push. For now the action is recorded and passes.
        self._emit(stream, state.task_id, EventType.ACTION_RUN, node.node_id, {"action": node.action})
        self._goto(stream, state.task_id, node, node.on_pass or _TERMINAL)

    def _open_gate(self, stream: EventStream, task_id: str, node: Node) -> None:
        self._emit(stream, task_id, EventType.HUMAN_GATE_OPENED, node.node_id, {
            "prompt": node.prompt, "options": node.options, "default": node.default,
        })

    def _escalate(self, stream: EventStream, task_id: str, node: Node, reason: str) -> None:
        self._emit(stream, task_id, EventType.ESCALATED, node.node_id, {"reason": reason})

    def _goto(self, stream: EventStream, task_id: str, node: Node, target: str) -> None:
        if target == _TERMINAL:
            self._emit(stream, task_id, EventType.TASK_COMPLETED, node.node_id)
        elif target == _ESCALATE:
            self._escalate(stream, task_id, node, "routed to escalate")
        else:
            self._enter(stream, task_id, target)

    def _enter(self, stream: EventStream, task_id: str, node_id: str) -> None:
        self._emit(stream, task_id, EventType.NODE_ENTERED, node_id)

    def _emit(self, stream: EventStream, task_id: str, event_type: EventType,
              node_id: str | None = None, data: dict | None = None) -> None:
        stream.append(Event(
            task_id=task_id, seq=stream.next_seq(), event_type=event_type,
            node_id=node_id, data=data or {},
        ))

    def _build_instruction(self, node: Node, state: TaskState) -> str:
        template = self._load_template(node.instruction)
        return f"{template}\n\n## Task request\n{state.request}\n"

    def _gather_inputs(self, node: Node, state: TaskState) -> dict[str, str]:
        return {name: state.artifacts[name] for name in node.inputs if name in state.artifacts}

    def _load_template(self, instruction: str | None) -> str:
        if not instruction:
            return ""
        candidate = self._root / instruction
        return candidate.read_text(encoding="utf-8") if candidate.exists() else instruction

    def _load(self, flow_name: str) -> Flow:
        return load_flow(self._flows_dir / f"{flow_name}.yaml")
