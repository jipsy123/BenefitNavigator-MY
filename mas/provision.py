"""Provision the six Foundry agents + their MCP tools (idempotent).

Run (with the trust-core MCP server reachable at BENEFITNAV_MCP_URL — the Azure
Container Apps endpoint + /mcp):

    BENEFITNAV_MCP_URL="https://<aca-host>/mcp" \\
    PYTHONPATH="$PWD" .venv/bin/python -m mas.provision

What it does, per mas/agents.AGENTS:
  - Specialists: create_version with a PromptAgentDefinition carrying that agent's MCP
    tool (allowed_tools scoped to just its functions).
  - Orchestrator: create_version as a TOOL-LESS router. It decides the action; FastAPI
    executes the chosen specialist directly via the Responses API (mas/orchestrate).

Why no A2A wiring. Same-project Foundry→Foundry A2A delegation is an open platform bug
(the agent card-path validation rejects every form — GitHub azure-sdk-for-python
#47419). So we do NOT publish A2A endpoints or create RemoteA2A connections; the
delegation hop lives in FastAPI instead. The vertical slice (agent → MCP → live
container → trust core) is proven working via direct invocation.

Auth is az-CLI based (AzureCliCredential); no secrets. The deterministic verdict logic
is untouched — these agents only route and narrate, and call the MCP tools that call
compute/.
"""
from __future__ import annotations

import os

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition
from azure.identity import AzureCliCredential

from ingest import config

from . import agents as A

_BASE = config.FOUNDRY_PROJECT_ENDPOINT  # https://{acct}.services.ai.azure.com/api/projects/{proj}
_MCP_LABEL = "benefitnav-trust-core"


def _client() -> AIProjectClient:
    return AIProjectClient(endpoint=_BASE, credential=AzureCliCredential())


def _mcp_tool(spec: A.AgentSpec, mcp_url: str) -> MCPTool | None:
    """One MCP tool per agent, scoped (allowed_tools) to just its functions."""
    fns = [t.name for t in spec.tools if t.kind == A.TOOL_MCP]
    if not fns:
        return None
    return MCPTool(server_label=_MCP_LABEL, server_url=mcp_url,
                   allowed_tools=fns, require_approval="never")


def _provision(client: AIProjectClient, spec: A.AgentSpec, mcp_url: str) -> None:
    """Create (or re-version) one agent with its MCP tool (if any)."""
    tool = _mcp_tool(spec, mcp_url)
    definition = PromptAgentDefinition(
        model=spec.model, instructions=spec.instructions,
        tools=[tool] if tool else [], temperature=0.0)
    client.agents.create_version(agent_name=spec.id, definition=definition,
                                 description=spec.display_name)
    print(f"  ✓ {spec.id:13} (tools={[t.name for t in spec.tools] or '—'})")


def main() -> None:
    mcp_url = os.environ.get("BENEFITNAV_MCP_URL")
    if not mcp_url:
        raise SystemExit("Set BENEFITNAV_MCP_URL to the public MCP endpoint "
                         "(the Azure Container Apps URL + /mcp) before provisioning.")
    client = _client()
    print(f"Provisioning into {_BASE}\n  MCP server_url: {mcp_url}")
    print("Specialists:")
    for spec in A.SPECIALISTS:
        _provision(client, spec, mcp_url)
    print("Orchestrator (tool-less router):")
    _provision(client, A.ORCHESTRATOR, mcp_url)
    print(f"\nDone. {len(A.AGENTS)} agents provisioned. Orchestrator entry: "
          f"agent_reference name='{A.ORCHESTRATOR.id}'.")


if __name__ == "__main__":
    main()
