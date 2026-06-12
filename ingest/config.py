"""Central configuration for the ingest pipeline.

API keys are fetched at runtime via the Azure CLI so they never touch the repo
or disk. Resource names are non-secret and live here as constants. See
benefitnav/infra/azure-resources.md for provenance.
"""
from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path

# --- Non-secret resource identifiers ---
RESOURCE_GROUP = "rg-benefitnav-my"
AOAI_ACCOUNT = "benefitnav-ai-sc-79c45"
SEARCH_SERVICE = "benefitnav-search-79c45"

# Endpoint used by the OpenAI SDK (proven host from the smoke test).
AOAI_ENDPOINT = f"https://{AOAI_ACCOUNT}.cognitiveservices.azure.com"
# resourceUri the AI Search vectorizer calls (canonical OpenAI host form).
AOAI_VECTORIZER_URI = f"https://{AOAI_ACCOUNT}.openai.azure.com"
AOAI_API_VERSION = "2024-10-21"
AOAI_EMBED_DEPLOYMENT = "text-embedding-3-large"
AOAI_CHAT_DEPLOYMENT = "gpt-4o"
EMBED_DIM = 3072

SEARCH_ENDPOINT = f"https://{SEARCH_SERVICE}.search.windows.net"
SEARCH_INDEX = "benefitnav-corpus"
SEMANTIC_CONFIG = "sem"
KNOWLEDGE_SOURCE = "benefitnav-ks"
KNOWLEDGE_BASE = "benefitnav-kb"

# --- Foundry Agent Service (multi-agent A2A layer, mas/) ---
FOUNDRY_PROJECT = "benefitnav-proj"
FOUNDRY_PROJECT_ENDPOINT = (
    f"https://{AOAI_ACCOUNT}.services.ai.azure.com/api/projects/{FOUNDRY_PROJECT}"
)
# Scopes for `az account get-access-token` when calling Foundry data-plane / ARM.
FOUNDRY_DATAPLANE_SCOPE = "https://ai.azure.com/.default"
ARM_SCOPE = "https://management.azure.com/.default"

# Search REST api-versions: index/docs are GA; knowledge source/base are preview.
SEARCH_API_INDEX = "2024-07-01"
SEARCH_API_KS = "2026-04-01"
SEARCH_API_KB = "2026-05-01-preview"

# --- Trust-core MCP server (Azure Container Apps, mas/mcp_server.py) ---
# The HMAC secret that signs/verifies the /chat state token lives here as a Container
# Apps secret. The signer (the FastAPI orchestrator) and the verifier (this container's
# grill/assess tools) must use the identical key, so the local orchestrator fetches the
# same value the container holds — see token_secret() below.
MCP_CONTAINER_APP = "benefitnav-mcp"
MCP_TOKEN_SECRET_NAME = "token-secret"

# --- Corpus location (extracted, machine-readable text) ---
# The index build reads pre-extracted corpus text from the separate corpus-fetcher
# tool. Override with the CORPUS_TEXT_DIR env var when that tool is not a sibling of
# this repo. Not needed at runtime/demo — the index is already built on Azure Search.
CORPUS_TEXT_DIR = Path(
    os.environ.get(
        "CORPUS_TEXT_DIR",
        str(Path(__file__).resolve().parents[2] / "corpus-fetcher" / "corpus" / "text"),
    )
)


def _az(*args: str) -> str:
    """Run an `az` command, returning stripped stdout or raising with context."""
    try:
        result = subprocess.run(
            ["az", *args], capture_output=True, text=True, check=True
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Azure CLI ('az') not found on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"`az {' '.join(args)}` failed:\n{exc.stderr.strip()}"
        ) from exc
    value = result.stdout.strip()
    if not value:
        raise RuntimeError(f"`az {' '.join(args)}` returned no output.")
    return value


@functools.lru_cache(maxsize=1)
def aoai_key() -> str:
    """Azure OpenAI admin key (cached for the process lifetime).

    Prefers the BENEFITNAV_AOAI_KEY env var (set in hosted/container deploys where
    the Azure CLI is unavailable); otherwise fetched via `az` — the local default.
    Either way no key is committed to the repo.
    """
    env = os.environ.get("BENEFITNAV_AOAI_KEY")
    if env:
        return env
    return _az(
        "cognitiveservices", "account", "keys", "list",
        "-g", RESOURCE_GROUP, "-n", AOAI_ACCOUNT,
        "--query", "key1", "-o", "tsv",
    )


@functools.lru_cache(maxsize=1)
def search_key() -> str:
    """Azure AI Search primary admin key (cached for the process lifetime).

    Prefers the BENEFITNAV_SEARCH_KEY env var (set in hosted/container deploys
    where the Azure CLI is unavailable); otherwise fetched via `az` — the local
    default. Either way no key is committed to the repo.
    """
    env = os.environ.get("BENEFITNAV_SEARCH_KEY")
    if env:
        return env
    return _az(
        "search", "admin-key", "show",
        "-g", RESOURCE_GROUP, "--service-name", SEARCH_SERVICE,
        "--query", "primaryKey", "-o", "tsv",
    )


@functools.lru_cache(maxsize=1)
def token_secret() -> str:
    """HMAC secret for the /chat conversation state token (cached for the process).

    The FastAPI orchestrator *signs* the token and the Foundry MCP container *verifies*
    it, so both must use the identical secret or every grill/assess tool call fails the
    signature check. Prefers the BENEFITNAV_TOKEN_SECRET env var — that is how the
    container (which has no Azure CLI) receives it; locally the var is unset, so the
    same value is fetched from the container's Container Apps secret via `az`. Either
    way the secret is never committed to the repo.
    """
    env = os.environ.get("BENEFITNAV_TOKEN_SECRET")
    if env:
        return env
    return _az(
        "containerapp", "secret", "show",
        "-g", RESOURCE_GROUP, "-n", MCP_CONTAINER_APP,
        "--secret-name", MCP_TOKEN_SECRET_NAME,
        "--query", "value", "-o", "tsv",
    )


def azure_credential():
    """Azure credential for Foundry Agent Service (data-plane) calls.

    `DefaultAzureCredential` works in BOTH environments with no code change:
      - locally it picks up your `az login` session (Azure CLI is in its chain);
      - in the deployed conductor container it picks up the Container App's
        system-assigned managed identity.
    Imported lazily so the deterministic trust core (compute/) never needs the
    azure-identity SDK merely to import this module.
    """
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()
