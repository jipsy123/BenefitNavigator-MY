"""Unit tests for the proof-passage retrieval helpers (no Azure — search is mocked).

These pin: (a) noise stripping for the messy JKM PDF text, (b) exact-locator resolution
for FAQ-style docs, and (c) the doc-scoped semantic fallback when the locator is mangled.
"""
from __future__ import annotations

from ingest import knowledge_base as kb


def test_clean_passage_strips_page_headers_and_collapses_whitespace():
    raw = ("SYARAT KELAYAKAN \n 1. Warganegara;  3. Pemegang Kad OKU JKM; \n"
           "JKM 100/03/1/ JLD.4 ( 20 ) \n Garis Panduan Pengurusan \n 8 \n"
           "4. Pendapatan bulanan RM 1,200.00 dan ke bawah;")
    out = kb._clean_passage(raw)
    assert "JKM 100/03/1" not in out and "Garis Panduan Pengurusan" not in out
    assert "\n" not in out and "  " not in out                 # collapsed
    assert "Pemegang Kad OKU JKM" in out and "RM 1,200.00" in out


def test_fetch_passage_uses_exact_locator_when_present(monkeypatch):
    cit = {"doc_name": "lhdn_sara_faq_2026", "locator": "S2",
           "doc_title": "SARA FAQ", "source_url": "https://x.gov.my", "name_ms": "SARA"}

    def fake_search(method, path, api, body=None, timeout=90):
        return {"value": [
            {"locator": "S1", "content": "wrong section"},
            {"locator": "S2", "content": "Kelayakan SARA 2026 adalah terhad..."},
        ]}

    monkeypatch.setattr(kb, "search_request", fake_search)
    out = kb.fetch_passage(cit)
    assert out["passage"].startswith("Kelayakan SARA 2026")
    assert out["doc_name"] == "lhdn_sara_faq_2026" and out["locator"] == "S2"


def test_fetch_passage_falls_back_to_topic_and_skips_toc(monkeypatch):
    cit = {"doc_name": "jkm_garis_panduan_2018",
           "locator": "6.3. BANTUAN OKU TIDAK BERUPAYA BEKERJA (BTB)",
           "doc_title": "JKM", "source_url": "https://x.gov.my",
           "name_ms": "Bantuan OKU Tidak Berupaya Bekerja (BTB)"}
    calls = {"n": 0}

    def fake_search(method, path, api, body=None, timeout=90):
        calls["n"] += 1
        if calls["n"] == 1:                                    # exact-locator attempt: no match
            return {"value": [{"locator": "SYARAT KELAYAKAN", "content": "x"}]}
        return {"value": [                                     # topic attempt: TOC then the clause
            {"locator": "6. KRITERIA", "content": "6.1. BKK; 6.2. EPC; 6.3. BTB; 6.4. BPT;"},
            {"locator": "SYARAT KELAYAKAN",
             "content": "1. Pemegang Kad OKU JKM; 4. Tidak berupaya bekerja."},
        ]}

    monkeypatch.setattr(kb, "search_request", fake_search)
    out = kb.fetch_passage(cit)
    assert "Tidak berupaya bekerja" in out["passage"]          # the clause, not the TOC
    assert "6.1." not in out["passage"]


def test_fetch_passage_returns_none_when_nothing_found(monkeypatch):
    cit = {"doc_name": "jkm_garis_panduan_2018", "locator": "X. NONEXISTENT",
           "doc_title": "JKM", "source_url": "https://x.gov.my", "name_ms": "Nope"}

    def fake_search(method, path, api, body=None, timeout=90):
        # exact-locator attempt: no matching locator; topic attempt: only a TOC chunk
        if "queryType" in (body or {}):
            return {"value": [{"locator": "6. KRITERIA",
                               "content": "6.1. BKK; 6.2. EPC; 6.3. BTB;"}]}
        return {"value": [{"locator": "SYARAT KELAYAKAN", "content": "irrelevant"}]}

    monkeypatch.setattr(kb, "search_request", fake_search)
    assert kb.fetch_passage(cit) is None


def test_fetch_proofs_marks_a_miss_with_empty_passage(monkeypatch):
    hit_cit = {"doc_name": "lhdn_sara_faq_2026", "locator": "S2",
               "doc_title": "SARA", "source_url": "https://x.gov.my", "name_ms": "SARA"}
    miss_cit = {"doc_name": "jkm_garis_panduan_2018", "locator": "X. NONE",
                "doc_title": "JKM", "source_url": "https://x.gov.my", "name_ms": "Nope"}

    def fake_search(method, path, api, body=None, timeout=90):
        if "queryType" in (body or {}):
            return {"value": [{"locator": "6. KRITERIA", "content": "6.1. ; 6.2. ; 6.3. ;"}]}
        # exact-locator attempt: only S2 matches its own locator
        return {"value": [{"locator": "S2", "content": "Kelayakan SARA 2026 adalah terhad..."}]}

    monkeypatch.setattr(kb, "search_request", fake_search)
    out = kb.fetch_proofs([hit_cit, miss_cit])
    assert len(out) == 2
    assert out[0]["passage"].startswith("Kelayakan SARA 2026")
    assert out[1]["passage"] == "" and out[1]["doc_name"] == "jkm_garis_panduan_2018"
