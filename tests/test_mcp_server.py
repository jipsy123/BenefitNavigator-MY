"""Smoke tests for the trust-core MCP server (mas/mcp_server.py).

These drive the tools through the real FastMCP machinery (registration, argument
schema, dispatch, error surfacing) via `call_tool` — no network, no Azure. They
prove the transport binding faithfully exposes trust_tools and that a tampered
token fails as an error rather than returning a usable verdict.
"""
from __future__ import annotations

import asyncio
import json

from mas.mcp_server import mcp
from mas.state import ChatState, encode


def _call(tool: str, **arguments) -> dict:
    """Invoke an MCP tool and parse its JSON result block."""
    blocks = asyncio.run(mcp.call_tool(tool, arguments))
    return json.loads(blocks[0].text)


def _token(**facts) -> str:
    return encode(ChatState(facts=facts))


def test_all_tools_registered():
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"assess", "optimize", "grill_next", "grade", "retrieve"}


def test_assess_tool_returns_verdicts_through_mcp():
    out = _call("assess", state_token=_token(citizen=True, has_dependents=True,
                                             household_income=2000))
    assert {e["program_id"] for e in out["eligible"]} >= {"str_household"}
    assert out["total_monthly_min"] >= 100


def test_grill_next_tool_through_mcp():
    out = _call("grill_next", state_token=encode(ChatState()))
    assert out["done"] is False and out["question"] is not None


def test_optimize_tool_through_mcp():
    out = _call("optimize", state_token=_token(citizen=True, age=30, is_oku=True,
                                              is_working=True, individual_income=500))
    assert "has_kad_oku" in {s["field"] for s in out["steps"]}


def test_grade_tool_through_mcp():
    out = _call("grade", text="Anda layak. Kami akan bantu.")
    assert "grade" in out and isinstance(out["readable"], bool)


def test_tampered_token_is_a_clean_error_not_a_verdict():
    """A bad token must never yield a usable result — it surfaces as an error."""
    try:
        blocks = asyncio.run(mcp.call_tool("assess", {"state_token": "not-a-real-token"}))
        text = " ".join(getattr(b, "text", "") for b in blocks).lower()
        assert "invalid" in text or "error" in text, f"expected an error, got: {text[:200]}"
        assert "eligible" not in text, "a tampered token must not return verdicts"
    except Exception as exc:  # FastMCP may raise instead of returning error content
        assert "invalid" in str(exc).lower()
