"""Step B assertion test: agentic retrieval returns cited, citation-resolved passages.

Run:  python -m ingest.kb_smoke
"""
from __future__ import annotations

from . import knowledge_base as kb

QUERY = "Saya OKU dan tidak boleh bekerja. Bantuan kewangan bulanan apa yang saya layak?"
EXPECT_DOC = "jkm_garis_panduan_2018"


def run() -> None:
    print("Creating knowledge source + knowledge base ...")
    kb.setup()
    print("Agentic retrieve ('low' reasoning, extractive) ...\n")
    passages = kb.retrieve_passages(QUERY, reasoning="low")

    print(f'Query: "{QUERY}"')
    print(f"Passages: {len(passages)}\n")
    for i, p in enumerate(passages[:5], 1):
        score = p.get("reranker_score") or 0.0
        snippet = " ".join((p["content"] or "")[:160].split())
        print(f"#{i} [{p['doc_name']} | {p['locator']}] score={score:.3f}")
        print(f"    {snippet}")
        print(f"    cite: {p['source_url']}\n")

    if not passages:
        raise SystemExit("FAIL: no passages returned.")
    if not any(p["doc_name"] == EXPECT_DOC for p in passages):
        raise SystemExit(f"FAIL: expected '{EXPECT_DOC}' among passages.")
    if not all(p["source_url"] for p in passages[:3]):
        raise SystemExit("FAIL: top passages missing resolved citations.")
    print(f"PASS: agentic retrieval returns cited passages; '{EXPECT_DOC}' present.")


if __name__ == "__main__":
    run()
