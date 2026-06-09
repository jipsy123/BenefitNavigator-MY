"""Search index definition + idempotent (re)creation.

The index is 'knowledge-agent-shaped': it carries a semantic configuration and an
azureOpenAI vectorizer on the vector field, because the knowledge base embeds
query subqueries at runtime. The content field uses the Malay 'ms.microsoft'
analyzer for better Bahasa Malaysia tokenization.
"""
from __future__ import annotations

from . import config
from .restclient import search_request


def build_index_definition(aoai_key: str) -> dict:
    return {
        "name": config.SEARCH_INDEX,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "content", "type": "Edm.String", "searchable": True,
             "analyzer": "ms.microsoft"},
            {"name": "doc_name", "type": "Edm.String", "filterable": True, "facetable": True},
            {"name": "doc_title", "type": "Edm.String"},
            {"name": "locator", "type": "Edm.String", "searchable": True},
            {"name": "source_url", "type": "Edm.String"},
            {"name": "content_vector", "type": "Collection(Edm.Single)",
             "searchable": True, "retrievable": False,
             "dimensions": config.EMBED_DIM, "vectorSearchProfile": "vprofile"},
        ],
        "semantic": {
            "defaultConfiguration": config.SEMANTIC_CONFIG,
            "configurations": [{
                "name": config.SEMANTIC_CONFIG,
                "prioritizedFields": {
                    "titleField": {"fieldName": "doc_title"},
                    "prioritizedContentFields": [{"fieldName": "content"}],
                    "prioritizedKeywordsFields": [{"fieldName": "locator"}],
                },
            }],
        },
        "vectorSearch": {
            "algorithms": [{
                "name": "hnsw", "kind": "hnsw",
                "hnswParameters": {"metric": "cosine", "m": 4,
                                   "efConstruction": 400, "efSearch": 500},
            }],
            "profiles": [{"name": "vprofile", "algorithm": "hnsw", "vectorizer": "aoai"}],
            "vectorizers": [{
                "name": "aoai", "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": config.AOAI_VECTORIZER_URI,
                    "deploymentId": config.AOAI_EMBED_DEPLOYMENT,
                    "modelName": config.AOAI_EMBED_DEPLOYMENT,
                    "apiKey": aoai_key,
                },
            }],
        },
    }


def recreate_index() -> None:
    """Drop the index if present, then create it fresh (idempotent rebuild)."""
    try:
        search_request("DELETE", f"indexes/{config.SEARCH_INDEX}", config.SEARCH_API_INDEX)
    except RuntimeError as exc:
        if "404" not in str(exc):
            raise
    definition = build_index_definition(config.aoai_key())
    search_request("PUT", f"indexes/{config.SEARCH_INDEX}", config.SEARCH_API_INDEX,
                   body=definition)
