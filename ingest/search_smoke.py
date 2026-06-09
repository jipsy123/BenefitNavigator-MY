"""Step A smoke test: a Malay eligibility query must return a cited chunk.

Exercises the full retrieval path WITHOUT the knowledge base:
  hybrid (BM25 + vector-via-vectorizer) + semantic rerank + citation fields.
Asserts the JKM rulebook appears in the top results for an OKU-allowance query —
the Day-1 milestone the whole build hinges on.

Run:  python -m ingest.search_smoke
"""
from __future__ import annotations

from . import config
from .restclient import search_request

QUERY = "Saya OKU dan tidak boleh bekerja. Bantuan kewangan bulanan apa yang saya layak?"
EXPECT_DOC = "jkm_garis_panduan_2018"


def run() -> None:
    body = {
        "search": QUERY,
        "queryType": "semantic",
        "semanticConfiguration": config.SEMANTIC_CONFIG,
        "vectorQueries": [{"kind": "text", "text": QUERY,
                           "fields": "content_vector", "k": 5}],
        "select": "doc_name,doc_title,locator,source_url,content",
        "top": 5,
    }
    result = search_request("POST", f"indexes/{config.SEARCH_INDEX}/docs/search",
                            config.SEARCH_API_INDEX, body=body)
    hits = (result or {}).get("value", [])
    print(f'Query: "{QUERY}"')
    print(f"Results: {len(hits)}\n")
    for rank, hit in enumerate(hits, 1):
        score = hit.get("@search.rerankerScore") or hit.get("@search.score") or 0.0
        snippet = " ".join(hit["content"][:220].split())
        print(f"#{rank} [{hit['doc_name']} | {hit['locator']}] score={score:.3f}")
        print(f"    {snippet}")
        print(f"    source: {hit['source_url']}\n")

    if not hits:
        raise SystemExit("FAIL: no results returned.")
    top_docs = [hit["doc_name"] for hit in hits[:3]]
    if EXPECT_DOC not in top_docs:
        raise SystemExit(f"FAIL: expected '{EXPECT_DOC}' in top 3, got {top_docs}")
    print(f"PASS: cited Malay retrieval works; '{EXPECT_DOC}' is in the top 3.")


if __name__ == "__main__":
    run()
