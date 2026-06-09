"""APPEAL stage — draft a formal Bahasa Melayu surat rayuan for one program.

Grounded in the program's gazetted rule (citation) + the applicant's stated facts.
Personal identifiers are left as placeholders ([Nama], [No. MyKad]) — never invented,
never a real NRIC. The same amount guard as the narrative applies.
"""
from __future__ import annotations

from dataclasses import dataclass

from compute import checker
from compute.profile import Applicant

from . import intake, llm, verify

# Where an appeal actually goes — the correct track per agency.
_ROUTING_MS = {
    "JKM": ("Hantar surat rayuan ke Pejabat Kebajikan Masyarakat (JKM) daerah tempat "
            "permohonan dibuat, atau melalui sistem eBantuan JKM. Sertakan dokumen "
            "sokongan (salinan Kad OKU, pengesahan pendapatan, laporan perubatan)."),
    "LHDN": ("Buat rayuan STR/SARA melalui portal rasmi MyHASiL (bahagian Rayuan) dalam "
             "tempoh rayuan yang dibenarkan, atau di cawangan LHDN berhampiran."),
}

_APPEAL_SYSTEM = """Anda menulis SURAT RAYUAN rasmi dalam Bahasa Melayu bagi pemohon bantuan kerajaan Malaysia.

Anda diberi: nama bantuan, agensi, syarat rasmi (dengan sumber), syarat yang DIPENUHI dan BELUM dipenuhi oleh pemohon, dan fakta pemohon.

PERATURAN KETAT:
- Gunakan HANYA fakta yang diberikan. Jangan reka fakta peribadi, jumlah, atau syarat baharu.
- Untuk butiran peribadi yang tiada, guna ruang isi seperti [Nama Penuh], [No. MyKad], [Alamat], [Tarikh]. JANGAN reka nombor MyKad.
- Rujuk syarat kelayakan rasmi dengan menyebut sumbernya (cth: merujuk Garis Panduan JKM, 6.3 BTB).
- Hujah rayuan mesti berdasarkan syarat yang pemohon SUDAH penuhi, dan menerangkan langkah untuk syarat yang belum dipenuhi jika relevan.
- Nada hormat dan formal. Struktur: tarikh & alamat penerima (ruang isi), tajuk (RAYUAN ...), perenggan pembuka, latar belakang pemohon, asas rayuan (rujuk syarat + sumber), permohonan pertimbangan, penutup, ruang tandatangan.
- Jangan minta nombor MyKad penuh daripada sesiapa.
- JANGAN guna pemformatan markdown (tiada *, **, #) atau emoji. Tulis surat biasa sahaja.

Hasilkan teks surat sahaja (plain text) dalam Bahasa Melayu."""


@dataclass(frozen=True)
class AppealLetter:
    program_id: str
    program_name_ms: str
    agency: str
    body_ms: str
    routing_ms: str
    citation: dict
    grounded: bool


def _find_program(applicant: Applicant, program_id: str) -> checker.ProgramResult:
    for result in checker.assess(applicant):
        if result.program_id == program_id:
            return result
    raise ValueError(f"unknown program_id: {program_id!r}")


def _context(result: checker.ProgramResult, applicant: Applicant) -> str:
    met = "; ".join(c.label_ms for c in result.met) or "—"
    unmet = "; ".join(c.label_ms for c in result.unmet) or "tiada (semua syarat dipenuhi)"
    amount = result.amount
    amt = (f"RM{amount['monthly_myr']} sebulan" if amount.get("type") == "fixed"
           else f"RM{amount.get('monthly_myr_min')}–RM{amount.get('monthly_myr_max')} sebulan")
    cite = f"{result.citation.get('doc_title')}, {result.citation['locator']}"
    facts = (f"OKU={applicant.is_oku}, Kad OKU={applicant.has_kad_oku}, "
             f"tidak berupaya bekerja={applicant.unable_to_work}, bekerja={applicant.is_working}, "
             f"umur={applicant.age}, status={applicant.marital_status}, "
             f"pendapatan individu=RM{applicant.individual_income:.0f}, "
             f"pendapatan isi rumah=RM{applicant.household_income:.0f}")
    return (f"Bantuan: {result.name_ms} ({result.agency}) — {amt}.\n"
            f"Sumber syarat: {cite}\n"
            f"Syarat DIPENUHI: {met}\n"
            f"Syarat BELUM dipenuhi: {unmet}\n"
            f"Fakta pemohon: {facts}")


def draft(user_text: str, program_id: str) -> AppealLetter:
    intake_result = intake.run_intake(user_text)
    result = _find_program(intake_result.applicant, program_id)
    body = llm.chat_text(_APPEAL_SYSTEM, _context(result, intake_result.applicant),
                         temperature=0.3, max_tokens=900)

    thresholds = checker.load_thresholds()
    allowed = verify.allowed_amounts(intake_result.applicant, thresholds)
    amounts_ok, _ = verify.verify_amounts(body, allowed)

    return AppealLetter(
        program_id=result.program_id,
        program_name_ms=result.name_ms,
        agency=result.agency,
        body_ms=body,
        routing_ms=_ROUTING_MS.get(result.agency, "Hubungi agensi berkaitan untuk prosedur rayuan."),
        citation=result.citation,
        grounded=amounts_ok,
    )
