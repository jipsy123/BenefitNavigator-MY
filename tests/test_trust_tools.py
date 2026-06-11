"""Tests for the agent-callable trust tools (mas/trust_tools.py).

These prove the tools (a) read facts only from the verified token, (b) return
deterministic compute output with no narrative, and (c) preserve the OKU-fact
invariant at the tool boundary — i.e. an agent calling `optimize` can never be
told to "register a Kad OKU" for a non-disabled person.
"""
from __future__ import annotations

import pytest

from mas import trust_tools
from mas.state import ChatState, InvalidToken, encode


def _token(**facts) -> str:
    return encode(ChatState(facts=facts))


# --- assess ----------------------------------------------------------------------

def test_assess_returns_only_compute_output():
    token = _token(citizen=True, has_dependents=True, household_income=2000)
    out = trust_tools.assess(token)
    assert set(out) == {"eligible", "gaps", "total_monthly_min", "citations"}
    assert "message_ms" not in out and "narrative" not in out      # no narration here
    ids = {e["program_id"] for e in out["eligible"]}
    assert "str_household" in ids                                   # citizen + dependents + income
    assert out["total_monthly_min"] >= 100


def test_assess_verifies_the_token():
    bad = _token(citizen=True) + "tamper"
    with pytest.raises(InvalidToken):
        trust_tools.assess(bad)


def test_assess_citations_are_gov_sources():
    out = trust_tools.assess(_token(citizen=True, has_dependents=True, household_income=2000))
    assert out["citations"], "an eligible verdict must carry at least one citation"
    for c in out["citations"]:
        assert c["source_url"]                                     # cite-or-refuse invariant


# --- optimize (the OKU regression lives here) ------------------------------------

def test_optimize_unlocks_epc_for_a_genuine_oku_without_the_card():
    # Disabled, working, low income, no card yet -> registering the card unlocks EPC.
    token = _token(citizen=True, age=30, is_oku=True, is_working=True,
                  individual_income=500)
    plan = trust_tools.optimize(token)
    step_fields = {s["field"] for s in plan["steps"]}
    assert "has_kad_oku" in step_fields
    kad = next(s for s in plan["steps"] if s["field"] == "has_kad_oku")
    assert kad["marginal_monthly_min"] >= 400                       # EPC = RM400


def test_optimize_never_recommends_kad_oku_for_a_non_disabled_person():
    # NOT is_oku: flipping has_kad_oku unlocks nothing, so it must not be a step.
    token = _token(citizen=True, age=30, is_working=True, individual_income=500)
    plan = trust_tools.optimize(token)
    step_fields = {s["field"] for s in plan["steps"]}
    assert "has_kad_oku" not in step_fields                        # the OKU-bug guard


# --- grill_next ------------------------------------------------------------------

def test_grill_next_asks_when_profile_incomplete():
    out = trust_tools.grill_next(encode(ChatState()))
    assert out["done"] is False
    assert out["question"] is not None
    assert out["question"]["field"] in trust_tools.elicit.FIELD_META


def test_grill_next_done_when_every_program_decided():
    token = _token(citizen=True, age=30, marital_status="single", has_dependents=False,
                  is_oku=False, has_kad_oku=False, is_working=True, unable_to_work=False,
                  is_carer=False, individual_income=5000, household_income=5000,
                  str_approved=False, ekasih_listed=False)
    out = trust_tools.grill_next(token)
    assert out["done"] is True
    assert out["question"] is None


# --- grade -----------------------------------------------------------------------

def test_grade_reports_readability():
    out = trust_tools.grade("Anda layak. Kami akan bantu.")
    assert set(out) == {"grade", "readable", "target_grade"}
    assert isinstance(out["grade"], float) and out["grade"] >= 0.0
    assert isinstance(out["readable"], bool)
