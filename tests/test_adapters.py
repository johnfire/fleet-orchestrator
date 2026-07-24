"""Adapter parser tests over REAL captured output (laptop smoke, 2026-07-24).

These fixtures are the actual JSON the tools emitted for the prompt "Reply with
exactly one word: ready", so the parsers are verified against reality, not a guess.
No live calls here — safe for CI.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.adapters.base import AgentRequest
from orchestrator.adapters.codex_cli import CodexAdapter
from orchestrator.adapters.hermes_cli import HermesAdapter
from orchestrator.adapters.opencode_cli import OpencodeAdapter

OPENCODE_STDOUT = "\n".join([
    '{"type":"step_start","sessionID":"ses_06cc","part":{"type":"step-start"}}',
    '{"type":"text","sessionID":"ses_06cc","part":{"type":"text","text":"ready"}}',
    '{"type":"step_finish","sessionID":"ses_06cc","part":{"type":"step-finish","reason":"stop",'
    '"tokens":{"total":10623,"input":10622,"output":1},"cost":0.00148736}}',
])

CODEX_STDOUT = "\n".join([
    '{"type":"thread.started","thread_id":"019f9336-d8ad-7653"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"ready"}}',
    '{"type":"turn.completed","usage":{"input_tokens":18661,"output_tokens":5}}',
])

HERMES_USAGE_OK = (
    '{"estimated_cost_usd":0.0021,"input_tokens":120,"output_tokens":3,'
    '"session_id":"hs_42","model":"deepseek","provider":"openrouter",'
    '"completed":true,"failed":false}'
)

HERMES_USAGE_FAILED = (
    '{"estimated_cost_usd":null,"session_id":null,"completed":false,"failed":true}'
)


def _request(tmp_path: Path, role: str = "builder") -> AgentRequest:
    return AgentRequest(role=role, instruction="do it", workspace=tmp_path, session_id="mint-uuid")


def test_opencode_parser(tmp_path: Path) -> None:
    result = OpencodeAdapter._parse(_request(tmp_path), OPENCODE_STDOUT, returncode=0)
    assert result.succeeded is True
    assert result.output == "ready"
    assert result.session_id == "ses_06cc"
    assert result.cost_usd == 0.00148736


def test_opencode_failure_surfaces_output_as_findings(tmp_path: Path) -> None:
    result = OpencodeAdapter._parse(_request(tmp_path), "", returncode=1)
    assert result.succeeded is False
    assert result.findings  # never None on failure, so rework has something to carry


def test_codex_parser(tmp_path: Path) -> None:
    result = CodexAdapter._parse(_request(tmp_path, "reviewer"), CODEX_STDOUT, returncode=0)
    assert result.succeeded is True
    assert result.output == "ready"
    assert result.session_id == "019f9336-d8ad-7653"
    assert result.cost_usd == 0.0  # no USD under ChatGPT auth
    assert result.raw["usage"]["output_tokens"] == 5


def test_hermes_parser_success(tmp_path: Path) -> None:
    usage_path = tmp_path / "hermes_usage.json"
    usage_path.write_text(HERMES_USAGE_OK, encoding="utf-8")
    result = HermesAdapter._parse(_request(tmp_path, "pentester"), "ready", 0, usage_path)
    assert result.succeeded is True
    assert result.session_id == "hs_42"
    assert result.cost_usd == 0.0021


def test_hermes_parser_failure(tmp_path: Path) -> None:
    usage_path = tmp_path / "hermes_usage.json"
    usage_path.write_text(HERMES_USAGE_FAILED, encoding="utf-8")
    result = HermesAdapter._parse(_request(tmp_path, "pentester"), "boom", 0, usage_path)
    assert result.succeeded is False
    assert result.findings == "boom"
