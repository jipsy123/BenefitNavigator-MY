"""Unit tests for the low-level Foundry agent stream parser (mas.orchestrate).

These prove _invoke_agent_stream translates raw Responses-API events into the internal
(kind, payload) protocol — in particular that it captures an MCP tool's OUTPUT, so the
conductor can use the deterministic `prove` result rather than the agent's prose.
"""
from __future__ import annotations

from types import SimpleNamespace

from mas import orchestrate


def _ev(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def test_invoke_agent_stream_captures_tool_output_from_done_event(monkeypatch):
    # Primary path: the per-item `done` event carries the populated McpCall.output.
    item_added = _ev("response.output_item.added",
                     item=SimpleNamespace(type="mcp_call", name="prove"))
    item_done = _ev("response.output_item.done",
                    item=SimpleNamespace(type="mcp_call", name="prove",
                                         output='{"proofs": [{"passage": "x"}]}'))
    completed = _ev("response.completed",
                    response=SimpleNamespace(output=[], output_text="hasil"))
    monkeypatch.setattr(orchestrate, "_open_agent_stream",
                        lambda _id, _p: [item_added, item_done, completed])

    events = list(orchestrate._invoke_agent_stream("retrieval", "prompt"))

    assert ("tool", "prove") in events
    assert ("tool_result", ("prove", '{"proofs": [{"passage": "x"}]}')) in events
    assert events[-1] == ("final", "hasil")
    assert events.index(("tool", "prove")) < events.index(
        ("tool_result", ("prove", '{"proofs": [{"passage": "x"}]}'))
    )


def test_invoke_agent_stream_captures_tool_output_from_final_response(monkeypatch):
    # Secondary path: the per-item `done` event has output=None, but the assembled final
    # response carries the populated McpCall. Capture must still fire (Task 0 outcome 2).
    item_done = _ev("response.output_item.done",
                    item=SimpleNamespace(type="mcp_call", name="prove", output=None))
    final_item = SimpleNamespace(type="mcp_call", name="prove",
                                 output='{"proofs": [{"passage": "y"}]}')
    completed = _ev("response.completed",
                    response=SimpleNamespace(output=[final_item], output_text="hasil"))
    monkeypatch.setattr(orchestrate, "_open_agent_stream",
                        lambda _id, _p: [item_done, completed])

    events = list(orchestrate._invoke_agent_stream("retrieval", "prompt"))

    assert ("tool_result", ("prove", '{"proofs": [{"passage": "y"}]}')) in events
    assert events[-1] == ("final", "hasil")


def test_parse_proofs_returns_list_on_valid_output():
    assert orchestrate._parse_proofs('{"proofs": [{"passage": "x"}]}') == [{"passage": "x"}]


def test_parse_proofs_allows_genuine_empty_results():
    # prove ran and found nothing relevant — a valid result, not a failure.
    assert orchestrate._parse_proofs('{"proofs": []}') == []


def test_parse_proofs_returns_none_on_failure_shapes():
    assert orchestrate._parse_proofs("") is None                       # tool errored / no output
    assert orchestrate._parse_proofs("not json") is None               # garbage
    assert orchestrate._parse_proofs('{"proofs": [], "error": "kb down"}') is None
    assert orchestrate._parse_proofs('{"nope": 1}') is None            # wrong shape
