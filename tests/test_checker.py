"""TDD for the deterministic eligibility checker.

These tests encode the *legal income concepts* from the corpus, not paraphrases:
  - individual vs household income are distinct (the advisor's real-harm bug)
  - "dan ke bawah" => inclusive (<=), so income == threshold is ELIGIBLE
  - SARA uses OR-logic over registration gates
Run:  python -m pytest tests/ -q
"""
from __future__ import annotations

import pytest

from compute import checker
from compute.profile import Applicant, from_dict


def _result(applicant: Applicant, program_id: str):
    results = {r.program_id: r for r in checker.assess(applicant)}
    return results[program_id]


# --- The critical bug the advisor caught: BTB is INDIVIDUAL income, not per-capita ---

def test_btb_eligible_when_individual_income_zero_despite_high_household():
    """An unable-to-work OKU with RM0 own income qualifies for BTB regardless of
    how much the rest of the household earns. Modeling this as household/size would
    wrongly disqualify the exact population the tool serves."""
    a = Applicant(citizen=True, age=40, is_oku=True, has_kad_oku=True,
                  unable_to_work=True, individual_income=0,
                  household_income=4000, household_size=5)
    r = _result(a, "jkm_btb")
    assert r.eligible is True
    assert r.amount["monthly_myr"] == 250


def test_btb_blocked_by_missing_kad_oku_yields_gap_action():
    """Same person without a registered Kad OKU: not eligible, and the unmet
    criterion must carry an actionable next step for GAP analysis."""
    a = Applicant(citizen=True, age=40, is_oku=True, has_kad_oku=False,
                  unable_to_work=True, individual_income=0,
                  household_income=4000, household_size=5)
    r = _result(a, "jkm_btb")
    assert r.eligible is False
    unmet_flags = [c.label_ms for c in r.unmet]
    assert any("Kad OKU" in lbl for lbl in unmet_flags)
    gap = next(c for c in r.unmet if "Kad OKU" in c.label_ms)
    assert gap.category == "registration"
    assert gap.gap_action_ms and "JKM" in gap.gap_action_ms


# --- Inclusive boundary: "dan ke bawah" == <= ---

def test_epc_income_boundary_is_inclusive():
    base = dict(citizen=True, age=30, is_oku=True, has_kad_oku=True, is_working=True)
    at_threshold = Applicant(individual_income=1200, household_income=1200, **base)
    over = Applicant(individual_income=1201, household_income=1201, **base)
    assert _result(at_threshold, "jkm_epc").eligible is True   # == 1200 eligible
    assert _result(over, "jkm_epc").eligible is False          # 1201 not


def test_str_household_income_boundary_is_inclusive():
    base = dict(citizen=True, age=35, marital_status="married", household_size=4)
    at = Applicant(household_income=5000, individual_income=5000, **base)
    over = Applicant(household_income=5001, individual_income=5001, **base)
    assert _result(at, "str_household").eligible is True
    assert _result(over, "str_household").eligible is False


# --- EPC (working) vs BTB (unable to work) are mutually exclusive ---

def test_working_oku_gets_epc_not_btb():
    a = Applicant(citizen=True, age=30, is_oku=True, has_kad_oku=True,
                  is_working=True, unable_to_work=False,
                  individual_income=1000, household_income=1000)
    assert _result(a, "jkm_epc").eligible is True
    assert _result(a, "jkm_btb").eligible is False  # fails unable_to_work


# --- STR Bujang age rule: >=21, OR >=18 if OKU registered with JKM ---

def test_str_bujang_under_21_non_oku_not_eligible():
    a = Applicant(citizen=True, age=19, marital_status="single",
                  individual_income=1000, household_income=1000)
    assert _result(a, "str_bujang").eligible is False


def test_str_bujang_oku_18_eligible():
    a = Applicant(citizen=True, age=18, marital_status="single",
                  is_oku=True, has_kad_oku=True,
                  individual_income=500, household_income=500)
    assert _result(a, "str_bujang").eligible is True


def test_str_bujang_21_non_oku_eligible():
    a = Applicant(citizen=True, age=21, marital_status="single",
                  individual_income=2000, household_income=2000)
    assert _result(a, "str_bujang").eligible is True


def test_single_parent_with_dependents_is_household_not_bujang():
    """A single mother with children qualifies for STR Isi Rumah (not Bujang)."""
    a = Applicant(citizen=True, age=38, marital_status="divorced", is_working=True,
                  has_dependents=True, household_income=2800, individual_income=2800,
                  household_size=4)
    assert _result(a, "str_household").eligible is True
    assert _result(a, "str_bujang").eligible is False  # has dependents -> not Bujang


# --- SARA: OR-logic over registration gates ---

def test_sara_eligible_via_ekasih_only():
    a = Applicant(citizen=True, age=45, ekasih_listed=True,
                  ekasih_category="miskin", str_approved=False,
                  individual_income=800, household_income=1500, household_size=3)
    assert _result(a, "sara").eligible is True


def test_sara_not_eligible_without_any_gate():
    a = Applicant(citizen=True, age=45, ekasih_listed=False, str_approved=False)
    assert _result(a, "sara").eligible is False


# --- Boundary validation at the system edge ---

def test_from_dict_rejects_unknown_field():
    with pytest.raises(ValueError):
        from_dict({"citizen": True, "incom": 1000})  # typo must fail loudly


def test_from_dict_rejects_individual_income_above_household():
    with pytest.raises(ValueError):
        from_dict({"individual_income": 5000, "household_income": 1000})


# --- assess() returns every program, eligible ones first, with resolved citations ---

def test_assess_lists_all_programs_with_citation_urls():
    a = Applicant(citizen=True, age=40, is_oku=True, has_kad_oku=True,
                  unable_to_work=True, individual_income=0,
                  household_income=2000, household_size=4)
    results = checker.assess(a)
    assert {r.program_id for r in results} == {
        "jkm_epc", "jkm_btb", "jkm_bpt", "str_household", "str_bujang", "sara"}
    # eligible programs sort before ineligible ones
    eligibilities = [r.eligible for r in results]
    assert eligibilities == sorted(eligibilities, reverse=True)
    # every program resolves an official source_url for its citation
    for r in results:
        assert r.citation["source_url"].startswith("http")
