"""Step B: Foundry IQ knowledge base (agentic retrieval) over the corpus index.

The knowledge base wraps the index with managed query-decomposition + reranking
and returns extractive (verbatim, cited) passages — not an LLM-synthesised answer —
so the downstream agent reasons over grounded content (cite-or-refuse).
"""
from __future__ import annotations

import json
import re

from . import config, sources
from .restclient import search_request

# ---------------------------------------------------------------------------
# Proof-passage retrieval helpers (Task 2: retrieval proves the verdict)
# ---------------------------------------------------------------------------

_PROOF_MAX_CHARS = 600
_NOISE_PREFIXES = ("JKM 100/03/1", "Garis Panduan Pengurusan", "Bantuan Kewangan Persekutuan")
_TOC_RE = re.compile(r"\d+\.\d+\.\s")          # "6.1. ", "6.2. " — the JKM table-of-contents
_TOC_MIN_ENTRIES = 3        # a chunk with >=3 "6.1."-style refs is a table-of-contents, not a clause


def _clean_passage(text: str, max_chars: int = _PROOF_MAX_CHARS) -> str:
    """Strip the page-header boilerplate + bare page numbers that the JKM PDF extraction
    leaves on their own lines, collapse whitespace, and cap length. FAQ passages pass through clean."""
    lines: list[str] = []
    for line in (text or "").split("\n"):
        stripped = line.strip()
        if not stripped or stripped.isdigit():
            continue
        if any(stripped.startswith(prefix) for prefix in _NOISE_PREFIXES):
            continue
        lines.append(stripped)
    collapsed = " ".join(" ".join(lines).split())
    return collapsed[:max_chars].rstrip()


def _odata_quote(value: str) -> str:
    """Escape a value for an OData string literal (doc names are constants, but be safe)."""
    return value.replace("'", "''")


def _search(body: dict) -> list[dict]:
    result = search_request("POST", f"indexes/{config.SEARCH_INDEX}/docs/search",
                            config.SEARCH_API_INDEX, body=body)
    return (result or {}).get("value", [])


def fetch_by_locator(doc_name: str, locator: str) -> dict | None:
    """Exact resolution: the chunk in `doc_name` whose locator equals `locator`
    (works for FAQ/akta docs whose locators survived extraction — e.g. S5, S2)."""
    hits = _search({
        "search": locator,
        "filter": f"doc_name eq '{_odata_quote(doc_name)}'",
        "select": "doc_name,doc_title,locator,source_url,content",
        "top": 10,
    })
    for hit in hits:
        if hit.get("locator") == locator:
            return hit
    return None


def fetch_by_topic(doc_name: str, query_hint: str) -> dict | None:
    """Doc-scoped semantic resolution for docs whose locators were mangled by extraction
    (the JKM guide). Skips the table-of-contents chunk so the eligibility clause wins."""
    hits = _search({
        "search": query_hint,
        "queryType": "semantic",
        "semanticConfiguration": config.SEMANTIC_CONFIG,
        "filter": f"doc_name eq '{_odata_quote(doc_name)}'",
        "vectorQueries": [{"kind": "text", "text": query_hint,
                           "fields": "content_vector", "k": 5}],
        "select": "doc_name,doc_title,locator,source_url,content",
        "top": 5,
    })
    for hit in hits:
        if len(_TOC_RE.findall(hit.get("content", ""))) >= _TOC_MIN_ENTRIES:  # a table-of-contents chunk
            continue
        return hit
    return None        # all hits were table-of-contents → no usable clause (caller fails hard)


def fetch_passage(citation: dict) -> dict | None:
    """The gazetted passage that proves one verdict: exact-locator first, doc-scoped
    semantic fallback otherwise. Returns the citation enriched with a cleaned `passage`,
    or None if the corpus yielded nothing usable for it. The citation should carry
    `name_ms` (as `proof_citations` provides) so the JKM doc-scoped fallback can build
    a meaningful query hint; without it the hint degrades to the mangled locator."""
    hit = fetch_by_locator(citation["doc_name"], citation["locator"])
    if hit is None:
        hint = f"{citation.get('name_ms') or citation['locator']} syarat kelayakan kadar bantuan"
        hit = fetch_by_topic(citation["doc_name"], hint)
    if hit is None:
        return None
    return {
        "doc_name": citation.get("doc_name"),
        "locator": citation.get("locator"),
        "doc_title": citation.get("doc_title"),
        "source_url": citation.get("source_url"),
        "passage": _clean_passage(hit.get("content", "")),
    }


def fetch_proofs(citations: list[dict]) -> list[dict]:
    """Fetch a proving passage for each citation (preserving order). A citation that
    yields nothing keeps an empty `passage`; the conductor's fail-hard check rejects the
    turn if any passage is empty. A transport error in `search_request` propagates."""
    out: list[dict] = []
    for citation in citations:
        proof = fetch_passage(citation)
        if proof is None:
            proof = {"doc_name": citation.get("doc_name"), "locator": citation.get("locator"),
                     "doc_title": citation.get("doc_title"),
                     "source_url": citation.get("source_url"), "passage": ""}
        out.append(proof)
    return out


# ---------------------------------------------------------------------------
# Knowledge base management (Foundry IQ)
# ---------------------------------------------------------------------------

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
