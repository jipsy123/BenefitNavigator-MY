"""Tests for the Foundry runtime credential.

This is the ONLY auth that must work BOTH locally (your `az login`) and inside the
deployed conductor container (the Container App's system-assigned managed identity).
`DefaultAzureCredential` covers both with one chain, so we pin that contract here —
and pin that `mas.orchestrate._project_client` is built with it — without ever making
a network call (constructing the credential does not authenticate).
"""
from __future__ import annotations

from ingest import config


def test_azure_credential_returns_default_credential():
    from azure.identity import DefaultAzureCredential

    cred = config.azure_credential()
    assert isinstance(cred, DefaultAzureCredential)


def test_project_client_is_built_with_azure_credential(monkeypatch):
    """_project_client must build AIProjectClient with config.azure_credential(),
    not a hard-coded AzureCliCredential — that is what makes the deployed container's
    managed identity work."""
    from mas import orchestrate

    captured: dict = {}

    class FakeAIProjectClient:
        def __init__(self, *, endpoint=None, credential=None):
            captured["endpoint"] = endpoint
            captured["credential"] = credential

    # _project_client does `from azure.ai.projects import AIProjectClient` at call
    # time, so patching the attribute on the module swaps the class it resolves.
    monkeypatch.setattr("azure.ai.projects.AIProjectClient", FakeAIProjectClient)
    sentinel = object()
    monkeypatch.setattr(config, "azure_credential", lambda: sentinel)

    orchestrate._project_client.cache_clear()
    try:
        orchestrate._project_client()
    finally:
        orchestrate._project_client.cache_clear()

    assert captured["endpoint"] == config.FOUNDRY_PROJECT_ENDPOINT
    assert captured["credential"] is sentinel
