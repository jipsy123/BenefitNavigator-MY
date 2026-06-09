"""Structure-aware chunking with citation-first locators.

Each document type uses the most specific anchor that survived PDF->text
extraction (verified: page markers did NOT survive, so we never cite a page):
  faq   -> one chunk per "N. S:" question  -> locator "S{n}"
  akta  -> one chunk per "N." section      -> locator "Seksyen {n}"
  guide -> heading-aware paragraph packing -> locator = nearest heading / ordinal
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CHARS = 2000
MIN_CHARS = 120
OVERLAP = 150


@dataclass(frozen=True)
class Chunk:
    doc_name: str
    locator: str
    content: str


def _clean(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_oversize(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    """Split a block longer than max_chars on paragraph/sentence boundaries."""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            window = text[start:end]
            brk = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
            if brk > max_chars * 0.5:
                end = start + brk + 1
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def _chunk_numbered(doc_name: str, text: str, pattern: re.Pattern, label) -> list[Chunk]:
    """Generic splitter for numbered structures (FAQ questions, Akta sections)."""
    text = _clean(text)
    matches = list(pattern.finditer(text))
    if not matches:
        return chunk_guide(doc_name, text)
    chunks: list[Chunk] = []
    for i, match in enumerate(matches):
        number = match.group(1)
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        for part, piece in enumerate(_split_oversize(body)):
            locator = label(number) if part == 0 else f"{label(number)} (samb.)"
            if part == 0 or len(piece) >= MIN_CHARS:
                chunks.append(Chunk(doc_name, locator, piece))
    return chunks


def chunk_faq(doc_name: str, text: str) -> list[Chunk]:
    pattern = re.compile(r"(?m)^\s*(\d+)\.\s*S\s*:")
    return _chunk_numbered(doc_name, text, pattern, lambda n: f"S{n}")


def chunk_akta(doc_name: str, text: str) -> list[Chunk]:
    pattern = re.compile(r"(?m)^\s*(\d+)\.\s")
    return _chunk_numbered(doc_name, text, pattern, lambda n: f"Seksyen {n}")


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not 0 < len(stripped) <= 80:
        return False
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) < 3:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) > 0.7


def chunk_guide(doc_name: str, text: str) -> list[Chunk]:
    text = _clean(text)
    chunks: list[Chunk] = []
    buffer: list[str] = []
    current_heading = "Pengenalan"

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        body = "\n".join(buffer).strip()
        for part, piece in enumerate(_split_oversize(body)):
            locator = current_heading if part == 0 else f"{current_heading} (samb.)"
            if len(piece) >= MIN_CHARS:
                chunks.append(Chunk(doc_name, locator[:90], piece))
        buffer = []

    buffer_len = 0
    for line in text.split("\n"):
        if _looks_like_heading(line):
            flush()
            current_heading = line.strip()
            buffer_len = 0
            continue
        buffer.append(line)
        buffer_len += len(line) + 1
        if buffer_len >= MAX_CHARS:
            flush()
            buffer_len = 0
    flush()

    if not chunks:  # fallback: pure size split
        chunks = [
            Chunk(doc_name, f"bahagian {i + 1}", piece)
            for i, piece in enumerate(_split_oversize(text))
        ]
    return chunks


_STRATEGIES = {"faq": chunk_faq, "akta": chunk_akta, "guide": chunk_guide}


def chunk_document(doc_name: str, doc_type: str, text: str) -> list[Chunk]:
    return _STRATEGIES.get(doc_type, chunk_guide)(doc_name, text)
