"""Deterministic "what we still don't know" disclosure for the grill path.

After the interview, the assumptions shown to the user must reflect the facts the
grill actually gathered — not the one-shot list the INTAKE LLM produced from the
opening paragraph (which the grill then went on to clarify). This derives the
disclosure from the established `known` facts, so a field the user answered is never
reported as "not specified", and the output is stable run-to-run (no LLM).

A group is disclosed only when *every* field it covers is still absent from `known`:
answering any one field in a group means the user told us about that area. Phrasing is
Bahasa Melayu (the pipeline's canonical language); localize translates it for display
like every other assumption line.
"""
from __future__ import annotations

from typing import Mapping

# Ordered to match the assumptions card's established layout. Each entry pairs the
# Applicant fields that cover one disclosure with its Malay sentence. `age` is
# deliberately not disclosed (it has no card line and the grill almost always asks it).
_GROUPS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("citizen",),
     "Kewarganegaraan Malaysia tidak dinyatakan."),
    (("marital_status",),
     "Status perkahwinan tidak dinyatakan."),
    (("is_oku", "has_kad_oku"),
     "Maklumat tentang OKU atau Kad OKU tidak dinyatakan."),
    (("is_working", "individual_income", "household_income", "unable_to_work", "is_carer"),
     "Maklumat tentang pekerjaan atau pendapatan tidak dinyatakan."),
    (("has_dependents", "household_size"),
     "Maklumat tentang tanggungan atau ahli isi rumah tidak dinyatakan."),
    (("str_approved", "ekasih_listed", "ekasih_category"),
     "Maklumat tentang status STR atau eKasih tidak dinyatakan."),
)


def unspecified_ms(known: Mapping[str, object]) -> tuple[str, ...]:
    """Malay disclosure lines for every area the grill still has no fact about.

    `known` is the grill's established view — stated facts plus surviving presumptions,
    i.e. `elicit.with_presumed(...)`. A group is disclosed only when all of its fields
    are absent, so anything the user clarified drops out of the list.
    """
    return tuple(sentence for fields, sentence in _GROUPS
                 if all(field not in known for field in fields))
