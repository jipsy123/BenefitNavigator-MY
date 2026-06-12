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
