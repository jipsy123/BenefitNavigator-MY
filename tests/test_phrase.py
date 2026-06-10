"""Unit tests for agent.phrase — display-only contextual rewording of grill questions.

Trust contract under test: phrasing NEVER raises and NEVER blocks the grill — any
failure (LLM error, empty output, oversized output, unknown field/language) returns
None so the client falls back to its static i18n template.
"""
from __future__ import annotations

from agent import llm, phrase


def _patch(monkeypatch, reply):
    monkeypatch.setattr(llm, "chat_text", lambda system, user, **k: reply)


def test_returns_cleaned_single_line_question(monkeypatch):
    _patch(monkeypatch, "  Since you care for your mother,\n does she hold a Kad OKU?  ")
    out = phrase.phrase_question("has_kad_oku", "I care for my OKU mother",
                                 {"is_carer": True}, "en")
    assert out == "Since you care for your mother, does she hold a Kad OKU?"


def test_returns_none_when_llm_raises(monkeypatch):
    def boom(system, user, **k):
        raise RuntimeError("azure down")
    monkeypatch.setattr(llm, "chat_text", boom)
    assert phrase.phrase_question("age", "hi", {}, "en") is None


def test_returns_none_for_empty_or_oversized_output(monkeypatch):
    _patch(monkeypatch, "   ")
    assert phrase.phrase_question("age", "hi", {}, "en") is None
    _patch(monkeypatch, "x" * 500)
    assert phrase.phrase_question("age", "hi", {}, "en") is None


def test_returns_none_without_calling_llm_for_bad_inputs(monkeypatch):
    def boom(system, user, **k):
        raise AssertionError("llm must not be called")
    monkeypatch.setattr(llm, "chat_text", boom)
    assert phrase.phrase_question("not_a_field", "hi", {}, "en") is None
    assert phrase.phrase_question("age", "hi", {}, "fr") is None
    assert phrase.phrase_question("age", "", {}, "en") is None
