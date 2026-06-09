"""Step A build: chunk -> embed -> (re)create index -> upload.

Idempotent: the index is dropped and recreated on every run, so re-chunking is safe.
Run:  python -m ingest.build
"""
from __future__ import annotations

from openai import AzureOpenAI

from . import config, sources
from .chunker import chunk_document
from .index_schema import recreate_index
from .restclient import search_request


def load_text(doc_name: str) -> str:
    path = config.CORPUS_TEXT_DIR / f"{doc_name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Missing extracted text: {path}")
    return path.read_text(encoding="utf-8")


def collect_chunks() -> list[dict]:
    """Chunk all documents into citation-first records (no vectors yet)."""
    records: list[dict] = []
    for doc_name, meta in sources.DOCS.items():
        chunks = chunk_document(doc_name, meta["doc_type"], load_text(doc_name))
        for seq, chunk in enumerate(chunks):
            records.append({
                "id": f"{doc_name}-{seq}",
                "doc_name": doc_name,
                "doc_title": meta["title"],
                "source_url": meta["source_url"],
                "locator": chunk.locator,
                "content": chunk.content,
            })
    return records


def embed_records(client: AzureOpenAI, records: list[dict], batch: int = 32) -> None:
    """Attach a content_vector to each record, embedding in batches."""
    for start in range(0, len(records), batch):
        window = records[start:start + batch]
        response = client.embeddings.create(
            model=config.AOAI_EMBED_DEPLOYMENT,
            input=[record["content"] for record in window],
        )
        for record, item in zip(window, response.data):
            record["content_vector"] = item.embedding


def upload_records(records: list[dict], batch: int = 100) -> None:
    for start in range(0, len(records), batch):
        window = records[start:start + batch]
        payload = {"value": [{"@search.action": "mergeOrUpload", **record}
                             for record in window]}
        search_request("POST", f"indexes/{config.SEARCH_INDEX}/docs/index",
                       config.SEARCH_API_INDEX, body=payload)


def main() -> None:
    records = collect_chunks()
    print(f"Chunks: {len(records)}")
    counts: dict[str, int] = {}
    for record in records:
        counts[record["doc_name"]] = counts.get(record["doc_name"], 0) + 1
    for doc_name, count in counts.items():
        print(f"  {doc_name}: {count}")

    client = AzureOpenAI(
        api_key=config.aoai_key(),
        api_version=config.AOAI_API_VERSION,
        azure_endpoint=config.AOAI_ENDPOINT,
    )
    embed_records(client, records)
    print(f"Embedded {len(records)} chunks ({config.EMBED_DIM}-dim).")

    recreate_index()
    print(f"Index '{config.SEARCH_INDEX}' recreated.")

    upload_records(records)
    print(f"Uploaded {len(records)} documents.")


if __name__ == "__main__":
    main()
