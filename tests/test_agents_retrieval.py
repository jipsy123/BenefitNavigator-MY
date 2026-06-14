"""The Retrieval agent must own the `prove` tool (verdict-driven), not `retrieve`."""
from mas import agents


def test_retrieval_agent_uses_prove_tool():
    tools = {t.name for t in agents.RETRIEVAL.tools}
    assert tools == {"prove"}


def test_no_agent_references_the_removed_retrieve_tool():
    for spec in agents.AGENTS:
        assert "retrieve" not in {t.name for t in spec.tools}
