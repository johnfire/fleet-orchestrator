"""The deterministic flow engine — the control plane.

It walks a flow node by node, invoking agents through adapters, running gates, and
routing on pass/fail. It never guesses at a human gate: it opens the gate, records
that the task is blocked, and stops until a decision arrives. Every transition is
recorded as an event, so the engine holds no hidden state and can resume after a
crash by re-reading the stream.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from .adapters.base import Adapter, AgentRequest
from .events import Event, EventStream, EventType
from .flow import Binding, Flow, Node, NodeType, OnFail, load_flow
from .gates import run_gate
from .notify import NullNotifier, Notifier
from .store import TaskState, TaskStatus, TaskStore
from .workspace import commit_all, create_worktree, push_branch, remove_worktree

_TERMINAL = "end"
_ESCALATE = "escalate"


class Engine:
    def __init__(self, project_root: Path, adapters: dict[str, Adapter],
                 notifier: Notifier | None = None) -> None:
        self._root = project_root
        self._flows_dir = project_root / "flows"
        self._runs_dir = project_root / "runs"
        self._adapters = adapters
        self._notifier = notifier or NullNotifier()
        self.store = TaskStore(self._runs_dir)

    def start_task(self, flow_name: str, request: str, target_repo: str | None = None) -> str:
        flow = self._load(flow_name)
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        workspace = self._runs_dir / task_id / "workspace"
        task_branch = self._prepare_workspace(flow, task_id, workspace, target_repo)
        stream = self.store.stream(task_id)
        self._emit(stream, task_id, EventType.TASK_CREATED, data={
            "flow_name": flow_name,
            "request": request,
            "workspace": str(workspace),
            "target_repo": target_repo,
            "task_branch": task_branch,
        })
        self._enter(stream, task_id, flow.start)
        self.run(task_id)
        return task_id

    def _prepare_workspace(self, flow: Flow, task_id: str, workspace: Path,
                           target_repo: str | None) -> str | None:
        """Create an isolated worktree when a target repo is given; else a plain dir."""
        uses_worktree = target_repo and flow.workspace.get("strategy") == "git-worktree"
        if not uses_worktree:
            workspace.mkdir(parents=True, exist_ok=True)
            return None
        branch = flow.workspace.get("task_branch", "task/{task_id}").format(task_id=task_id)
        base = flow.workspace.get("base_branch", "main")
        outcome = create_worktree(target_repo, workspace, branch, base)
        if not outcome.ok:
            raise RuntimeError(f"worktree setup failed: {outcome.output}")
        return branch

    _HALTED = (TaskStatus.BLOCKED_ON_HUMAN, TaskStatus.ESCALATED, TaskStatus.HANDED_OVER,
               TaskStatus.COMPLETED, TaskStatus.ABORTED)

    def run(self, task_id: str) -> TaskState:
        """Advance the task until it blocks on a human or reaches a terminal state.

        Any unexpected error while processing a node (a crashed adapter, a bad flow edit)
        escalates the task instead of crashing the process — a single failing task must
        never wedge the orchestrator or lose the event trail.
        """
        while True:
            state = self.store.state(task_id)
            assert state is not None
            if state.status in self._HALTED:
                return state
            try:
                flow = self._load(state.flow_name)
                node = flow.node(state.current_node)
                self._process(flow, state, node)
            except Exception as exc:  # isolate the failure to this task
                stream = self.store.stream(task_id)
                self._emit(stream, task_id, EventType.ESCALATED, state.current_node,
                           {"reason": f"engine error at {state.current_node}: {exc}"})
                return self.store.state(task_id)

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

    def resolve_escalation(self, task_id: str, action: str, agent: str | None = None,
                           model: str | None = None, role: str | None = None) -> TaskState:
        """Human resolution of an escalation: retry, reassign, take_over, or abort."""
        state = self.store.state(task_id)
        assert state is not None and state.status is TaskStatus.ESCALATED, "task is not escalated"
        stream = self.store.stream(task_id)
        node_id = state.current_node
        data: dict = {"action": action}
        if action == "reassign":
            role = role or (self._load(state.flow_name).node(node_id).role if node_id else None)
            if not (role and agent):
                raise ValueError("reassign needs a role and an agent")
            override = {"agent": agent}
            if model:
                override["model"] = model
            data.update({"role": role, "override": override})
        elif action not in ("retry", "take_over", "abort"):
            raise ValueError(f"unknown escalation action '{action}'")

        self._emit(stream, task_id, EventType.ESCALATION_RESOLVED, node_id, data)
        if action == "abort":
            self._emit(stream, task_id, EventType.TASK_ABORTED, node_id)
            return self.store.state(task_id)
        if action == "take_over":
            return self.store.state(task_id)
        self._enter(stream, task_id, node_id)  # retry / reassign: re-run the failed node
        return self.run(task_id)

    def cleanup_task(self, task_id: str) -> TaskState:
        """Remove a finished task's worktree. Only terminal tasks; the event trail stays."""
        state = self.store.state(task_id)
        assert state is not None, f"unknown task {task_id}"
        if state.status not in (TaskStatus.COMPLETED, TaskStatus.ABORTED):
            return state
        if state.target_repo and state.workspace and Path(state.workspace).exists():
            outcome = remove_worktree(state.target_repo, Path(state.workspace))
            self._emit(self.store.stream(task_id), task_id, EventType.WORKSPACE_CLEANED, None,
                       {"ok": outcome.ok, "output": outcome.output})
        return self.store.state(task_id)

    def cleanup_finished(self) -> list[str]:
        """Sweep worktrees of all completed/aborted tasks. Returns the ids cleaned."""
        cleaned = []
        for task_id in self.store.list_task_ids():
            state = self.store.state(task_id)
            if state and state.status in (TaskStatus.COMPLETED, TaskStatus.ABORTED):
                self.cleanup_task(task_id)
                cleaned.append(task_id)
        return cleaned

    def _process(self, flow: Flow, state: TaskState, node: Node) -> None:
        stream = self.store.stream(state.task_id)
        if node.node_type is NodeType.HUMAN_GATE:
            self._open_gate(stream, state.task_id, node)
        elif node.node_type is NodeType.ACTION:
            self._run_action(stream, state, node)
        else:
            self._run_agent(flow, state, node, stream)

    def _run_agent(self, flow: Flow, state: TaskState, node: Node, stream: EventStream) -> None:
        binding = self._effective_binding(flow, state, node.role)
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
        artifacts = {**result.artifacts, **self._capture_produced(node, state.workspace)}
        self._emit(stream, state.task_id, EventType.AGENT_RESULT, node.node_id, {
            "succeeded": result.succeeded, "cost_usd": result.cost_usd,
            "artifacts": artifacts, "findings": result.findings,
        })

        gates_passed = self._run_gates(stream, state.task_id, flow, node, state.workspace)
        verdict_ok, verdict_findings = self._check_verdict(node, state.workspace)
        if result.succeeded and gates_passed and verdict_ok:
            self._emit(stream, state.task_id, EventType.NODE_PASSED, node.node_id)
            self._goto(stream, state.task_id, node, node.on_pass or _TERMINAL)
        else:
            reason = verdict_findings or result.findings or "gate failed"
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
        carried = self._write_carry(on_fail, state, reason, attempts + 1)
        self._emit(stream, state.task_id, EventType.REWORK_ROUTED, node.node_id, {
            "route": on_fail.route, "carry": on_fail.carry, "reason": reason,
            "attempts": attempts + 1, "artifacts": carried,
        })
        self._enter(stream, state.task_id, on_fail.route)

    def _write_carry(self, on_fail: OnFail, state: TaskState, reason: str, attempt: int) -> dict[str, str]:
        """Persist findings to the carry file so the next builder pass receives them."""
        if not (on_fail.carry and state.workspace):
            return {}
        carry_path = Path(state.workspace) / on_fail.carry
        carry_path.write_text(
            f"# Rework findings (pass {attempt})\n\n{reason}\n", encoding="utf-8")
        return {on_fail.carry: str(carry_path)}

    def _run_gates(self, stream: EventStream, task_id: str, flow: Flow, node: Node,
                   workspace: str | None) -> bool:
        all_passed = True
        for gate in node.gates:
            outcome = run_gate(gate, flow.gate_commands.get(gate), workspace or ".")
            self._emit(stream, task_id, EventType.GATE_RESULT, node.node_id, outcome.as_event_data())
            all_passed = all_passed and outcome.passed
        return all_passed

    def _run_action(self, stream: EventStream, state: TaskState, node: Node) -> None:
        if node.action == "git_push":
            self._git_push(stream, state, node)
            return
        self._emit(stream, state.task_id, EventType.ACTION_RUN, node.node_id,
                   {"action": node.action, "skipped": "unknown action"})
        self._goto(stream, state.task_id, node, node.on_pass or _TERMINAL)

    def _git_push(self, stream: EventStream, state: TaskState, node: Node) -> None:
        if not (state.target_repo and state.task_branch and state.workspace):
            self._emit(stream, state.task_id, EventType.ACTION_RUN, node.node_id,
                       {"action": "git_push", "skipped": "no target repo / branch"})
            self._goto(stream, state.task_id, node, node.on_pass or _TERMINAL)
            return
        flow = self._load(state.flow_name)
        remote = flow.workspace.get("remote", "origin")
        committed = commit_all(state.workspace, f"task {state.task_id}: automated build")
        pushed = push_branch(state.workspace, remote, state.task_branch)
        self._emit(stream, state.task_id, EventType.ACTION_RUN, node.node_id, {
            "action": "git_push", "branch": state.task_branch, "remote": remote,
            "committed": committed.output, "pushed": pushed.ok, "push_output": pushed.output,
        })
        if pushed.ok:
            self._goto(stream, state.task_id, node, node.on_pass or _TERMINAL)
        else:
            self._escalate(stream, state.task_id, node, f"push failed: {pushed.output}")

    def _open_gate(self, stream: EventStream, task_id: str, node: Node) -> None:
        self._emit(stream, task_id, EventType.HUMAN_GATE_OPENED, node.node_id, {
            "prompt": node.prompt, "options": node.options, "default": node.default,
        })
        self._push(EventType.HUMAN_GATE_OPENED.value, task_id, f"{task_id}: needs a decision",
                   node.prompt or "gate", list(node.options))

    def _escalate(self, stream: EventStream, task_id: str, node: Node, reason: str) -> None:
        self._emit(stream, task_id, EventType.ESCALATED, node.node_id, {"reason": reason})
        self._push("escalation", task_id, f"{task_id}: escalated", reason, ["retry", "abort"])

    def _push(self, kind: str, task_id: str, title: str, message: str, choices: list[str]) -> None:
        try:
            self._notifier.notify(kind, task_id, title, message, choices)
        except Exception as exc:  # a notifier must never break the run
            print(f"[engine] notifier raised (non-fatal): {exc}")

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
        parts = [self._load_template(node.instruction), f"\n\n## Task request\n{state.request}\n"]
        for name in node.inputs:
            content = self._read_artifact(node, state, name)
            if content is not None:
                parts.append(f"\n\n## Input: {name}\n{content}\n")
        return "".join(parts)

    def _effective_binding(self, flow: Flow, state: TaskState, role: str | None) -> Binding | None:
        base = flow.bindings.get(role)
        override = state.binding_overrides.get(role)
        if not override:
            return base
        return Binding(
            agent=override["agent"],
            model=override.get("model", base.model if base else None),
            effort=override.get("effort", base.effort if base else None),
        )

    def _gather_inputs(self, node: Node, state: TaskState) -> dict[str, str]:
        return {name: state.artifacts[name] for name in node.inputs if name in state.artifacts}

    def _capture_produced(self, node: Node, workspace: str | None) -> dict[str, str]:
        if not workspace:
            return {}
        produced: dict[str, str] = {}
        for filename in node.produces:
            candidate = Path(workspace) / filename
            if candidate.exists():
                produced[filename] = str(candidate)
        return produced

    def _check_verdict(self, node: Node, workspace: str | None) -> tuple[bool, str | None]:
        if not node.verdict_file:
            return True, None
        if not workspace:
            return False, f"no workspace to read verdict {node.verdict_file}"
        path = Path(workspace) / node.verdict_file
        if not path.exists():
            return False, f"reviewer emitted no verdict at {node.verdict_file}"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False, f"unparseable verdict at {node.verdict_file}"
        if str(data.get("verdict", "")).lower() == "pass":
            return True, None
        return False, data.get("findings") or f"verdict={data.get('verdict')}"

    def _read_artifact(self, node: Node, state: TaskState, name: str) -> str | None:
        path = state.artifacts.get(name)
        if path is None and state.workspace:
            candidate = Path(state.workspace) / name
            path = str(candidate) if candidate.exists() else None
        if path and Path(path).exists():
            try:
                return Path(path).read_text(encoding="utf-8")
            except OSError:
                return None
        return None

    def _load_template(self, instruction: str | None) -> str:
        if not instruction:
            return ""
        candidate = self._root / instruction
        return candidate.read_text(encoding="utf-8") if candidate.exists() else instruction

    def _load(self, flow_name: str) -> Flow:
        return load_flow(self._flows_dir / f"{flow_name}.yaml")
