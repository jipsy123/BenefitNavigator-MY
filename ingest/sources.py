"""Per-document metadata: human title, official source URL, and chunk strategy.

`doc_type` drives the chunker's locator strategy:
  faq   -> cite by question number   ("S{n}")
  akta  -> cite by section           ("Seksyen {n}")
  guide -> cite by nearest heading / ordinal (no page markers survived extraction)

URLs are the confirmed-downloadable official `.gov.my` sources (see corpus-fetcher/sources.json).
"""

DOCS: dict[str, dict] = {
    "jkm_garis_panduan_2018": {
        "title": "JKM Garis Panduan Pengurusan Bantuan Kewangan Persekutuan (2018)",
        "doc_type": "guide",
        "source_url": (
            "https://ebantuanjkm.jkm.gov.my/spbkDoc/Pautan/"
            "Pautan_Pd_29.06.2018%20GARIS%20PANDUAN%20PENGURUSAN%20"
            "BANTUAN%20KEWANGAN%20PERSEKUTUAN%20JKM.pdf"
        ),
    },
    "perkeso_booklet_2025": {
        "title": "PERKESO 2025 Booklet — Guidelines on Social Security Protection",
        "doc_type": "guide",
        "source_url": "https://perkeso.gov.my/images/dokumen/risalah/2025-BOOKLET_PERKESO_BI.pdf",
    },
    "lhdn_str_application_faq_2026": {
        "title": "LHDN STR 2026 — FAQ Permohonan",
        "doc_type": "faq",
        "source_url": "https://bantuantunai.hasil.gov.my/FAQ/FAQ%20PERMOHONAN%20STR%202026.pdf",
    },
    "lhdn_str_payment_faq_2026": {
        "title": "LHDN STR 2026 — FAQ Pembayaran",
        "doc_type": "faq",
        "source_url": "https://bantuantunai.hasil.gov.my/FAQ/FAQ%20PEMBAYARAN%20STR%202026.pdf",
    },
    "lhdn_sara_faq_2026": {
        "title": "LHDN SARA 2026 — FAQ Sumbangan Asas Rahmah",
        "doc_type": "faq",
        "source_url": (
            "https://bantuantunai.hasil.gov.my/FAQ/"
            "FAQ%20SUMBANGAN%20ASAS%20RAHMAH%20(SARA)%202026.pdf"
        ),
    },
    "akta_oku_2008_act685": {
        "title": "Akta Orang Kurang Upaya 2008 (Akta 685)",
        "doc_type": "akta",
        "source_url": (
            "https://lom.agc.gov.my/ilims/upload/portal/akta/outputaktap/"
            "Salinan%20Warta%20Akta%20685.pdf"
        ),
    },
}
