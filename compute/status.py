"""STATUS-CHECK + GAP analysis.

Turns the checker's raw verdicts into the genuinely useful output: which benefits
you already qualify for, and for the rest, *why not* and *what single action* would
unlock them. A 'near miss' is the high-value case — income/eligibility facts all
pass and only a registration gate (Kad OKU, eKasih, STR approval) stands in the way.
"""
from __future__ import annotations

from dataclasses import dataclass

from .checker import ProgramResult, assess
from .profile import Applicant


@dataclass(frozen=True)
class Gap:
    program_id: str
    name_ms: str
    agency: str
    amount: dict
    near_miss: bool                  # only registration gate(s) block; income/facts met
    blocking_ms: tuple[str, ...]     # unmet criterion labels
    actions_ms: tuple[str, ...]      # concrete next steps
    citation: dict


def _is_near_miss(result: ProgramResult) -> bool:
    income_ok = all(c.passed for c in result.criteria if c.category == "income")
    fact_ok = all(c.passed for c in result.criteria if c.category == "fact")
    unmet = result.unmet
    only_registration = bool(unmet) and all(c.category == "registration" for c in unmet)
    return income_ok and fact_ok and only_registration


def analyse_gaps(results: list[ProgramResult]) -> list[Gap]:
    gaps: list[Gap] = []
    for result in results:
        if result.eligible:
            continue
        gaps.append(Gap(
            program_id=result.program_id,
            name_ms=result.name_ms,
            agency=result.agency,
            amount=result.amount,
            near_miss=_is_near_miss(result),
            blocking_ms=tuple(c.label_ms for c in result.unmet),
            actions_ms=tuple(c.gap_action_ms for c in result.unmet if c.gap_action_ms),
            citation=result.citation,
        ))
    # near misses first — the most actionable advice surfaces at the top
    gaps.sort(key=lambda g: (not g.near_miss, g.agency, g.name_ms))
    return gaps


@dataclass(frozen=True)
class Assessment:
    eligible: tuple[ProgramResult, ...]
    gaps: tuple[Gap, ...]

    @property
    def total_monthly_min(self) -> int:
        """Lower-bound of monthly cash the applicant already qualifies for (RM)."""
        total = 0
        for r in self.eligible:
            amount = r.amount
            total += int(amount.get("monthly_myr", amount.get("monthly_myr_min", 0)))
        return total


def summarise(applicant: Applicant, thresholds: dict | None = None) -> Assessment:
    results = assess(applicant, thresholds)
    eligible = tuple(r for r in results if r.eligible)
    gaps = tuple(analyse_gaps(results))
    return Assessment(eligible=eligible, gaps=gaps)
