"""OPTIMIZE — the deterministic optimal-unlock planner.

Given a validated Applicant, find the *sequence* of registration steps (Kad OKU,
eKasih, STR) that unlocks the most monthly benefit, ordered by marginal RM gained
per step. Greedy is exact here: each registration flag gates its own programmes
independently, so marginal gains never interact.

Trust properties (same bar as checker.py):
  - Pure functions over the Applicant + curated thresholds.json. No LLM, no I/O
    beyond loading thresholds, so every RM figure passes the amount guard by
    construction.
  - Only *registration* flags are ever simulated — the optimizer never pretends
    an income or a fact (age, disability) is different from what was stated.
  - Every step carries the gazetted citation(s) of the programme(s) it unlocks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .checker import load_thresholds, to_dict
from .profile import Applicant
from .status import summarise

# Flags an applicant can change by registering — the optimizer's whole move set.
REGISTRATION_FIELDS: tuple[str, ...] = ("has_kad_oku", "str_approved", "ekasih_listed")


@dataclass(frozen=True)
class UnlockStep:
    field: str                       # the registration flag this step flips
    action_ms: str                   # the concrete next step, from thresholds.json
    marginal_monthly_min: int        # RM/month this step alone unlocks (lower bound)
    programs: tuple[dict, ...]       # programmes newly unlocked, each with citation


@dataclass(frozen=True)
class UnlockPlan:
    baseline_monthly_min: int        # RM/month already qualified for
    potential_monthly_min: int       # RM/month after completing every step
    steps: tuple[UnlockStep, ...]    # ordered: biggest marginal gain first


def _action_for(field: str, thresholds: dict, program_ids: set[str]) -> str:
    """The curated gap action text for a registration flag, preferring the
    programmes this step unlocks so the advice matches the citation."""
    fallback = ""
    for pid, spec in thresholds["programs"].items():
        for crit in spec["criteria"]:
            if (crit["type"] == "flag_true" and crit.get("flag") == field
                    and crit.get("category") == "registration"):
                action = crit.get("gap_action_ms") or ""
                if action and pid in program_ids:
                    return action
                fallback = fallback or action
    return fallback


def _simulate(applicant: Applicant, fields: tuple[str, ...]) -> Applicant:
    return applicant.with_changes(**{field: True for field in fields})


def plan(applicant: Applicant, thresholds: Optional[dict] = None) -> UnlockPlan:
    """Build the ordered registration plan with the marginal RM each step unlocks.

    Greedy by marginal gain: at each round, simulate every remaining registration
    flip on top of the steps already taken and keep the one that adds the most
    RM/month. A flip that adds nothing (income or facts also fail, or another step
    already unlocked the programme) is dropped — the plan never pads itself.
    """
    data = thresholds or load_thresholds()
    baseline = summarise(applicant, data)
    eligible_ids = {r.program_id for r in baseline.eligible}
    current_total = baseline.total_monthly_min

    taken: tuple[str, ...] = ()
    remaining = [f for f in REGISTRATION_FIELDS if not getattr(applicant, f)]
    steps: list[UnlockStep] = []

    while remaining:
        best: Optional[tuple[int, str, tuple, set]] = None
        for field in remaining:
            assessment = summarise(_simulate(applicant, taken + (field,)), data)
            gain = assessment.total_monthly_min - current_total
            if gain <= 0:
                continue
            new_ids = {r.program_id for r in assessment.eligible} - eligible_ids
            new_programs = tuple(to_dict(r) for r in assessment.eligible
                                 if r.program_id in new_ids)
            if best is None or gain > best[0]:
                best = (gain, field, new_programs, new_ids)
        if best is None:
            break
        gain, field, new_programs, new_ids = best
        steps.append(UnlockStep(
            field=field,
            action_ms=_action_for(field, data, new_ids),
            marginal_monthly_min=gain,
            programs=new_programs,
        ))
        taken = taken + (field,)
        eligible_ids |= new_ids
        current_total += gain
        remaining.remove(field)

    return UnlockPlan(
        baseline_monthly_min=baseline.total_monthly_min,
        potential_monthly_min=current_total,
        steps=tuple(steps),
    )
