"""Thin CLI over the engine: start a task, check status, answer a gate.

    python -m orchestrator.cli start standard-build "add data export to artcrm"
    python -m orchestrator.cli status task-1a2b3c4d
    python -m orchestrator.cli decide task-1a2b3c4d approve
    python -m orchestrator.cli list

Only the Claude Code adapter is wired so far, bound to the `claude-desktop` agent.
Nodes whose agent has no adapter escalate cleanly rather than crashing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapters.claude_code import ClaudeCodeAdapter
from .engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_engine() -> Engine:
    adapters = {"claude-desktop": ClaudeCodeAdapter()}
    return Engine(PROJECT_ROOT, adapters)


def _print_state(engine: Engine, task_id: str) -> None:
    state = engine.store.state(task_id)
    if state is None:
        print(f"unknown task {task_id}")
        return
    summary = {
        "task_id": state.task_id,
        "flow": state.flow_name,
        "status": state.status.value,
        "current_node": state.current_node,
        "cost_usd": round(state.total_cost_usd, 4),
        "rework_counts": state.rework_counts,
        "open_gate": state.open_gate,
    }
    print(json.dumps(summary, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="start a new task")
    start.add_argument("flow")
    start.add_argument("request")

    status = sub.add_parser("status", help="show a task's state")
    status.add_argument("task_id")

    decide = sub.add_parser("decide", help="answer an open human gate")
    decide.add_argument("task_id")
    decide.add_argument("choice")

    sub.add_parser("list", help="list known tasks")

    args = parser.parse_args(argv)
    engine = build_engine()

    if args.command == "start":
        task_id = engine.start_task(args.flow, args.request)
        _print_state(engine, task_id)
    elif args.command == "status":
        _print_state(engine, args.task_id)
    elif args.command == "decide":
        engine.submit_decision(args.task_id, args.choice)
        _print_state(engine, args.task_id)
    elif args.command == "list":
        for task_id in engine.store.list_task_ids():
            _print_state(engine, task_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
