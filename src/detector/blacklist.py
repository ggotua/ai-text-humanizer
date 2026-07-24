"""
Blacklist phrase detection for the detector component (SPEC-001 §4a).

Provides lemma-based matching for Russian (via natasha) and literal
case-insensitive substring matching for English, both with explicit
placeholder-token exclusion.
"""

import re

from src.detector.models import RuleMatch, Severity, PLACEHOLDER_TOKEN_PATTERN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(PLACEHOLDER_TOKEN_PATTERN)


def _tokenize_and_lemmatize_ru(
    text: str,
) -> list[tuple[str, str, int, int]]:
    """Tokenise and lemmatise *text* via natasha.

    Returns an ordered list of ``(surface_form, lemma, char_start, char_end)``
    for every real-word token.  Placeholder tokens (``[[FN:n]]`` /
    ``[[EN:n]]``) are **excluded** from the result entirely — they contribute
    no lemma and do not break a multi-word blacklist match spanning around
    them, since they simply are not part of the sequence being matched
    against.

    Parameters
    ----------
    text:
        Raw input text that may contain placeholder tokens.

    Returns
    -------
    A list of ``(surface, lemma, start, end)`` tuples for real tokens only.
    """
    from natasha import (
        MorphVocab,
        NewsEmbedding,
        NewsMorphTagger,
        Segmenter,
        Doc,
    )

    emb = NewsEmbedding()
    morph_vocab = MorphVocab()
    seg = Segmenter()
    morph_tagger = NewsMorphTagger(emb)

    doc = Doc(text)
    doc.segment(seg)
    doc.tag_morph(morph_tagger)

    # Pre-compute placeholder token spans so we can filter out any natasha
    # token that falls inside one.  Doing this by span rather than by
    # fullmatch is important because natasha may split a placeholder like
    # "[[FN:1]]" into multiple sub-tokens (e.g. "[", "[", "FN", ":", "1",
    # "]", "]").
    placeholder_intervals: list[tuple[int, int]] = [
        (m.start(), m.end()) for m in _PLACEHOLDER_RE.finditer(text)
    ]

    def _inside_placeholder(start: int, end: int) -> bool:
        for ps, pe in placeholder_intervals:
            if start >= ps and end <= pe:
                return True
        return False

    tokens: list[tuple[str, str, int, int]] = []
    for token in doc.tokens:
        # Skip any token that lies entirely within a placeholder span
        if _inside_placeholder(token.start, token.stop):
            continue

        token.lemmatize(morph_vocab)
        # natasha's token.start / token.stop are character offsets
        tokens.append(
            (token.text, token.lemma, token.start, token.stop)
        )

    return tokens


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_blacklist_phrases_ru(
    text: str,
    blacklist_lemmas: tuple[tuple[str, ...], ...],
    rule_name: str,
    severity: Severity,
) -> list[RuleMatch]:
    """Lemma-based blacklist matching for Russian (SPEC-001 §4a).

    1. Tokenises and lemmatises *text* via natasha into an ordered
       sequence of ``(surface_form, lemma, char_start, char_end)``.
    2. Placeholder tokens are excluded from the sequence entirely.
    3. For each blacklist entry (a tuple of N lemmas), slides a
       window of size N over the lemma sequence looking for an exact
       lemma-sequence match.
    4. On match, ``RuleMatch.matched_text`` is the **raw substring** from
       the original text spanning from ``char_start`` of the first matched
       token to ``char_end`` of the last matched token.  This preserves
       the invariant ``text[position : position + len(matched_text)] ==
       matched_text`` exactly, even when placeholder tokens fall inside
       the match span.
    5. Returns **all** matches found, not just the first per entry.

    Parameters
    ----------
    text:
        Raw input text (may contain ``[[FN:n]]`` / ``[[EN:n]]`` tokens).
    blacklist_lemmas:
        Each entry is a tuple of lemmas (e.g. ``("играть", "важный",
        "роль")``) as produced by ``load_config``.
    rule_name:
        Identifies which rule fired (e.g. ``"cliche_blacklist"``).
    severity:
        Severity level for each match.

    Returns
    -------
    A list of ``RuleMatch`` instances, one per occurrence found.
    """
    tokens = _tokenize_and_lemmatize_ru(text)

    # Extract just the lemma sequence for sliding-window matching
    lemmas = [t[1] for t in tokens]

    matches: list[RuleMatch] = []

    for entry in blacklist_lemmas:
        n = len(entry)
        if n == 0:
            continue

        # Slide a window of size n over the lemma sequence
        for i in range(len(lemmas) - n + 1):
            if lemmas[i : i + n] == list(entry):
                # Build matched_text as the raw substring from the original
                # text, preserving the invariant that
                # text[position : position + len(matched_text)] == matched_text.
                start_token = tokens[i]
                end_token = tokens[i + n - 1]
                char_start = start_token[2]
                char_end = end_token[3]
                matched_text = text[char_start:char_end]
                position = char_start

                matches.append(
                    RuleMatch(
                        rule_name=rule_name,
                        matched_text=matched_text,
                        position=position,
                        severity=severity,
                    )
                )

    return matches


def detect_blacklist_phrases_en(
    text: str,
    blacklist_literal: frozenset[str],
    rule_name: str,
    severity: Severity,
) -> list[RuleMatch]:
    """Case-insensitive literal substring blacklist matching for English.

    For each phrase in *blacklist_literal*, finds **all** non-overlapping
    occurrences in *text* using case-insensitive search.  Matches that
    overlap a placeholder token are explicitly excluded.

    Parameters
    ----------
    text:
        Raw input text (may contain ``[[FN:n]]`` / ``[[EN:n]]`` tokens).
    blacklist_literal:
        Lowercased literal phrases to search for.
    rule_name:
        Identifies which rule fired (e.g. ``"cliche_blacklist"``).
    severity:
        Severity level for each match.

    Returns
    -------
    A list of ``RuleMatch`` instances, one per occurrence found.
    """
    matches: list[RuleMatch] = []
    lower_text = text.lower()

    # Build a set of (start, end) intervals occupied by placeholder tokens.
    placeholder_spans: set[tuple[int, int]] = set()
    for m in _PLACEHOLDER_RE.finditer(text):
        placeholder_spans.add((m.start(), m.end()))

    def _overlaps_placeholder(start: int, end: int) -> bool:
        """Return True if the interval [start, end) overlaps any placeholder."""
        for ps, pe in placeholder_spans:
            # Intervals overlap if start < pe and end > ps
            if start < pe and end > ps:
                return True
        return False

    for phrase in blacklist_literal:
        if not phrase:
            continue

        phrase_lower = phrase.lower()
        search_start = 0
        while True:
            pos = lower_text.find(phrase_lower, search_start)
            if pos == -1:
                break

            match_end = pos + len(phrase)

            # Explicitly skip matches that overlap a placeholder token
            if not _overlaps_placeholder(pos, match_end):
                # matched_text is the original-case substring
                matched_text = text[pos:match_end]
                matches.append(
                    RuleMatch(
                        rule_name=rule_name,
                        matched_text=matched_text,
                        position=pos,
                        severity=severity,
                    )
                )

            # Advance search position to avoid infinite loop on zero-length
            search_start = pos + 1  # +1 to allow overlapping matches
            # However, the spec says "one RuleMatch per occurrence" —
            # we use +1 to find each distinct starting position.

    return matches