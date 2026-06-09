"""Tests for agent.readability — Malay readability scorer and simplify loop.

Syllable rule in use: maximal contiguous vowel-group (a, e, i, o, u) = 1 syllable.
Consecutive vowels collapse to one run (e.g. 'ua' in 'bantuan' is 1 run).
'y' is treated as a consonant. Minimum 1 for any alphabetic word, 0 for non-alpha.

Verified expected values (computed from the implementation's vowel-group rule):
    saya     -> [a] y [a]       -> 2 runs -> 2
    makan    -> [a] k [a] n     -> 2 runs -> 2
    sekolah  -> [e] k [o] l [a] h -> 3 runs -> 3
    bantuan  -> [a] nt [ua] n   -> 2 runs -> 2   (ua collapses to 1 run)
    kewangan -> [e] w [a] ng [a] n -> 3 runs -> 3
    di       -> [i]             -> 1 run  -> 1
    nth      -> (no vowels)     -> 0 runs -> floor 1

Grade formula: 0.39*avg_wps + 11.8*avg_spw - 15.59, clamped >= 0.

Digit tokens count as words but contribute 0 syllables (chosen policy, tested below).
"""
from __future__ import annotations

import math

from agent.readability import (
    ReadabilityMetrics,
    count_syllables_ms,
    grade_level,
    is_readable,
    metrics,
    simplify,
)


# ---------------------------------------------------------------------------
# count_syllables_ms
# ---------------------------------------------------------------------------


class TestCountSyllablesMs:
    """Tests for the vowel-group syllable counting rule."""

    def test_saya_two_syllables(self) -> None:
        # s-[a]-y-[a]: two separate vowel runs
        assert count_syllables_ms("saya") == 2

    def test_makan_two_syllables(self) -> None:
        # m-[a]-k-[a]-n: two vowel runs
        assert count_syllables_ms("makan") == 2

    def test_sekolah_three_syllables(self) -> None:
        # s-[e]-k-[o]-l-[a]-h: three vowel runs
        assert count_syllables_ms("sekolah") == 3

    def test_bantuan_two_syllables(self) -> None:
        # b-[a]-nt-[ua]-n: 'ua' collapses to a single vowel run -> 2
        assert count_syllables_ms("bantuan") == 2

    def test_kewangan_three_syllables(self) -> None:
        # k-[e]-w-[a]-ng-[a]-n: three separate vowel runs -> 3
        assert count_syllables_ms("kewangan") == 3

    def test_single_syllable_di(self) -> None:
        # d-[i]: one vowel run
        assert count_syllables_ms("di") == 1

    def test_no_vowels_floor_one(self) -> None:
        # 'nth' has no vowels but is alphabetic -> floor to 1
        assert count_syllables_ms("nth") == 1

    def test_empty_string_returns_zero(self) -> None:
        # No alphabetic character -> 0 (below the alphabetic floor)
        assert count_syllables_ms("") == 0

    def test_pure_digit_returns_zero(self) -> None:
        # No alphabetic character -> 0
        assert count_syllables_ms("123") == 0

    def test_mixed_case_normalised(self) -> None:
        # Case should not affect the count
        assert count_syllables_ms("Saya") == count_syllables_ms("saya")
        assert count_syllables_ms("MAKAN") == 2

    def test_y_treated_as_consonant(self) -> None:
        # 'saya': s-[a]-y-[a] -> 2 (y does not merge the two 'a' runs)
        assert count_syllables_ms("saya") == 2


# ---------------------------------------------------------------------------
# metrics and grade_level — basic correctness
# ---------------------------------------------------------------------------


class TestMetrics:
    """Tests for the metrics() and grade_level() functions."""

    def test_returns_readability_metrics_instance(self) -> None:
        result = metrics("Dia ada di sini.")
        assert isinstance(result, ReadabilityMetrics)

    def test_word_count(self) -> None:
        # 4 alphabetic words
        m = metrics("Dia ada di sini.")
        assert m.words == 4

    def test_sentence_count_single(self) -> None:
        m = metrics("Dia ada di sini.")
        assert m.sentences == 1

    def test_sentence_count_multiple(self) -> None:
        m = metrics("Dia ada di sini. Saya makan nasi.")
        assert m.sentences == 2

    def test_syllable_count(self) -> None:
        # dia=1, ada=2, di=1, sini=2 -> total=6
        m = metrics("Dia ada di sini.")
        assert m.syllables == 6

    def test_complex_words_count(self) -> None:
        # sekolah=3 syllables (complex), di=1, ada=2 (not complex)
        m = metrics("Di sekolah ada buku.")
        assert m.complex_words == 1

    def test_avg_syllables_per_word(self) -> None:
        # dia=1, ada=2, di=1, sini=2 -> total=6, words=4 -> avg=1.5
        m = metrics("Dia ada di sini.")
        assert math.isclose(m.avg_syllables_per_word, 1.5, rel_tol=1e-6)

    def test_avg_words_per_sentence(self) -> None:
        m = metrics("Dia ada di sini.")
        assert math.isclose(m.avg_words_per_sentence, 4.0, rel_tol=1e-6)

    def test_grade_positive_for_normal_text(self) -> None:
        g = grade_level("Dia ada di sini.")
        assert g > 0.0

    def test_grade_is_clamped_to_zero(self) -> None:
        # A single, very short word might produce a raw negative grade
        g = grade_level("di")
        assert g >= 0.0

    def test_digit_tokens_count_as_words_zero_syllables(self) -> None:
        # "Saya ada 3 epal." -> words=4 (Saya, ada, 3, epal), syllables=6 (0 from 3)
        m = metrics("Saya ada 3 epal.")
        assert m.words == 4
        assert m.syllables == 6  # Saya=2, ada=2, 3=0, epal=2

    def test_sentence_split_on_question_mark(self) -> None:
        m = metrics("Siapa dia? Dia ada di sini.")
        assert m.sentences == 2

    def test_sentence_split_on_newline(self) -> None:
        m = metrics("Dia ada.\nSaya ada.")
        assert m.sentences == 2


# ---------------------------------------------------------------------------
# Simple vs. complex grade comparison
# ---------------------------------------------------------------------------


class TestGradeComparison:
    """Simple text must score lower than complex text."""

    def test_simple_sentence_lower_grade_than_complex(self) -> None:
        simple = "Dia ada di sini."         # grade ~3.67
        complex_text = (
            "Saya perlu bantuan kewangan kerana pendapatan saya tidak mencukupi "
            "untuk membayar bil-bil bulanan yang semakin meningkat."
        )  # grade ~19.5
        assert grade_level(simple) < grade_level(complex_text)

    def test_grade_values_in_expected_range(self) -> None:
        simple = "Dia ada di sini."
        assert grade_level(simple) < 6.0
        complex_text = (
            "Saya memerlukan bantuan kewangan kerana pendapatan saya tidak mencukupi."
        )
        assert grade_level(complex_text) > 10.0


# ---------------------------------------------------------------------------
# is_readable
# ---------------------------------------------------------------------------


class TestIsReadable:
    """Tests for the is_readable() function."""

    def test_simple_sentence_is_readable(self) -> None:
        # "Dia ada di sini." grades ~3.67 < 6.0
        assert is_readable("Dia ada di sini.") is True

    def test_complex_paragraph_not_readable(self) -> None:
        complex_text = (
            "Saya perlu bantuan kewangan kerana pendapatan saya tidak mencukupi "
            "untuk membayar bil-bil bulanan yang semakin meningkat."
        )
        assert is_readable(complex_text) is False

    def test_custom_max_grade(self) -> None:
        text = "Dia ada di sini."  # grade ~3.67
        assert is_readable(text, max_grade=4.0) is True
        assert is_readable(text, max_grade=3.0) is False

    def test_empty_string_is_readable(self) -> None:
        # Empty text returns grade 0.0, so always readable
        assert is_readable("") is True

    def test_exact_boundary(self) -> None:
        # is_readable uses <=, so grade == max_grade should be True
        text = "Dia ada di sini."
        g = grade_level(text)
        assert is_readable(text, max_grade=g) is True
        assert is_readable(text, max_grade=g - 0.01) is False


# ---------------------------------------------------------------------------
# Empty / whitespace edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Ensure empty and whitespace inputs do not crash and return sensible values."""

    def test_empty_string_metrics(self) -> None:
        m = metrics("")
        assert m.words == 0
        assert m.sentences == 0
        assert m.syllables == 0
        assert m.complex_words == 0
        assert m.avg_words_per_sentence == 0.0
        assert m.avg_syllables_per_word == 0.0
        assert m.grade == 0.0

    def test_whitespace_only_metrics(self) -> None:
        m = metrics("   \t\n  ")
        assert m.words == 0
        assert m.grade == 0.0

    def test_empty_string_grade_level(self) -> None:
        assert grade_level("") == 0.0

    def test_whitespace_only_grade_level(self) -> None:
        assert grade_level("   ") == 0.0

    def test_single_word_no_crash(self) -> None:
        # Single word, no sentence terminator -> 1 sentence (min)
        m = metrics("bantuan")
        assert m.words == 1
        assert m.sentences == 1
        assert m.grade >= 0.0


# ---------------------------------------------------------------------------
# simplify
# ---------------------------------------------------------------------------


class TestSimplify:
    """Tests for the simplify() loop."""

    # Complex seed text for simplify tests
    _COMPLEX = (
        "Saya perlu bantuan kewangan kerana pendapatan saya tidak mencukupi "
        "untuk membayar bil-bil bulanan."
    )

    def test_improving_rewrite_reaches_target(self) -> None:
        """A fake rewrite that progressively shortens text should reach target."""
        stages = iter([
            "Saya ada masalah wang.",
            "Saya perlukan wang.",
            "Dia di sini.",  # grade ~1.31 — well below 6.0
        ])

        def improving_rewrite(text: str, grade: float) -> str:
            return next(stages)

        result_text, result_grade = simplify(
            self._COMPLEX, improving_rewrite, target_grade=6.0, max_rounds=3
        )
        assert result_grade <= 6.0

    def test_worsening_rewrite_returns_best_original(self) -> None:
        """If every rewrite makes text worse, simplify returns the original.

        The 'worse' texts must have a higher grade than the original.
        Short sentences packed with polysyllabic words achieve this.
        Verified: original ~18.5, worse1 ~25.9, worse2 ~32.7.
        """
        worse_texts = iter([
            # grade ~25.9 — higher than original ~18.5
            "Saya memerlukan penjelasan berkenaan permohonan program bantuan kewangan berkelanjutan.",
            # grade ~32.7 — even higher
            "Keberkesanan pelaksanaan program pembangunan infrastruktur berteraskan teknologi.",
        ])

        def worsening_rewrite(text: str, grade: float) -> str:
            return next(worse_texts)

        original_grade = grade_level(self._COMPLEX)
        result_text, result_grade = simplify(
            self._COMPLEX, worsening_rewrite, target_grade=6.0, max_rounds=3
        )
        assert result_text == self._COMPLEX
        assert math.isclose(result_grade, original_grade, rel_tol=1e-9)

    def test_already_readable_does_not_call_rewrite(self) -> None:
        """If text already meets target_grade, rewrite is never called."""
        call_count: list[int] = [0]

        def counting_rewrite(text: str, grade: float) -> str:
            call_count[0] += 1
            return text

        simple_text = "Dia di sini."  # grade ~1.31 < 6.0
        result_text, result_grade = simplify(
            simple_text, counting_rewrite, target_grade=6.0, max_rounds=3
        )
        assert call_count[0] == 0
        assert result_text == simple_text
        assert result_grade <= 6.0

    def test_rounds_exhausted_returns_best(self) -> None:
        """When max_rounds is exhausted without reaching target, best is returned."""
        # Rewrite consistently improves but never enough
        grades_sequence = iter(["Saya ada wang sedikit.", "Saya ada wang."])

        def partial_rewrite(text: str, grade: float) -> str:
            try:
                return next(grades_sequence)
            except StopIteration:
                return text

        result_text, result_grade = simplify(
            self._COMPLEX, partial_rewrite, target_grade=0.5, max_rounds=2
        )
        # Grade should be better than the original even if target not reached
        assert result_grade < grade_level(self._COMPLEX)

    def test_simplify_empty_string(self) -> None:
        """Empty input should not crash."""
        def noop_rewrite(text: str, grade: float) -> str:
            return text

        result_text, result_grade = simplify("", noop_rewrite, target_grade=6.0)
        assert result_text == ""
        assert result_grade == 0.0

    def test_max_rounds_zero(self) -> None:
        """With max_rounds=0, no rewrite calls occur and original is returned."""
        call_count: list[int] = [0]

        def counting_rewrite(text: str, grade: float) -> str:
            call_count[0] += 1
            return text

        result_text, result_grade = simplify(
            self._COMPLEX, counting_rewrite, target_grade=6.0, max_rounds=0
        )
        assert call_count[0] == 0
        assert result_text == self._COMPLEX

    def test_noop_rewrite_on_non_readable_stops_after_one_call(self) -> None:
        """A no-op rewrite (returns same text) on non-readable text stops early.

        The round produces no improvement, so simplify should return after
        exactly one rewrite call rather than exhausting all max_rounds.
        """
        call_count: list[int] = [0]

        def noop_rewrite(text: str, grade: float) -> str:
            call_count[0] += 1
            return text  # returns identical text — no improvement

        result_text, result_grade = simplify(
            self._COMPLEX, noop_rewrite, target_grade=6.0, max_rounds=5
        )
        assert call_count[0] == 1
        assert result_text == self._COMPLEX
