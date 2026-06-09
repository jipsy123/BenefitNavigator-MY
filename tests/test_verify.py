"""Pure tests for the deterministic anti-fabrication amount guard (no network)."""
from __future__ import annotations

from agent import verify
from compute.checker import load_thresholds
from compute.profile import Applicant


def _allowed(applicant: Applicant) -> set[int]:
    return verify.allowed_amounts(applicant, load_thresholds())


def test_legit_benefit_amounts_pass():
    a = Applicant(citizen=True, age=35, individual_income=0, household_income=1500)
    allowed = _allowed(a)
    text = "Anda layak BTB RM250 sebulan dan STR Bujang RM50 sebulan. Pendapatan RM1,500."
    ok, fabricated = verify.verify_amounts(text, allowed)
    assert ok and fabricated == []


def test_fabricated_amount_is_caught():
    a = Applicant(citizen=True, age=35, individual_income=0, household_income=1500)
    allowed = _allowed(a)
    text = "Anda layak bantuan istimewa RM9000 sebulan dan kereta percuma."
    ok, fabricated = verify.verify_amounts(text, allowed)
    assert ok is False
    assert 9000 in fabricated


def test_amounts_extraction_handles_commas_and_spacing():
    assert verify.amounts_in("RM1,200 dan RM 350 serta RM50") == {1200, 350, 50}


def test_gazetted_threshold_amounts_are_allowed():
    """Mentioning a real threshold (e.g. RM5,000 household cap) is not fabrication."""
    a = Applicant(citizen=True, age=35, household_income=4000, individual_income=2000)
    allowed = _allowed(a)
    ok, fabricated = verify.verify_amounts(
        "Pendapatan isi rumah di bawah had RM5,000 melayakkan STR.", allowed)
    assert ok and fabricated == []
