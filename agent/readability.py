"""Malay (Bahasa Melayu) readability estimator.

Syllable rule: a maximal contiguous run of vowels (a, e, i, o, u — y excluded)
counts as exactly one syllable. Any alphabetic word with zero vowel runs gets
a floor of 1.  Pure-digit tokens are counted as words but contribute 0 syllables.

Grade formula: Flesch-Kincaid-style linear blend
    grade = 0.39 * avg_words_per_sentence + 11.8 * avg_syllables_per_word - 15.59
Clamped to >= 0.0.  Empty / whitespace text returns 0.0.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VOWELS: frozenset[str] = frozenset("aeiou")

# Tokenise runs of word characters (letters, digits, apostrophe).
_WORD_RE = re.compile(r"[A-Za-z']+|\d+")

# Sentence boundaries: period, question mark, exclamation mark, newline.
_SENTENCE_SPLIT_RE = re.compile(r"[.?!\n]+")

# FK-style coefficients
_COEFF_SENTENCE = 0.39
_COEFF_SYLLABLE = 11.8
_INTERCEPT = 15.59


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadabilityMetrics:
    """Immutable container for readability statistics of a piece of text."""

    words: int
    sentences: int
    syllables: int
    complex_words: int  # words with >= 3 syllables
    avg_words_per_sentence: float
    avg_syllables_per_word: float
    grade: float  # estimated reading grade level (lower = easier)


# ---------------------------------------------------------------------------
# Syllable counting
# ---------------------------------------------------------------------------


def count_syllables_ms(word: str) -> int:
    """Count syllables in a Malay word using the vowel-group rule.

    A maximal contiguous run of vowels (a, e, i, o, u) counts as one syllable.
    Consecutive vowels (e.g. 'ua' in 'bantuan', 'ai' in 'pandai') are treated
    as a single run and thus one syllable.  'y' is treated as a consonant.

    Minimum return value for any alphabetic word is 1.
    Returns 0 for empty string or a string with no alphabetic characters.

    Examples (vowel-group rule output — NOT phonemic syllabification):
        saya     -> s-[a]-y-[a]      -> 2 runs -> 2
        makan    -> m-[a]-k-[a]-n    -> 2 runs -> 2
        sekolah  -> s-[e]-k-[o]-l-[a]-h -> 3 runs -> 3
        bantuan  -> b-[a]-nt-[ua]-n  -> 2 runs -> 2   (ua collapses to 1)
        kewangan -> k-[e]-w-[a]-ng-[a]-n -> 3 runs -> 3

    Args:
        word: A single word string (may contain mixed case).

    Returns:
        Integer syllable count >= 0.
    """
    word_lower = word.lower()
    has_alpha = any(ch.isalpha() for ch in word_lower)

    if not has_alpha:
        return 0

    count = 0
    in_vowel_run = False
    for ch in word_lower:
        if ch in _VOWELS:
            if not in_vowel_run:
                count += 1
                in_vowel_run = True
        else:
            in_vowel_run = False

    # Floor: every alphabetic word has at least 1 syllable.
    return max(count, 1)


# ---------------------------------------------------------------------------
# Tokenisation helpers
# ---------------------------------------------------------------------------


def _tokenise_words(text: str) -> list[str]:
    """Return all word tokens (alphabetic sequences and digit sequences).

    Pure-digit tokens are included so they are counted as words but will
    contribute 0 syllables via count_syllables_ms.

    Args:
        text: Raw input text.

    Returns:
        List of word tokens.
    """
    return _WORD_RE.findall(text)


def _count_sentences(text: str, has_words: bool) -> int:
    """Count the number of sentences in *text*.

    Splits on '.', '?', '!', and newlines.  Returns at least 1 if *has_words*
    is True, to avoid zero-division.

    Args:
        text:      Raw input text.
        has_words: Whether the text contains at least one word token.

    Returns:
        Integer sentence count.
    """
    if not has_words:
        return 0
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    return max(len(parts), 1)


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def metrics(text: str) -> ReadabilityMetrics:
    """Compute readability metrics for *text*.

    Handles empty/whitespace input gracefully: returns a zeroed metric object
    with grade 0.0.

    Args:
        text: Input text in Bahasa Melayu (or any language).

    Returns:
        A frozen :class:`ReadabilityMetrics` instance.
    """
    tokens = _tokenise_words(text)
    # Separate alphabetic word tokens from digit-only tokens.
    alpha_tokens = [t for t in tokens if any(ch.isalpha() for ch in t)]
    all_word_count = len(tokens)  # digits count as words per chosen policy

    has_words = all_word_count > 0
    sentence_count = _count_sentences(text, has_words)

    # Syllable counts: alphabetic tokens use count_syllables_ms; digits => 0.
    syllable_counts = [
        count_syllables_ms(t) if any(ch.isalpha() for ch in t) else 0
        for t in tokens
    ]
    total_syllables = sum(syllable_counts)

    # Complex words: alphabetic words with >= 3 syllables.
    complex_count = sum(
        1 for t in alpha_tokens if count_syllables_ms(t) >= 3
    )

    if has_words:
        avg_wps = all_word_count / sentence_count
        avg_spw = total_syllables / all_word_count
    else:
        avg_wps = 0.0
        avg_spw = 0.0

    grade = _compute_grade(avg_wps, avg_spw)

    return ReadabilityMetrics(
        words=all_word_count,
        sentences=sentence_count,
        syllables=total_syllables,
        complex_words=complex_count,
        avg_words_per_sentence=avg_wps,
        avg_syllables_per_word=avg_spw,
        grade=grade,
    )


def _compute_grade(avg_wps: float, avg_spw: float) -> float:
    """Apply the FK-style grade formula and clamp to >= 0.

    Args:
        avg_wps: Average words per sentence.
        avg_spw: Average syllables per word.

    Returns:
        Grade level float, clamped to 0.0 minimum.
    """
    raw = _COEFF_SENTENCE * avg_wps + _COEFF_SYLLABLE * avg_spw - _INTERCEPT
    return max(raw, 0.0)


def grade_level(text: str) -> float:
    """Return the estimated reading grade level for *text*.

    Uses a Flesch-Kincaid-style formula:
        grade = 0.39 * avg_words_per_sentence + 11.8 * avg_syllables_per_word - 15.59

    Result is clamped to >= 0.0.  Empty or whitespace-only text returns 0.0.

    Args:
        text: Input text.

    Returns:
        Grade level as a non-negative float.
    """
    return metrics(text).grade


def is_readable(text: str, max_grade: float = 6.0) -> bool:
    """Return True if *text*'s grade level is at or below *max_grade*.

    Args:
        text:      Input text.
        max_grade: Maximum acceptable grade level (default 6.0).

    Returns:
        True if grade_level(text) <= max_grade.
    """
    return grade_level(text) <= max_grade


def simplify(
    text: str,
    rewrite: Callable[[str, float], str],
    *,
    target_grade: float = 6.0,
    max_rounds: int = 3,
) -> tuple[str, float]:
    """Iteratively rewrite *text* to lower its readability grade.

    Calls ``rewrite(current_text, current_grade)`` each round.  Stops when:
    - grade <= target_grade, OR
    - *max_rounds* have been exhausted, OR
    - a round fails to improve the grade (returns best seen so far).

    The *rewrite* callable is injected; this function never makes network calls.

    Args:
        text:         Input text to simplify.
        rewrite:      Callable ``(text, grade) -> new_text``.
        target_grade: Desired maximum grade (default 6.0).
        max_rounds:   Maximum number of rewrite attempts (default 3).

    Returns:
        Tuple of (best_text, best_grade).
    """
    current_text = text
    current_grade = grade_level(text)

    # Track the best version seen (initialise with the original).
    best_text = current_text
    best_grade = current_grade

    # Short-circuit: already at target.
    if current_grade <= target_grade:
        return best_text, best_grade

    for _ in range(max_rounds):
        new_text = rewrite(current_text, current_grade)
        new_grade = grade_level(new_text)

        improved = new_grade < best_grade
        if improved:
            best_text = new_text
            best_grade = new_grade

        # Advance current for next round.
        current_text = new_text
        current_grade = new_grade

        if best_grade <= target_grade:
            return best_text, best_grade

        # Stop if this round did not improve (covers no-ops, ties, worsening).
        if not improved:
            return best_text, best_grade

    return best_text, best_grade
