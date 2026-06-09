"""Deterministic anti-fabrication guard for the narrative.

Groundedness detection is a fuzzy, style-sensitive signal. This guard is exact and
targets the one failure that actually harms users: a fabricated *money amount*.
Every "RMxxx" in the narrative must trace to a verdict amount, the applicant's own
stated income, or a gazetted threshold — otherwise the narrative is rejected.
"""
from __future__ import annotations

import re
from typing import Iterable

from compute.profile import Applicant

_RM = re.compile(r"RM\s?([\d,]+(?:\.\d{1,2})?)")


def amounts_in(text: str) -> set[int]:
    out: set[int] = set()
    for raw in _RM.findall(text):
        try:
            out.add(int(round(float(raw.replace(",", "")))))
        except ValueError:
            continue
    return out


def _numbers_from_thresholds(thresholds: dict) -> set[int]:
    nums: set[int] = set()
    for ref in thresholds.get("reference_values", {}).values():
        if isinstance(ref.get("value"), (int, float)):
            nums.add(int(ref["value"]))
    for spec in thresholds.get("programs", {}).values():
        amount = spec.get("amount", {})
        for key in ("monthly_myr", "monthly_myr_min", "monthly_myr_max"):
            if isinstance(amount.get(key), (int, float)):
                nums.add(int(amount[key]))
        for crit in spec.get("criteria", []):
            if isinstance(crit.get("threshold"), (int, float)):
                nums.add(int(crit["threshold"]))
    # common annual restatements (RM600 = RM50 x 12) already appear via notes text
    return nums


def allowed_amounts(applicant: Applicant, thresholds: dict,
                    extra: Iterable[int] = ()) -> set[int]:
    allowed = _numbers_from_thresholds(thresholds)
    allowed.update({int(round(applicant.individual_income)),
                    int(round(applicant.household_income))})
    allowed.update(int(x) for x in extra)
    allowed.add(0)
    return allowed


def verify_amounts(narrative: str, allowed: set[int]) -> tuple[bool, list[int]]:
    """Return (ok, fabricated_amounts). ok is False if any RM amount is untraceable."""
    fabricated = sorted(a for a in amounts_in(narrative) if a not in allowed)
    return (not fabricated), fabricated
