"""Unit tests for compute.elicit — the three-valued elicitation engine (the grill core).

This is a trust-critical, fully deterministic module: given the facts known so far it
decides which question could still change a verdict and when no question can. No LLM,
no I/O. Tests pin the Kleene truth tables, the three compound evaluators, question
ranking, short-circuit, termination, and the facts -> Applicant materialization that
feeds the existing pipeline.
"""
from __future__ import annotations

import pytest

from compute import checker, elicit
from compute.profile import Applicant

THRESHOLDS = checker.load_thresholds()
REFS = THRESHOLDS["reference_values"]
PROGRAMS = THRESHOLDS["programs"]


def _crit(program_id: str, ctype: str) -> dict:
    for c in PROGRAMS[program_id]["criteria"]:
        if c["type"] == ctype:
            return c
    raise KeyError(f"{ctype} not in {program_id}")


# --- Kleene three-valued logic ---------------------------------------------------

@pytest.mark.parametrize("values,expected", [
    ([True, True], True),
    ([True, False], False),
    ([True, None], None),
    ([False, None], False),     # one False short-circuits an AND, unknowns irrelevant
    ([None, None], None),
    ([], True),                 # vacuous AND
])
def test_kleene_and(values, expected):
    assert elicit.kleene_and(values) is expected


@pytest.mark.parametrize("values,expected", [
    ([False, False], False),
    ([True, False], True),
    ([True, None], True),       # one True short-circuits an OR
    ([False, None], None),
    ([None, None], None),
    ([], False),                # vacuous OR
])
def test_kleene_or(values, expected):
    assert elicit.kleene_or(values) is expected


@pytest.mark.parametrize("value,expected", [(True, False), (False, True), (None, None)])
def test_kleene_not(value, expected):
    assert elicit.kleene_not(value) is expected


# --- simple criterion evaluation -------------------------------------------------

def test_simple_criterion_unknown_when_field_missing():
    assert elicit.eval_criterion({}, _crit("jkm_epc", "citizen"), REFS) is None


def test_simple_criterion_resolves_when_field_known():
    crit = _crit("jkm_epc", "citizen")
    assert elicit.eval_criterion({"citizen": True}, crit, REFS) is True
    assert elicit.eval_criterion({"citizen": False}, crit, REFS) is False


def test_income_criterion_inclusive_threshold():
    crit = _crit("jkm_epc", "individual_income_lte")  # <= 1200
    assert elicit.eval_criterion({"individual_income": 1200}, crit, REFS) is True
    assert elicit.eval_criterion({"individual_income": 1201}, crit, REFS) is False
    assert elicit.eval_criterion({}, crit, REFS) is None


def test_income_criterion_resolves_reference_value():
    crit = _crit("jkm_btb", "individual_income_lte")  # ref pgk = 1198
    assert elicit.eval_criterion({"individual_income": 1198}, crit, REFS) is True
    assert elicit.eval_criterion({"individual_income": 1199}, crit, REFS) is False


# --- compound evaluator: household_category (married OR has_dependents) -----------

def test_household_category_married_is_true_without_dependents():
    crit = _crit("str_household", "household_category")
    # A married person is eligible-category regardless of dependents -> short-circuit.
    assert elicit.eval_criterion({"marital_status": "married"}, crit, REFS) is True


def test_household_category_dependents_is_true_without_marital():
    crit = _crit("str_household", "household_category")
    assert elicit.eval_criterion({"has_dependents": True}, crit, REFS) is True


def test_household_category_false_only_when_both_known_false():
    crit = _crit("str_household", "household_category")
    assert elicit.eval_criterion(
        {"marital_status": "single", "has_dependents": False}, crit, REFS) is False


def test_household_category_unknown_when_single_and_dependents_missing():
    crit = _crit("str_household", "household_category")
    assert elicit.eval_criterion({"marital_status": "single"}, crit, REFS) is None
    assert elicit.eval_criterion({}, crit, REFS) is None


# --- compound evaluator: bujang_category (NOT married AND no dependents) ----------

def test_bujang_category_married_is_false_short_circuit():
    crit = _crit("str_bujang", "bujang_category")
    # Married -> not-bujang, no need to know dependents.
    assert elicit.eval_criterion({"marital_status": "married"}, crit, REFS) is False


def test_bujang_category_dependents_is_false():
    crit = _crit("str_bujang", "bujang_category")
    assert elicit.eval_criterion({"has_dependents": True}, crit, REFS) is False


def test_bujang_category_true_when_single_no_dependents():
    crit = _crit("str_bujang", "bujang_category")
    assert elicit.eval_criterion(
        {"marital_status": "single", "has_dependents": False}, crit, REFS) is True


def test_bujang_category_unknown_when_single_dependents_missing():
    crit = _crit("str_bujang", "bujang_category")
    assert elicit.eval_criterion({"marital_status": "single"}, crit, REFS) is None


# --- compound evaluator: str_bujang_age (>=21 OR (>=18 AND OKU AND Kad)) ----------

def test_str_bujang_age_over_21_is_true():
    crit = _crit("str_bujang", "str_bujang_age")
    assert elicit.eval_criterion({"age": 25}, crit, REFS) is True


def test_str_bujang_age_under_18_is_false():
    crit = _crit("str_bujang", "str_bujang_age")
    assert elicit.eval_criterion({"age": 17}, crit, REFS) is False


def test_str_bujang_age_19_unknown_until_oku_known():
    crit = _crit("str_bujang", "str_bujang_age")
    assert elicit.eval_criterion({"age": 19}, crit, REFS) is None
    assert elicit.eval_criterion(
        {"age": 19, "is_oku": True, "has_kad_oku": True}, crit, REFS) is True
    assert elicit.eval_criterion(
        {"age": 19, "is_oku": False}, crit, REFS) is False


def test_str_bujang_age_unknown_when_age_missing():
    crit = _crit("str_bujang", "str_bujang_age")
    assert elicit.eval_criterion({}, crit, REFS) is None


# --- program status (all / any logic) --------------------------------------------

def test_program_eligible_when_all_criteria_true():
    known = {"citizen": True, "age": 30, "has_kad_oku": True,
             "unable_to_work": True, "individual_income": 500}
    assert elicit.program_status(known, PROGRAMS["jkm_btb"], REFS) is True


def test_program_ineligible_when_one_criterion_false():
    known = {"citizen": True, "age": 30, "has_kad_oku": True,
             "unable_to_work": True, "individual_income": 5000}  # over PGK
    assert elicit.program_status(known, PROGRAMS["jkm_btb"], REFS) is False


def test_program_undecided_when_a_criterion_unknown():
    known = {"citizen": True, "age": 30, "has_kad_oku": True, "unable_to_work": True}
    assert elicit.program_status(known, PROGRAMS["jkm_btb"], REFS) is None


def test_program_short_circuits_false_on_known_blocker():
    # has_kad_oku False kills BTB even with income unknown.
    known = {"has_kad_oku": False}
    assert elicit.program_status(known, PROGRAMS["jkm_btb"], REFS) is False


def test_any_logic_program_sara():
    assert elicit.program_status({}, PROGRAMS["sara"], REFS) is None
    assert elicit.program_status({"str_approved": True}, PROGRAMS["sara"], REFS) is True
    assert elicit.program_status(
        {"str_approved": False, "ekasih_listed": False}, PROGRAMS["sara"], REFS) is False
    assert elicit.program_status(
        {"str_approved": False}, PROGRAMS["sara"], REFS) is None  # ekasih unknown


# --- next_field: ranking, stability, termination ---------------------------------

def test_first_question_is_highest_leverage_field():
    # citizen gates 5 of 6 programmes -> the single most decision-relevant fact.
    need = elicit.next_field({}, set(), THRESHOLDS)
    assert need is not None
    assert need.field == "citizen"


def test_ranking_is_deterministic_and_stable():
    # After citizen, age / has_kad_oku / individual_income each feed 3 undecided
    # programmes at max amount 400 -> tie broken by stable field order (age first).
    need = elicit.next_field({"citizen": True}, set(), THRESHOLDS)
    assert need.field == "age"


def test_field_helps_more_programs_outranks_fewer(monkeypatch):
    # is_carer feeds only BPT(1); household_income feeds BPT+str_household(2).
    known = {"citizen": True, "age": 30, "marital_status": "single",
             "has_dependents": False, "has_kad_oku": False, "is_oku": False,
             "individual_income": 100, "str_approved": False, "ekasih_listed": False}
    # epc/btb dead (has_kad_oku False); str_bujang dead? single,no dep -> bujang True,
    # age 30 -> True, income 100 -> True, citizen True => str_bujang ELIGIBLE.
    # Remaining undecided: jkm_bpt (is_carer?, household_income?), str_household
    # (household_category: single+no-dep -> False => str_household INELIGIBLE).
    # So only BPT undecided -> asks is_carer or household_income.
    need = elicit.next_field(known, set(), THRESHOLDS)
    assert need.field in {"is_carer", "household_income"}
    assert any(p.program_id == "jkm_bpt" for p in need.programs)


def test_skipped_field_is_never_reoffered():
    first = elicit.next_field({}, set(), THRESHOLDS)
    second = elicit.next_field({}, {first.field}, THRESHOLDS)
    assert second is not None
    assert second.field != first.field


def test_done_when_every_program_decided():
    # All booleans known-false / married+over-income => every programme decided.
    known = {"citizen": True, "marital_status": "married", "has_dependents": False,
             "has_kad_oku": False, "is_carer": False, "is_working": False,
             "unable_to_work": False, "is_oku": False, "str_approved": False,
             "ekasih_listed": False, "age": 40,
             "individual_income": 9999, "household_income": 9999}
    assert elicit.next_field(known, set(), THRESHOLDS) is None


def test_done_when_only_skipped_fields_remain():
    # Decide everything except SARA, then skip both of SARA's fields -> done.
    known = {"citizen": True, "marital_status": "married", "has_dependents": False,
             "has_kad_oku": False, "is_carer": False, "is_working": False,
             "unable_to_work": False, "is_oku": False, "age": 40,
             "individual_income": 9999, "household_income": 9999}
    # SARA still undecided (str_approved / ekasih_listed unknown).
    assert elicit.program_status(known, PROGRAMS["sara"], REFS) is None
    asked = {"str_approved", "ekasih_listed"}
    assert elicit.next_field(known, asked, THRESHOLDS) is None


# --- field metadata & skippability (trust: money is non-skippable) ---------------

def test_money_fields_are_not_skippable():
    assert elicit.FIELD_META["individual_income"].skippable is False
    assert elicit.FIELD_META["household_income"].skippable is False


def test_boolean_and_age_fields_are_skippable():
    assert elicit.FIELD_META["has_kad_oku"].skippable is True
    assert elicit.FIELD_META["age"].skippable is True


def test_need_carries_metadata_for_rendering():
    need = elicit.next_field({"citizen": True}, set(), THRESHOLDS)  # age
    assert need.answer_kind == "integer"
    assert need.skippable is True
    assert need.choices == ()


def test_choice_field_exposes_choices():
    # Force marital_status to surface by deciding around it.
    known = {"citizen": True, "age": 40, "has_kad_oku": False, "is_carer": False,
             "individual_income": 100, "household_income": 100}
    # Walk until marital_status is offered or done.
    asked: set = set()
    field = None
    for _ in range(20):
        need = elicit.next_field(known, asked, THRESHOLDS)
        if need is None:
            break
        if need.field == "marital_status":
            field = need
            break
        asked.add(need.field)
    assert field is not None, "marital_status should be asked for STR category"
    assert field.answer_kind == "choice"
    assert set(field.choices) == {"single", "married", "widowed", "divorced"}


# --- progress signal -------------------------------------------------------------

def test_progress_counts_decided_and_undecided():
    prog = elicit.progress({}, set(), THRESHOLDS)
    assert prog["total"] == len(PROGRAMS)
    assert prog["undecided"] == len(PROGRAMS)   # nothing known -> all undecided
    assert prog["decided"] == 0
    assert prog["asked"] == 0


def test_progress_advances_as_facts_arrive():
    known = {"has_kad_oku": False, "is_carer": False, "str_approved": False,
             "ekasih_listed": False, "marital_status": "married",
             "has_dependents": False, "citizen": True,
             "individual_income": 9999, "household_income": 9999, "age": 40,
             "is_oku": False}
    prog = elicit.progress(known, {"age"}, THRESHOLDS)
    assert prog["decided"] == prog["total"]
    assert prog["undecided"] == 0
    assert prog["asked"] == 1


# --- value coercion at the API boundary ------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (True, True), (False, False),
    ("yes", True), ("no", False), ("true", True), ("false", False),
    ("1", True), ("0", False),
])
def test_coerce_boolean(raw, expected):
    assert elicit.coerce_value("is_oku", raw) is expected


def test_coerce_integer():
    assert elicit.coerce_value("age", "35") == 35
    assert elicit.coerce_value("age", 35) == 35


def test_coerce_money():
    assert elicit.coerce_value("individual_income", "1500") == 1500.0
    assert elicit.coerce_value("individual_income", "1500.50") == 1500.5


def test_coerce_choice_valid_and_invalid():
    assert elicit.coerce_value("marital_status", "married") == "married"
    with pytest.raises(ValueError):
        elicit.coerce_value("marital_status", "complicated")


def test_coerce_rejects_negative_amounts_and_ages():
    with pytest.raises(ValueError):
        elicit.coerce_value("individual_income", "-5")
    with pytest.raises(ValueError):
        elicit.coerce_value("age", "-1")


def test_coerce_rejects_unknown_field():
    with pytest.raises(ValueError):
        elicit.coerce_value("not_a_field", "x")


# --- materialization: facts -> validated Applicant -------------------------------

def test_to_applicant_builds_validated_profile():
    applicant = elicit.to_applicant({"age": 35, "is_oku": True, "has_kad_oku": True})
    assert isinstance(applicant, Applicant)
    assert applicant.age == 35
    assert applicant.is_oku is True


def test_to_applicant_reconciles_individual_over_household():
    # Mirror intake: a person's own income cannot exceed the household's.
    applicant = elicit.to_applicant(
        {"individual_income": 2000, "household_income": 1000})
    assert applicant.household_income == 2000


def test_to_applicant_ignores_unknown_keys_safely():
    applicant = elicit.to_applicant({"age": 20, "junk": "x"})
    assert applicant.age == 20


# --- boundary sanitisation of client-echoed facts --------------------------------

def test_sanitize_drops_unknown_keys():
    out = elicit.sanitize_facts({"age": 30, "evil": "drop me"})
    assert out == {"age": 30}


def test_sanitize_coerces_string_types_from_client():
    out = elicit.sanitize_facts(
        {"age": "30", "is_oku": "yes", "individual_income": "1500"})
    assert out == {"age": 30, "is_oku": True, "individual_income": 1500.0}


def test_sanitize_accepts_non_askable_extras_from_intake():
    out = elicit.sanitize_facts({"household_size": 4, "ekasih_category": "miskin"})
    assert out == {"household_size": 4, "ekasih_category": "miskin"}


def test_sanitize_rejects_malformed_value():
    with pytest.raises(ValueError):
        elicit.sanitize_facts({"age": "not-a-number"})
    with pytest.raises(ValueError):
        elicit.sanitize_facts({"household_size": 0})


# --- presumption sanitisation (LLM-proposed, user-vetoable soft facts) ------------

def test_presumptions_coerce_values_and_keep_reasons():
    out = elicit.sanitize_presumptions(
        {"marital_status": {"value": "single", "reason_ms": "berumur 12 tahun"},
         "is_working": {"value": "no", "reason_ms": "masih bersekolah"}},
        facts={})
    assert out == {
        "marital_status": {"value": "single", "reason_ms": "berumur 12 tahun"},
        "is_working": {"value": False, "reason_ms": "masih bersekolah"},
    }


def test_presumptions_never_include_money_fields():
    # A presumed income of 0 could manufacture a false ELIGIBLE — always ask money.
    out = elicit.sanitize_presumptions(
        {"individual_income": {"value": 0, "reason_ms": "kanak-kanak"},
         "household_income": {"value": 0, "reason_ms": "kanak-kanak"}}, facts={})
    assert out == {}


def test_presumptions_stated_facts_win():
    out = elicit.sanitize_presumptions(
        {"marital_status": {"value": "single", "reason_ms": "x"}},
        facts={"marital_status": "married"})
    assert out == {}


def test_presumptions_drop_unknown_fields_and_malformed_values():
    # Presumptions are optional hints: a bad one degrades to "ask normally", never 400s.
    out = elicit.sanitize_presumptions(
        {"bogus": {"value": True, "reason_ms": "x"},
         "age": {"value": "not-a-number", "reason_ms": "x"},
         "household_size": {"value": 4, "reason_ms": "non-askable"},
         "citizen": "not-a-dict"},
        facts={})
    assert out == {}


def test_with_presumed_merges_and_stated_wins():
    merged = elicit.with_presumed(
        {"age": 12},
        {"marital_status": {"value": "single", "reason_ms": "x"},
         "age": {"value": 30, "reason_ms": "ignored — stated wins"}})
    assert merged == {"age": 12, "marital_status": "single"}


def test_presumed_fact_suppresses_question_until_dismissed():
    # With marital_status presumed, the engine never asks it; dismissing the chip
    # (removing the key) makes the field UNKNOWN again so it returns to the queue.
    known = {"citizen": True, "age": 12}
    presumed = {"marital_status": {"value": "single", "reason_ms": "umur 12"},
                "has_dependents": {"value": False, "reason_ms": "umur 12"},
                "is_working": {"value": False, "reason_ms": "umur 12"}}
    asked: list[str] = []
    fields_seen = set()
    facts = elicit.with_presumed(known, presumed)
    need = elicit.next_field(facts, asked)
    while need is not None:
        fields_seen.add(need.field)
        asked.append(need.field)
        need = elicit.next_field(facts, asked)
    assert "marital_status" not in fields_seen
    assert "has_dependents" not in fields_seen

    # Chip dismissed -> field is genuinely unknown again and can be asked.
    without_chip = {k: v for k, v in presumed.items() if k != "marital_status"}
    fields_seen2 = set()
    asked2: list[str] = []
    facts2 = elicit.with_presumed(known, without_chip)
    need = elicit.next_field(facts2, asked2)
    while need is not None:
        fields_seen2.add(need.field)
        asked2.append(need.field)
        need = elicit.next_field(facts2, asked2)
    assert "marital_status" in fields_seen2


# --- absence-only presumption guard (widow-stereotype regression) ------------------

def test_presumptions_positive_values_are_dropped():
    # Stereotype class: "widow -> probably has kids", "has income -> working".
    # Positive facts must be STATED by the user, never presumed by the LLM.
    out = elicit.sanitize_presumptions(
        {"has_dependents": {"value": True, "reason_ms": "status janda"},
         "is_working": {"value": True, "reason_ms": "ada pendapatan"},
         "citizen": {"value": True, "reason_ms": "bercakap Melayu"}},
        facts={})
    assert out == {}


def test_presumptions_marital_status_may_only_be_single():
    out = elicit.sanitize_presumptions(
        {"marital_status": {"value": "married", "reason_ms": "x"}}, facts={})
    assert out == {}
    out = elicit.sanitize_presumptions(
        {"marital_status": {"value": "single", "reason_ms": "umur 12"}}, facts={})
    assert out == {"marital_status": {"value": "single", "reason_ms": "umur 12"}}


def test_presumptions_numeric_fields_cannot_be_presumed():
    # Numbers have no "absence" direction — a guessed age is just a guess.
    out = elicit.sanitize_presumptions(
        {"age": {"value": 30, "reason_ms": "x"}}, facts={})
    assert out == {}


def test_widow_regression_presumed_dependents_cannot_block_bujang():
    # The exact reported bug: intake presumed has_dependents=True for a widow,
    # which decided STR Bujang INELIGIBLE without ever asking. The guard must
    # drop it so the engine still asks the dependents question.
    presumed = elicit.sanitize_presumptions(
        {"has_dependents": {"value": True,
                            "reason_ms": "Diandaikan mempunyai tanggungan kerana status janda."}},
        facts={"marital_status": "widowed"})
    assert presumed == {}
    known = elicit.with_presumed({"marital_status": "widowed"}, presumed)
    asked: list[str] = []
    fields = set()
    need = elicit.next_field(known, asked)
    while need is not None:
        fields.add(need.field)
        asked.append(need.field)
        need = elicit.next_field(known, asked)
    assert "has_dependents" in fields          # the widow gets ASKED, not stereotyped
