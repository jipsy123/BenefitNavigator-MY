"""Step B: Foundry IQ knowledge base (agentic retrieval) over the corpus index.

The knowledge base wraps the index with managed query-decomposition + reranking
and returns extractive (verbatim, cited) passages — not an LLM-synthesised answer —
so the downstream agent reasons over grounded content (cite-or-refuse).
"""
from __future__ import annotations

import json

from . import config, sources
from .restclient import search_request

# Docs disagree on the path spelling across preview versions; try both.
_KS_PATHS = ("knowledgesources", "knowledge-sources")

# Fields the knowledge source returns for each retrieved chunk (citation-first).
SOURCE_FIELDS = ["id", "doc_name", "doc_title", "locator", "source_url", "content"]


def _put_with_fallback(paths: tuple[str, ...], name: str, api_version: str,
                       body: dict) -> dict | None:
    last_error: Exception | None = None
    for path in paths:
        try:
            return search_request("PUT", f"{path}/{name}", api_version, body=body)
        except RuntimeError as exc:
            last_error = exc
    raise last_error  # type: ignore[misc]


def create_knowledge_source() -> None:
    body = {
        "name": config.KNOWLEDGE_SOURCE,
        "description": "BenefitNavigator Malaysia corpus (JKM, PERKESO, LHDN, Akta OKU).",
        "kind": "searchIndex",
        "searchIndexParameters": {
            "searchIndexName": config.SEARCH_INDEX,
            "sourceDataFields": [{"name": field} for field in SOURCE_FIELDS],
        },
    }
    _put_with_fallback(_KS_PATHS, config.KNOWLEDGE_SOURCE, config.SEARCH_API_KS, body)


def create_knowledge_base() -> None:
    # The KB needs an LLM (gpt-4o) to do agentic query planning for any reasoning
    # effort above 'minimal'. retrievalReasoningEffort is set per retrieve request.
    body = {
        "name": config.KNOWLEDGE_BASE,
        "description": "Agentic retrieval over the Malaysian benefits corpus.",
        "knowledgeSources": [{"name": config.KNOWLEDGE_SOURCE}],
        "models": [{
            "kind": "azureOpenAI",
            "azureOpenAIParameters": {
                "resourceUri": config.AOAI_VECTORIZER_URI,
                "deploymentId": config.AOAI_CHAT_DEPLOYMENT,
                "modelName": config.AOAI_CHAT_DEPLOYMENT,
                "apiKey": config.aoai_key(),
            },
        }],
        "encryptionKey": None,
    }
    search_request("PUT", f"knowledgebases/{config.KNOWLEDGE_BASE}",
                   config.SEARCH_API_KS, body=body)


def setup() -> None:
    create_knowledge_source()
    create_knowledge_base()


def retrieve(query: str, output_mode: str = "extractiveData",
             reasoning: str = "low") -> dict:
    body = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": query}]}],
        "outputMode": output_mode,
        "retrievalReasoningEffort": {"kind": reasoning},
    }
    result = search_request("POST", f"knowledgebases/{config.KNOWLEDGE_BASE}/retrieve",
                            config.SEARCH_API_KB, body=body)
    if result is None:
        raise RuntimeError("Empty retrieve response.")
    return result


def extract_passages(result: dict) -> list[dict]:
    """Flatten a retrieve() response into cited passages.

    The extractive payload lives in response[].content[].text as a JSON array of
    {ref_id, title, terms, content}. We resolve each ref_id to its citation via
    references[].docKey -> doc_name -> official source_url.
    """
    items: list[dict] = []
    for message in result.get("response", []):
        for part in message.get("content", []):
            if part.get("type") == "text":
                try:
                    items.extend(json.loads(part["text"]))
                except (ValueError, KeyError):
                    continue

    refs = {str(ref.get("id")): ref for ref in result.get("references", [])}
    passages: list[dict] = []
    for item in items:
        ref = refs.get(str(item.get("ref_id")), {})
        doc_key = ref.get("docKey", "")
        doc_name = doc_key.rsplit("-", 1)[0] if doc_key else ""
        meta = sources.DOCS.get(doc_name, {})
        passages.append({
            "ref_id": item.get("ref_id"),
            "doc_name": doc_name,
            "doc_title": item.get("title") or meta.get("title"),
            "locator": item.get("terms"),
            "content": item.get("content"),
            "source_url": meta.get("source_url"),
            "reranker_score": ref.get("rerankerScore"),
        })
    return passages


def retrieve_passages(query: str, reasoning: str = "low") -> list[dict]:
    """Convenience: agentic retrieve + flatten into cited passages."""
    return extract_passages(retrieve(query, reasoning=reasoning))
