"""Unit tests for the proper-noun protection in the translation transport layer.

These are offline: the network call (`_post_chunk`'s requests.post) is mocked, so we
verify the protect/restore markup and the textType=html wiring without hitting Azure.
The protection exists because the Translator otherwise mangles proper nouns
("Talian Kasih" -> "Línea Kasih", "LHDN" -> "IRB"), which is both wrong and
inconsistent with the Malay-context UI.
"""
from __future__ import annotations

from agent import translate


# --- _protect / _restore round-trip ----------------------------------------------

def test_protect_wraps_proper_nouns_and_newlines():
    out = translate._protect("Hubungi Talian Kasih 15999 di pejabat JKM/LHDN.\nTerima kasih.")
    assert '<span class="notranslate">Talian Kasih</span>' in out
    # Agency codes are intentionally NOT protected (acceptable English expansions; avoids
    # the "JKMoffice" spacing artifact). They stay as plain text for the Translator.
    assert "notranslate" not in out.split("Talian Kasih</span>")[1]  # nothing after is wrapped
    assert "<br>" in out and "\n" not in out


def test_restore_unwraps_spans_and_brs():
    translated = ('Contact <span class="notranslate">Talian Kasih</span> 15999 at the '
                  '<span class="notranslate">JKM</span> office.<br>Thank you.')
    restored = translate._restore(translated)
    assert restored == "Contact Talian Kasih 15999 at the JKM office.\nThank you."


def test_protect_restore_identity_round_trip():
    # If the translator changed nothing, restore must recover the source exactly.
    for src in ["Sila hubungi Talian Kasih 15999.",
                "Pendapatan < RM2000 & keluarga > 3 orang.",
                "eKasih, MyKad, JPN — semua kekal.",
                "Baris satu.\nBaris dua.\nBaris tiga."]:
        assert translate._restore(translate._protect(src)) == src


def test_restore_tolerates_reordered_span_attrs_and_self_closing_br():
    translated = ('Hubungi <span dir="ltr" class="notranslate">Talian Kasih</span>.<br/>Tamat.')
    assert translate._restore(translated) == "Hubungi Talian Kasih.\nTamat."


def test_ekasih_matches_before_kasih_substring():
    # Longest-first ordering: "eKasih" is protected as one unit (not split on "Kasih").
    out = translate._protect("Saya tersenarai dalam eKasih.")
    assert '<span class="notranslate">eKasih</span>' in out


# --- translate_batch wiring (network mocked) -------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_translate_batch_sends_html_and_restores(monkeypatch):
    captured = {}

    def fake_post(url, params=None, headers=None, json=None, timeout=None):
        captured["params"] = params
        captured["sent"] = [item["text"] for item in json]
        # Echo the protected markup back verbatim (proper nouns kept, "translated" prose).
        return _FakeResp([{"translations": [{"text": item["text"]}]} for item in json])

    monkeypatch.setattr(translate.requests, "post", fake_post)
    out, ok = translate.translate_batch(["Hubungi Talian Kasih 15999."], "en")
    assert ok is True
    assert captured["params"]["textType"] == "html"               # html mode requested
    assert '<span class="notranslate">Talian Kasih</span>' in captured["sent"][0]
    assert out == ["Hubungi Talian Kasih 15999."]                 # markup stripped on return


def test_translate_batch_noop_on_same_language():
    out, ok = translate.translate_batch(["apa-apa"], "ms")
    assert ok is True and out == ["apa-apa"]
