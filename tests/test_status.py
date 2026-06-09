"""TDD for STATUS-CHECK / GAP analysis."""
from __future__ import annotations

from compute.profile import Applicant
from compute.status import summarise


def test_near_miss_surfaces_registration_action_first():
    """OKU, unable to work, low income, but no Kad OKU: BTB is a NEAR MISS —
    income/facts pass, only the registration gate blocks. The action must be
    surfaced at the top of the gap list."""
    a = Applicant(citizen=True, age=40, is_oku=True, has_kad_oku=False,
                  unable_to_work=True, individual_income=0,
                  household_income=1500, household_size=3)
    result = summarise(a)
    btb_gap = next(g for g in result.gaps if g.program_id == "jkm_btb")
    assert btb_gap.near_miss is True
    assert any("Kad OKU" in a for a in btb_gap.actions_ms)
    # near misses sort ahead of non-near-misses
    assert result.gaps[0].near_miss is True


def test_eligible_totals_sum_minimum_monthly():
    """A registered, unable-to-work, low-income OKU on eKasih qualifies for
    BTB (RM250) + SARA (>=RM50) => >= RM300/month floor."""
    a = Applicant(citizen=True, age=40, is_oku=True, has_kad_oku=True,
                  unable_to_work=True, individual_income=0,
                  household_income=1200, household_size=3,
                  ekasih_listed=True, ekasih_category="miskin")
    result = summarise(a)
    ids = {r.program_id for r in result.eligible}
    assert "jkm_btb" in ids and "sara" in ids
    assert result.total_monthly_min >= 300


def test_high_income_non_oku_has_no_eligibility_but_clean_gaps():
    a = Applicant(citizen=True, age=35, marital_status="married",
                  household_income=9000, individual_income=6000, household_size=2)
    result = summarise(a)
    assert result.eligible == ()
    assert len(result.gaps) >= 1  # everything is a gap, none crash
