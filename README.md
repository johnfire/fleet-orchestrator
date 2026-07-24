# fleet-orchestrator

Orchestrates several coding agents in different harnesses through a human-authored
flow: a smart model plans, a cheaper model builds from the plan, a *different* model
reviews, then push, then optionally a *different* model pentests. The human composes
the flow and can change it mid-run.

**Primary goal is research** — comparing models/harnesses at each stage (feeds the
model-router work). Shipped software is the byproduct that validates the pipeline.

## Design in one screen

Three contracts are the whole system:

1. **Flow definition** — human-authored YAML (`flows/*.yaml`), re-read fresh at every
   transition. Flow is mutable data, never compiled in.
2. **Task / handoff artifact** — spec + branch + evidence, carried as the task travels.
   The planner emits acceptance tests; they are the ground truth for builder and reviewer.
3. **Adapter contract** — every agent looks identical to the engine:
   `invoke(worktree, instruction, session_id) -> AgentResult`. Adding a model is one adapter.

Principles:

- **Control plane is deterministic code**, not an LLM. Models are workers at nodes.
- **One append-only event stream per task** (`runs/<task_id>/events.jsonl`, `task_id` =
  correlation id). It is simultaneously the live feed, the convergence trace, the audit
  log, and the cost dataset. State is derived by folding the events.
- **Dashboard is a dumb read model** over that stream — orchestration survives if it dies.
- **Rework loop**: a failing reviewer/pentester routes the task back to the builder with
  the findings attached; fresh builder *and* fresh reviewer each pass;
  `max_iterations → escalate`.
- **Per-task git-worktree isolation** so concurrent tasks can't stomp shared checkouts.

## Status

Increment 1 — the spine — is in place: event stream, task store, flow engine (agent
nodes, human gates, routing, rework counter), a mock adapter, and the real Claude Code
adapter. Run `pytest` to see a flow execute end to end.

Not built yet: builder/reviewer/pentester templates, objective gate execution
(build/test/lint), the dashboard, the ntfy back-channel. See the flow strawman in
`flows/standard-build.yaml` for the target.

## Layout

```
flows/                 flow definitions (the recipes)
orchestrator/
  events.py            event types + append-only JSONL stream
  store.py             task store; state folded from the event stream
  flow.py              YAML flow loader + node model
  engine.py            deterministic flow engine
  adapters/
    base.py            Adapter protocol + AgentResult
    mock.py            deterministic adapter for tests
    claude_code.py     real `claude -p` adapter (stream-json)
  cli.py               start / advance / decide a task
templates/             instruction templates fed to agents (plan.md ...)
runs/                  per-task event streams + workspaces (gitignored)
tests/
```
