"""Unit tests for agent.localize — the verified-output translation layer.

Azure Translator is mocked; these verify the *plumbing and trust logic* (correct
field mapping, immutability, all-or-nothing fallback, and the RM-amount safety
gate). Real cross-language amount survival is covered by test_localize_live.py.
"""
from __future__ import annotations

import copy

import pytest

from agent import localize, translate


def _sample_result() -> dict:
    """A result shaped like dataclasses.asdict(PipelineResult)."""
    return {
        "ok": True,
        "refused": False,
        "message_ms": "Anda layak menerima RM400 sebulan daripada JKM.",
        "profile": {"age": 35, "is_oku": True},
        "assumptions_ms": ["Andaian: anda warganegara Malaysia."],
        "eligible": [{
            "program_id": "jkm_epc", "name_ms": "Elaun Pekerja OKU", "agency": "JKM",
            "eligible": True,
            "amount": {"type": "fixed", "monthly_myr": 400},
            "citation": {"doc_title": "Garis Panduan JKM 2018", "locator": "6.1",
                         "source_url": "https://example.gov.my/jkm"},
        }],
        "gaps": [{
            "program_id": "sara", "name_ms": "Bantuan Sara Hidup", "agency": "LHDN",
            "amount": {"type": "range", "monthly_myr_min": 250, "monthly_myr_max": 350,
                       "note_ms": "Jumlah bergantung pada kategori."},
            "near_miss": True,
            "blocking_ms": ["Belum diluluskan STR."],
            "actions_ms": ["Mohon STR di portal MyHASiL."],
            "citation": {"doc_title": "STR 2024", "locator": "3.2",
                         "source_url": "https://example.gov.my/str"},
        }],
        "total_monthly_min": 400,
        "citations": [{"doc_title": "Garis Panduan JKM 2018", "locator": "6.1",
                       "source_url": "https://example.gov.my/jkm"}],
        "groundedness": {"available": True, "grounded": True},
        "stages": [{"name": "SHIELD", "status": "ok", "summary": "Tiada serangan dikesan.",
                    "data": None}],
    }


def _tag_batch(prefix: str):
    """Fake translate_batch that tags each string — preserves RM amounts."""
    def fake(texts, to_lang, from_lang="ms"):
        return [f"{prefix}{t}" for t in texts], True
    return fake


def test_localize_ms_is_noop():
    result = _sample_result()
    display, ok = localize.localize_assess(result, "ms")
    assert ok is True
    assert display is result  # untouched, same object


def test_unsupported_lang_is_noop():
    result = _sample_result()
    display, ok = localize.localize_assess(result, "fr")
    assert ok is True
    assert display is result


def test_round_trip_maps_correct_fields(monkeypatch):
    monkeypatch.setattr(translate, "translate_batch", _tag_batch("EN::"))
    result = _sample_result()
    display, ok = localize.localize_assess(result, "en")

    assert ok is True
    assert display["lang"] == "en"
    # Every display string is translated...
    assert display["message_ms"] == "EN::Anda layak menerima RM400 sebulan daripada JKM."
    assert display["assumptions_ms"][0].startswith("EN::")
    assert display["eligible"][0]["name_ms"] == "EN::Elaun Pekerja OKU"
    assert display["gaps"][0]["name_ms"] == "EN::Bantuan Sara Hidup"
    assert display["gaps"][0]["amount"]["note_ms"].startswith("EN::")
    assert display["gaps"][0]["blocking_ms"][0].startswith("EN::")
    assert display["gaps"][0]["actions_ms"][0].startswith("EN::")
    assert display["stages"][0]["summary"].startswith("EN::")
    # ...but citations (official titles) and structured numbers are NOT touched.
    assert display["citations"][0]["doc_title"] == "Garis Panduan JKM 2018"
    assert display["eligible"][0]["amount"]["monthly_myr"] == 400
    assert display["total_monthly_min"] == 400


def test_input_is_never_mutated(monkeypatch):
    monkeypatch.setattr(translate, "translate_batch", _tag_batch("X::"))
    result = _sample_result()
    before = copy.deepcopy(result)
    localize.localize_assess(result, "en")
    assert result == before  # pure transform — original intact


def test_transport_failure_falls_back_to_malay(monkeypatch):
    monkeypatch.setattr(translate, "translate_batch",
                        lambda texts, to_lang, from_lang="ms": (list(texts), False))
    result = _sample_result()
    display, ok = localize.localize_assess(result, "en")
    assert ok is False
    assert display is result          # canonical Malay returned whole
    assert "lang" not in display or display.get("lang") != "en"


def test_invented_amount_is_rejected(monkeypatch):
    """If MT ever emits an RM amount absent from the source, reject the whole
    localization (no silent per-field mix) and fall back to Malay."""
    def fabricate(texts, to_lang, from_lang="ms"):
        out = list(texts)
        out[0] = out[0] + " Anda juga dapat RM9000 bonus."  # invented amount
        return out, True
    monkeypatch.setattr(translate, "translate_batch", fabricate)
    result = _sample_result()
    display, ok = localize.localize_assess(result, "en")
    assert ok is False
    assert display is result  # untranslated Malay, not the fabricated text


def test_dropping_an_amount_is_allowed(monkeypatch):
    """Translation may legitimately drop/keep amounts — only inventing is unsafe."""
    def drop(texts, to_lang, from_lang="ms"):
        return [t.replace("RM400", "the allowance") for t in texts], True
    monkeypatch.setattr(translate, "translate_batch", drop)
    display, ok = localize.localize_assess(_sample_result(), "en")
    assert ok is True
    assert "RM400" not in display["message_ms"]


def test_appeal_round_trip(monkeypatch):
    monkeypatch.setattr(translate, "translate_batch", _tag_batch("EN::"))
    letter = {
        "program_id": "jkm_btb", "program_name_ms": "Bantuan Tunai Bulanan",
        "agency": "JKM",
        "body_ms": "Tuan, saya merayu permohonan bantuan RM250 sebulan.",
        "routing_ms": "Hantar ke pejabat JKM daerah.",
        "citation": {"doc_title": "Garis Panduan JKM 2018", "locator": "6.3"},
        "grounded": True,
    }
    display, ok = localize.localize_appeal(letter, "en")
    assert ok is True
    assert display["body_ms"].startswith("EN::")
    assert display["routing_ms"].startswith("EN::")
    assert display["program_name_ms"].startswith("EN::")
    assert display["agency"] == "JKM"
    assert display["citation"]["doc_title"] == "Garis Panduan JKM 2018"


@pytest.mark.parametrize("text,expected", [
    ("RM400 sebulan", {"400"}),
    ("RM250 hingga RM350", {"250", "350"}),
    ("RM1,200 dan RM2,500", {"1200", "2500"}),
    ("tiada amaun di sini", set()),
])
def test_rm_amounts_extraction(text, expected):
    assert localize.rm_amounts(text) == expected
