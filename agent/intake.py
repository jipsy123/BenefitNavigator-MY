"""INTAKE stage — parse a free-text Malay description into a validated Applicant.

The LLM extracts *facts only*; it must not judge eligibility. Output is validated
through compute.profile.from_dict so a bad extraction fails loudly at the boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from compute.profile import Applicant, from_dict

from . import llm

_INTAKE_SYSTEM = """Anda ialah peringkat INTAKE bagi pembantu bantuan kerajaan untuk rakyat Malaysia.
Tugas anda: cabut FAKTA berstruktur daripada perihal pengguna. JANGAN menilai kelayakan.

Hasilkan JSON sahaja dengan kunci: "profile", "presumed", "assumptions_ms", "retrieval_query_ms".

"profile" boleh mengandungi kunci berikut (abaikan yang tidak dinyatakan):
- citizen (bool) — warganegara Malaysia
- age (int) — umur
- marital_status ("single"|"married"|"widowed"|"divorced")
- is_oku (bool) — orang kurang upaya
- has_kad_oku (bool) — memegang Kad OKU JKM yang berdaftar
- unable_to_work (bool) — tidak berupaya bekerja
- is_working (bool) — sedang bekerja
- is_carer (bool) — penjaga sepenuh masa OKU/pesakit terlantar
- has_dependents (bool) — mempunyai anak atau tanggungan
- individual_income (number) — pendapatan BULANAN individu sendiri (RM)
- household_income (number) — pendapatan BULANAN seisi rumah (RM)
- household_size (int) — bilangan ahli isi rumah
- str_approved (bool) — permohonan STR telah diluluskan
- ekasih_listed (bool) — tersenarai dalam eKasih
- ekasih_category ("miskin_tegar"|"miskin"|null)

"presumed" (pilihan): fakta yang TIDAK dinyatakan tetapi hampir pasti benar
berdasarkan apa yang pengguna ceritakan (cth. umur 12 tahun -> belum berkahwin,
tiada anak, tidak bekerja). Format setiap entri:
  {nama_medan: {"value": <nilai>, "reason_ms": "<ayat ringkas Bahasa Melayu yang
   menyatakan andaian DAN sebabnya, cth. 'Diandaikan belum berkahwin kerana berumur
   12 tahun'>"}}
PERATURAN "presumed":
- Hanya jika hampir pasti benar (melampaui keraguan munasabah). Jika ragu, JANGAN masukkan.
- JANGAN sekali-kali presumed individual_income atau household_income — sentiasa perlu ditanya.
- JANGAN masukkan medan yang sudah ada dalam "profile" atau bercanggah dengannya.

PERATURAN:
- Cabut hanya fakta yang dinyatakan atau tersirat dengan jelas dalam "profile". Jangan reka.
- individual_income = pendapatan sendiri; household_income = seisi rumah. Bezakan dengan teliti.
- "assumptions_ms": senaraikan andaian/maklumat penting yang tiada (Bahasa Melayu).
- "retrieval_query_ms": satu ayat carian ringkas Bahasa Melayu tentang situasi ini."""


@dataclass(frozen=True)
class IntakeResult:
    applicant: Applicant
    assumptions_ms: tuple[str, ...]
    retrieval_query_ms: str
    facts: dict           # pristine extracted facts (only stated keys) — the grill seed
    # LLM-proposed soft facts {field: {value, reason_ms}} — pristine; validated by
    # elicit.sanitize_presumptions at the API boundary.
    presumed: dict = field(default_factory=dict)


def run_intake(user_text: str) -> IntakeResult:
    data = llm.chat_json(_INTAKE_SYSTEM, user_text)

    allowed = set(Applicant.__dataclass_fields__)  # type: ignore[attr-defined]
    raw = data.get("profile", {}) or {}
    # `facts` = exactly what the model stated. Kept pristine so the grill can treat
    # unstated fields as UNKNOWN (and ask) rather than as their favourable defaults.
    facts = {k: v for k, v in raw.items() if k in allowed and v is not None}

    # For the one-shot Applicant only: a person's own income cannot exceed the
    # household's; reconcile a partial extraction so validation accepts it. This
    # injected value is deliberately NOT part of `facts` (see above).
    profile = dict(facts)
    indiv = float(profile.get("individual_income", 0) or 0)
    house = float(profile.get("household_income", 0) or 0)
    if indiv > house:
        profile["household_income"] = indiv

    applicant = from_dict(profile)
    # The model is asked for a list, but sometimes returns a single string; wrapping
    # avoids tuple("text") exploding it into one-character "assumptions".
    raw_assumptions = data.get("assumptions_ms") or []
    if isinstance(raw_assumptions, str):
        raw_assumptions = [raw_assumptions]
    assumptions = tuple(str(a) for a in raw_assumptions)
    query = data.get("retrieval_query_ms") or user_text
    raw_presumed = data.get("presumed")
    presumed = dict(raw_presumed) if isinstance(raw_presumed, dict) else {}
    return IntakeResult(applicant=applicant, assumptions_ms=assumptions,
                        retrieval_query_ms=query, facts=facts, presumed=presumed)
