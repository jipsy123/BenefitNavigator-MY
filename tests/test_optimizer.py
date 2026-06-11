"""Unit tests for compute/optimizer.py — the deterministic optimal-unlock planner.

The optimizer is PURE: it reasons over an Applicant plus the curated thresholds.json
with no LLM and no I/O beyond loading thresholds. Nothing is mocked here — these tests
assert the exact greedy registration-flip plan, that income-blocked programmes never
produce a step, that already-registered flags are never proposed, and that the planner
is deterministic (same input -> byte-identical UnlockPlan).
"""
from __future__ import annotations

from compute import optimizer
from compute.optimizer import REGISTRATION_FIELDS, UnlockPlan, UnlockStep
from compute.profile import Applicant


# The canonical worked case from the design: an OKU who cannot work, with a tiny
# income, unlocks BTB via Kad OKU (+250) and SARA via STR approval (+50).
def _oku_applicant() -> Applicant:
    return Applicant(citizen=True, age=35, is_oku=True, unable_to_work=True,
                     individual_income=0, household_income=1500)


# --- the canonical OKU plan ------------------------------------------------------

def test_oku_baseline_is_str_bujang_only():
    # Single OKU, age 35, income 0 already qualifies for STR Bujang (RM50) and nothing
    # else until a registration is completed.
    plan = optimizer.plan(_oku_applicant())
    assert plan.baseline_monthly_min == 50


def test_oku_plan_unlocks_btb_then_sara_to_potential_350():
    plan = optimizer.plan(_oku_applicant())
    assert plan.potential_monthly_min == 350
    assert [s.field for s in plan.steps] == ["has_kad_oku", "str_approved"]
    assert [s.marginal_monthly_min for s in plan.steps] == [250, 50]


def test_oku_steps_carry_the_program_they_unlock():
    # Each step names exactly the newly-unlocked programme(s) — Kad OKU -> BTB, STR -> SARA.
    plan = optimizer.plan(_oku_applicant())
    kad_step, str_step = plan.steps
    assert [p["program_id"] for p in kad_step.programs] == ["jkm_btb"]
    assert [p["program_id"] for p in str_step.programs] == ["sara"]


def test_steps_carry_action_text_and_citations():
    # action_ms comes from thresholds.json gap_action_ms; each unlocked programme
    # carries the gazetted citation the verdict is based on.
    plan = optimizer.plan(_oku_applicant())
    kad_step = plan.steps[0]
    assert "Kad OKU" in kad_step.action_ms
    assert kad_step.programs[0]["citation"]["source_url"]  # non-empty gov citation
    assert kad_step.programs[0]["citation"]["locator"]


def test_steps_are_ordered_biggest_marginal_gain_first():
    plan = optimizer.plan(_oku_applicant())
    margins = [s.marginal_monthly_min for s in plan.steps]
    assert margins == sorted(margins, reverse=True)


def test_potential_equals_baseline_plus_sum_of_marginals():
    plan = optimizer.plan(_oku_applicant())
    assert plan.potential_monthly_min == plan.baseline_monthly_min + sum(
        s.marginal_monthly_min for s in plan.steps)


# --- income failure: no step for a programme the applicant can't afford ----------

def test_income_failing_program_yields_no_step_for_that_flag():
    # An OKU who cannot work but earns RM5,000 fails the income gate of BTB/EPC, so
    # registering Kad OKU unlocks nothing -> has_kad_oku is never proposed. Only the
    # registration-only SARA path (via STR) remains.
    a = Applicant(citizen=True, age=35, is_oku=True, unable_to_work=True,
                  marital_status="single", individual_income=5000,
                  household_income=5000)
    plan = optimizer.plan(a)
    assert not any(s.field == "has_kad_oku" for s in plan.steps)
    assert [s.field for s in plan.steps] == ["str_approved"]


def test_plan_never_pads_with_zero_gain_steps():
    # Every proposed step must add real RM — the plan never lists a flip that unlocks
    # nothing.
    plan = optimizer.plan(_oku_applicant())
    assert all(s.marginal_monthly_min > 0 for s in plan.steps)


# --- already-registered flags are never proposed --------------------------------

def test_already_registered_flags_are_not_proposed():
    a = Applicant(citizen=True, age=35, is_oku=True, unable_to_work=True,
                  individual_income=0, household_income=1500,
                  has_kad_oku=True, str_approved=True, ekasih_listed=True)
    plan = optimizer.plan(a)
    assert plan.steps == ()
    # Nothing left to register, so potential cannot exceed the baseline.
    assert plan.potential_monthly_min == plan.baseline_monthly_min


def test_only_unregistered_flags_can_appear_as_steps():
    # Kad OKU already held: it must never appear, but the remaining flags still can.
    a = Applicant(citizen=True, age=35, is_oku=True, unable_to_work=True,
                  individual_income=0, household_income=1500, has_kad_oku=True)
    plan = optimizer.plan(a)
    proposed = {s.field for s in plan.steps}
    assert "has_kad_oku" not in proposed
    assert proposed <= set(REGISTRATION_FIELDS)


# --- determinism ------------------------------------------------------------------

def test_plan_is_deterministic():
    a = _oku_applicant()
    assert optimizer.plan(a) == optimizer.plan(a)


def test_plan_returns_frozen_dataclasses():
    plan = optimizer.plan(_oku_applicant())
    assert isinstance(plan, UnlockPlan)
    assert all(isinstance(s, UnlockStep) for s in plan.steps)
    assert isinstance(plan.steps, tuple)


# --- ekasih is redundant once STR already unlocks SARA (greedy stays exact) -------

def test_redundant_registration_for_same_program_is_dropped():
    # SARA is logic "any": str_approved OR ekasih_listed unlocks it. Once str_approved
    # is taken, flipping ekasih_listed adds nothing, so it is never a second step.
    plan = optimizer.plan(_oku_applicant())
    assert "ekasih_listed" not in {s.field for s in plan.steps}
