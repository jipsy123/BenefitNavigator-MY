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

# Search REST api-versions: index/docs are GA; knowledge source/base are preview.
SEARCH_API_INDEX = "2024-07-01"
SEARCH_API_KS = "2026-04-01"
SEARCH_API_KB = "2026-05-01-preview"

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
    """Azure OpenAI admin key (cached for the process lifetime)."""
    return _az(
        "cognitiveservices", "account", "keys", "list",
        "-g", RESOURCE_GROUP, "-n", AOAI_ACCOUNT,
        "--query", "key1", "-o", "tsv",
    )


@functools.lru_cache(maxsize=1)
def search_key() -> str:
    """Azure AI Search primary admin key (cached for the process lifetime)."""
    return _az(
        "search", "admin-key", "show",
        "-g", RESOURCE_GROUP, "--service-name", SEARCH_SERVICE,
        "--query", "primaryKey", "-o", "tsv",
    )
