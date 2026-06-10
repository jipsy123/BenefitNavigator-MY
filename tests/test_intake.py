"""Unit tests for agent.intake — the LLM boundary that extracts structured facts.

The Azure call is mocked; these pin the boundary's contracts: assumptions coercion,
and (trust-critical for the grill) that `facts` stays pristine — only what the model
actually stated, with no income reconciliation injected.
"""
from __future__ import annotations

from agent import intake, llm


def _patch(monkeypatch, payload: dict):
    monkeypatch.setattr(llm, "chat_json", lambda system, user, **k: payload)


def test_assumptions_string_is_wrapped_not_exploded(monkeypatch):
    # A string must become a one-element tuple, NOT tuple("text") -> per-char tuple.
    _patch(monkeypatch, {"profile": {"is_oku": True},
                         "assumptions_ms": "Maklumat umur tidak dinyatakan.",
                         "retrieval_query_ms": "q"})
    result = intake.run_intake("x")
    assert result.assumptions_ms == ("Maklumat umur tidak dinyatakan.",)


def test_assumptions_list_is_preserved(monkeypatch):
    _patch(monkeypatch, {"profile": {}, "assumptions_ms": ["a", "b"],
                         "retrieval_query_ms": "q"})
    assert intake.run_intake("x").assumptions_ms == ("a", "b")


def test_facts_are_pristine_without_household_injection(monkeypatch):
    # Individual income stated, household not: `facts` must leave household UNKNOWN so
    # the grill asks it — silently copying it could pass a household income gate falsely.
    _patch(monkeypatch, {"profile": {"individual_income": 2000},
                         "retrieval_query_ms": "q"})
    result = intake.run_intake("x")
    assert result.facts == {"individual_income": 2000}
    assert "household_income" not in result.facts
    # The one-shot Applicant is still reconciled + valid (household >= individual).
    assert result.applicant.household_income == 2000


def test_facts_drop_unknown_keys(monkeypatch):
    _patch(monkeypatch, {"profile": {"is_oku": True, "bogus_field": 1},
                         "retrieval_query_ms": "q"})
    assert intake.run_intake("x").facts == {"is_oku": True}


def test_presumed_dict_passes_through_pristine(monkeypatch):
    # Sanitization happens at the API boundary (elicit.sanitize_presumptions);
    # intake's contract is to surface exactly what the model proposed.
    presumed = {"marital_status": {"value": "single", "reason_ms": "berumur 12 tahun"}}
    _patch(monkeypatch, {"profile": {"age": 12}, "presumed": presumed,
                         "retrieval_query_ms": "q"})
    result = intake.run_intake("x")
    assert result.presumed == presumed
    assert result.facts == {"age": 12}


def test_presumed_missing_or_malformed_is_empty_dict(monkeypatch):
    _patch(monkeypatch, {"profile": {}, "retrieval_query_ms": "q"})
    assert intake.run_intake("x").presumed == {}
    _patch(monkeypatch, {"profile": {}, "presumed": ["not", "a", "dict"],
                         "retrieval_query_ms": "q"})
    assert intake.run_intake("x").presumed == {}
