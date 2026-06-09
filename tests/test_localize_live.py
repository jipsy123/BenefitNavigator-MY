"""Live integration: verify the amount guard survives real Azure translation.

Mocked unit tests prove the plumbing; this proves the trust claim — that fixed and
range RM amounts pass through Azure MT intact into en/zh-Hans/ta, so localization
neither corrupts amounts nor trips the safety gate into a spurious fallback.

Run with:  PYTHONPATH=. .venv/bin/python -m pytest tests/test_localize_live.py -m integration
Requires an authenticated `az` CLI (the key is fetched at runtime).
"""
from __future__ import annotations

import re

import pytest

from agent import localize

pytestmark = pytest.mark.integration

_FIXED = "400"
_RANGE = ("250", "350")
_HOUSEHOLD = "1200"
_FULLWIDTH = {ord("０") + i: ord("0") + i for i in range(10)}


def _digits(text: str) -> set[str]:
    """All numeric figures as bare-digit strings (separator/locale agnostic).
    Measures amount *survival* — robust to MT rendering 'RM400' as '400令吉'."""
    ascii_text = text.translate(_FULLWIDTH)
    return {re.sub(r"[.,]", "", t) for t in re.findall(r"\d[\d.,]*", ascii_text)}


def _result() -> dict:
    return {
        "ok": True, "refused": False,
        "message_ms": (
            "Anda layak menerima Elaun Pekerja OKU sebanyak RM400 sebulan daripada "
            "JKM. Anda juga hampir layak Bantuan Sara Hidup antara RM250 hingga RM350 "
            "sebulan. Pendapatan isi rumah anda RM1,200 sebulan."
        ),
        "assumptions_ms": ["Andaian: anda warganegara Malaysia."],
        "eligible": [{
            "program_id": "jkm_epc", "name_ms": "Elaun Pekerja OKU", "agency": "JKM",
            "eligible": True, "amount": {"type": "fixed", "monthly_myr": 400},
            "citation": {"doc_title": "Garis Panduan JKM 2018", "locator": "6.1",
                         "source_url": "https://example.gov.my/jkm"},
        }],
        "gaps": [], "total_monthly_min": 400, "citations": [],
        "groundedness": {"available": True, "grounded": True},
        "stages": [{"name": "EXPLAIN", "status": "ok",
                    "summary": "Disahkan: jumlah RM bersumber.", "data": None}],
    }


@pytest.mark.parametrize("lang", ["en", "zh-Hans", "ta"])
def test_amounts_survive_translation(lang):
    display, ok = localize.localize_assess(_result(), lang)

    assert ok is True, f"{lang}: localization fell back to Malay unexpectedly"
    assert display["lang"] == lang

    # SURVIVAL — every figure reaches the user as digits. Measured on digits, not
    # 'RM'-prefixed tokens: Chinese localizes 'RM400' -> '400令吉', still digit 400.
    figures = _digits(display["message_ms"])
    assert _FIXED in figures, f"{lang}: fixed amount {_FIXED} lost from prose"
    assert {_RANGE[0], _RANGE[1]}.issubset(figures), f"{lang}: range amount lost"
    assert _HOUSEHOLD in figures, f"{lang}: household figure lost"

    # SAFETY — no RM-amount may appear that wasn't in the Malay source (no MT
    # invention). Translation may only drop/keep/reformat, never add.
    source_rm = localize.rm_amounts(_result()["message_ms"])
    assert localize.rm_amounts(display["message_ms"]).issubset(source_rm)

    # BACKSTOP — the verified amount also lives in a language-neutral structured
    # field, rendered independently of the prose, so it can never be lost.
    assert display["eligible"][0]["amount"]["monthly_myr"] == 400


@pytest.mark.parametrize("lang", ["en", "zh-Hans", "ta"])
def test_text_is_actually_translated(lang):
    """Sanity: output differs from the Malay source (translation really ran)."""
    display, ok = localize.localize_assess(_result(), lang)
    assert ok is True
    assert display["message_ms"] != _result()["message_ms"]
