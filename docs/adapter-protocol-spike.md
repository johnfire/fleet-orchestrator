# Spike: can a common protocol collapse the adapter layer?

Date: 2026-07-24. Question: instead of four bespoke CLI wrappers, can we drive the
agents through one protocol? Probed live on the laptop with `scripts/proto_probe.py`
(sends a JSON-RPC `initialize` handshake over stdio — no prompt turn, no tokens spent).

## What each agent actually speaks (verified)

| Agent | Native protocol | Handshake result |
|---|---|---|
| opencode | **ACP v1** (`opencode acp`) | `protocolVersion: 1`; sessions fork/resume/list/close; prompt embeddedContext + image |
| hermes | **ACP v1** (`hermes acp`) | `protocolVersion: 1`; sessions fork/list/resume; `hermes acp --check` → OK |
| codex | **MCP** (`codex mcp-server`) | `protocolVersion: 2024-11-05`; `tools/list` → `['codex', 'codex-reply']` |
| claude code | none native | stream-json CLI / Agent SDK (see `adapters/claude_code.py`) |

## Verdict: not one protocol, but it collapses to three mechanisms

- **opencode and hermes are the same ACP version (1) with the same session/prompt shape.**
  One ACP client drives both — they differ *only* in the spawn command. Any future
  ACP agent (e.g. Gemini CLI) is then ~zero code.
- **codex is driven over MCP** by calling its `codex` tool (start) and `codex-reply`
  (continue). One MCP client; reusable for any future MCP-server agent.
- **claude code** stays on the stream-json CLI adapter we already have.

So the layer is **ACP client + MCP client + Claude CLI = 3 adapters for 4 agents**, and
the ACP one scales for free. That is a real reduction from four bespoke wrappers, and
ACP/MCP are stable public protocols.

Both ACP agents advertise `fork`/`resume`, but our design uses a fresh session every
pass — we just call `session/new` each time and ignore resume.

## Recommendation: CLI wrappers now, ACP/MCP next increment

Ship the pipeline first (the standing principle). One-shot CLI adapters
(`opencode run --format json`, `codex exec`, `hermes -z --usage-file`) are the same
`subprocess.run → parse json` shape as the working Claude adapter — fastest path to a
green end-to-end loop, no JSON-RPC/session plumbing.

Adopt ACP/MCP in a later increment when the async design wants what they add:
- **persistent server processes** (no per-node cold start — and hermes' slow MCP boot
  becomes a one-time cost instead of per-call),
- **streaming `session/update` notifications**, which map directly onto our per-task
  event stream — richer live trace for the dashboard than parsing a final CLI blob.

The only throwaway is two thin CLI wrappers (opencode, hermes); the ACP client that
replaces them is not throwaway. Cheap trade for shipping the loop sooner.
