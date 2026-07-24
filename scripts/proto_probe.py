"""Minimal stdio JSON-RPC probe: does an agent answer a protocol handshake?

Spawns `command`, sends an `initialize` request as newline-delimited JSON-RPC,
reads whatever comes back for `deadline` seconds, prints parsed JSON. Proves the
handshake without a full prompt turn (no tokens spent, no model call).
"""

from __future__ import annotations

import json
import select
import subprocess
import sys
import time


def probe(label: str, command: list[str], mode: str, deadline: float = 8.0) -> None:
    print(f"\n===== {label}  ({' '.join(command)}) =====")
    try:
        proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
    except FileNotFoundError as exc:
        print(f"  spawn failed: {exc}")
        return

    if mode == "acp":
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}}}}
    else:  # mcp
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "spike", "version": "0"}}}

    outbox = [json.dumps(init)]
    if mode == "mcp":
        outbox.append(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
        outbox.append(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))

    try:
        for line in outbox:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
    except BrokenPipeError:
        pass

    end = time.time() + deadline
    while time.time() < end:
        ready, _, _ = select.select([proc.stdout], [], [], 0.4)
        if ready:
            line = proc.stdout.readline()
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = json.loads(stripped)
            except json.JSONDecodeError:
                print(f"  [non-json] {stripped[:200]}")
                continue
            _summarize(message)
        if proc.poll() is not None:
            break

    stderr_tail = ""
    try:
        proc.terminate()
        _, stderr_tail = proc.communicate(timeout=3)
    except Exception:
        proc.kill()
    if stderr_tail:
        print(f"  [stderr] {stderr_tail.strip()[:300]}")


def _summarize(message: dict) -> None:
    if "result" in message and isinstance(message["result"], dict):
        result = message["result"]
        keys = sorted(result.keys())
        print(f"  RESULT id={message.get('id')} keys={keys}")
        for interesting in ("protocolVersion", "agentCapabilities", "capabilities", "serverInfo", "authMethods"):
            if interesting in result:
                print(f"    {interesting}: {json.dumps(result[interesting])[:300]}")
        if "tools" in result and isinstance(result["tools"], list):
            print(f"    tools: {[tool.get('name') for tool in result['tools']]}")
    elif "error" in message:
        print(f"  ERROR id={message.get('id')}: {json.dumps(message['error'])[:300]}")
    elif "method" in message:
        print(f"  NOTIFY method={message['method']}")
    else:
        print(f"  MSG {json.dumps(message)[:200]}")


if __name__ == "__main__":
    probe("opencode ACP", ["opencode", "acp"], "acp")
    probe("hermes ACP", ["hermes", "acp"], "acp")
    probe("codex MCP", ["codex", "mcp-server"], "mcp")
