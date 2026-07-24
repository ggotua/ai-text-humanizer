"""
Data models for the programmatic detector component (SPEC-001).

Defines the core types used across all 5 metrics: severity levels,
per-match records, aggregate statistics containers, the top-level
report, the tunable configuration, the shared placeholder regex,
and the custom exception for runtime errors.
"""

import re
from dataclasses import dataclass
from enum import Enum


PLACEHOLDER_TOKEN_PATTERN = r"\[\[(?:FN|EN):[^\]]+\]\]"
"""Regex matching ``[[FN:n]]`` / ``[[EN:n]]`` placeholder tokens.

Matches any substring of the form ``[[FN:<id>]]`` or ``[[EN:<id>]]``
where ``<id>`` is any sequence of characters that are not ``]``.
Used by ``strip_placeholder_tokens`` and by every metric that must
exclude these tokens from counting or matching.
"""


class Severity(Enum):
    """Severity level for a ``RuleMatch``, used by SPEC-002 to prioritise
    rewrite instructions."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class RuleMatch:
    """A single detection of a blacklist phrase, hedge phrase, meta-commentary
    pattern, or parallelism construct in the input text.

    Attributes
    ----------
    rule_name:
        Identifies which rule fired (e.g. ``"cliche_blacklist"``,
        ``"hedge_phrase"``, ``"meta_opening"``, ``"parallelism"``).
    matched_text:
        The exact surface-form substring that triggered the match —
        human-readable, not a lemma bag.
    position:
        Character offset in the original input text where the match
        begins.
    severity:
        How severely this match affects the ``DetectorReport.passed``
        decision.
    """
    rule_name: str
    matched_text: str
    position: int
    severity: Severity


@dataclass(frozen=True)
class RhythmStats:
    """Sentence-length rhythm and monotony statistics.

    ``monotony_flag`` is ``True`` when ``stdev_length_words`` drops
    below the configured ``min_stdev_threshold`` — meaning the text
    is rhythmically flat and likely machine-generated.
    """
    sentence_count: int
    mean_length_words: float
    stdev_length_words: float
    lengths: list[int]
    monotony_flag: bool


@dataclass(frozen=True)
class DiversityStats:
    """Lexical diversity statistics based on lemmatised n-grams.

    ``distinct_2`` and ``distinct_3`` are the fraction of unique
    bigrams / trigrams among all bigrams / trigrams in the lemmatised
    token stream (placeholder tokens excluded). ``parallelism_matches``
    lists literal ``"X, Y and Z"`` / ``"X, Y и Z"`` pattern hits.
    """
    distinct_2: float
    distinct_3: float
    parallelism_matches: list[RuleMatch]


@dataclass(frozen=True)
class DetectorReport:
    """The complete output of running all 5 metrics on a text draft.

    ``passed`` is ``True`` only when **all** configured thresholds are
    satisfied (no rule exceeds its max match count, rhythm is not
    monotonous, diversity meets minimums). ``failed_rules`` lists the
    human-readable rule names that caused ``passed == False``, so
    SPEC-002 can produce targeted rewrite instructions.
    """
    cliche_matches: list[RuleMatch]
    hedge_matches: list[RuleMatch]
    meta_commentary_matches: list[RuleMatch]
    rhythm: RhythmStats
    diversity: DiversityStats
    passed: bool
    failed_rules: list[str]


@dataclass(frozen=True)
class DetectorConfig:
    """Tunable parameters for the detector component.

    The split between ``_lemmas`` and ``_literal`` fields reflects the
    different matching strategies for RU vs. EN (SPEC-001 §1 rationale):

    - **Russian** uses *lemma-based matching* because Russian is highly
      inflected — a literal entry ``"играет важную роль"`` would miss
      ``"играют важную роль"`` (number) or ``"сыграла важной роли"``
      (case, tense).  Blacklist phrases are lemmatised at load time into
      lemma tuples; matching compares lemma sequences, not surface forms.
      Hence the ``_lemmas`` fields are populated for ``language="ru"``
      and the ``_literal`` fields are empty.

    - **English** uses *literal substring matching* (case-insensitive)
      because English morphology is simpler — ``"crucial role"`` matches
      ``"crucial role"`` whether it's in subject or object position.
      Hence the ``_literal`` fields are populated for ``language="en"``
      and the ``_lemmas`` fields are empty.

    Attributes
    ----------
    language:
        ``"ru"`` or ``"en"``.
    cliche_blacklist_lemmas:
        RU only: each entry is a tuple of lemmas (e.g.
        ``("играть", "важный", "роль")``).  Empty tuple for EN.
    hedge_blacklist_lemmas:
        RU only, same format as cliche_blacklist_lemmas.
    cliche_blacklist_literal:
        EN only: lowercased literal phrases.  Empty frozenset for RU.
    hedge_blacklist_literal:
        EN only, same format as cliche_blacklist_literal.
    meta_opening_patterns:
        Regex patterns checked against the first paragraph only.
    meta_closing_patterns:
        Regex patterns checked against the last paragraph only.
    max_cliche_matches:
        Maximum allowed cliché matches before the report is failed.
    max_hedge_matches:
        Maximum allowed hedge matches before the report is failed.
    min_stdev_threshold:
        If ``RhythmStats.stdev_length_words`` falls below this,
        rhythm is flagged as monotonous.
    min_distinct_2:
        Minimum acceptable distinct-2 ratio.
    min_distinct_3:
        Minimum acceptable distinct-3 ratio.
    max_parallelism_matches:
        Maximum allowed ``"X, Y and Z"`` / ``"X, Y и Z"`` matches.
    """
    language: str
    cliche_blacklist_lemmas: tuple[tuple[str, ...], ...]
    hedge_blacklist_lemmas: tuple[tuple[str, ...], ...]
    cliche_blacklist_literal: frozenset[str]
    hedge_blacklist_literal: frozenset[str]
    meta_opening_patterns: tuple[str, ...]
    meta_closing_patterns: tuple[str, ...]
    max_cliche_matches: int
    max_hedge_matches: int
    min_stdev_threshold: float
    min_distinct_2: float
    min_distinct_3: float
    max_parallelism_matches: int


class DetectorError(RuntimeError):
    """Raised when an internal library (razdel, natasha) fails on unexpected
    input, carrying the offending text snippet for diagnosis.

    This is distinct from ``InvalidDocxError`` (which is about file format)
    — ``DetectorError`` signals that text processing hit an edge case the
    library couldn't handle.

    Attributes
    ----------
    offending_text:
        The text snippet that triggered the error, if available.
    """

    def __init__(self, message: str, offending_text: str = "") -> None:
        self.offending_text = offending_text
        if offending_text:
            msg = f"{message} (offending text: {offending_text!r})"
        else:
            msg = message
        super().__init__(msg)