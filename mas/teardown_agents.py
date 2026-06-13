"""Delete the five Foundry agents from the project (data-plane).

This is OPTIONAL for a full teardown: infra/teardown.sh removes the whole resource
group, which destroys the Cognitive Services account → the Foundry project → every
agent and connection inside it. Use this only to clear the agents WITHOUT tearing down
the project (e.g. to re-provision them fresh):

    PYTHONPATH="$PWD" .venv/bin/python -m mas.teardown_agents

Best-effort and idempotent: an already-absent agent is reported, not fatal.
"""
from __future__ import annotations

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential

from ingest import config

from . import agents as A


def main() -> None:
    client = AIProjectClient(endpoint=config.FOUNDRY_PROJECT_ENDPOINT,
                             credential=AzureCliCredential())
    print(f"Deleting {len(A.AGENTS)} agents from {config.FOUNDRY_PROJECT} ...")
    for spec in A.AGENTS:
        try:
            client.agents.delete(spec.id, force=True)        # force: remove all versions
            print(f"  ✓ deleted {spec.id}")
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            print(f"  – {spec.id}: {str(exc)[:140]}")
    print("Done. (Connections, if any, are removed with the project on full teardown.)")


if __name__ == "__main__":
    main()
