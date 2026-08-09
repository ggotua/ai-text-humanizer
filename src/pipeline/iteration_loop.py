"""Iteration loop & threshold controller (SPEC-005).

This module contains the pure-Python orchestration layer that wraps
``run_single_pass`` (SPEC-004) in a loop, re-checking
``DetectorReport.passed`` after each pass up to a fixed iteration cap,
and enforcing placeholder-token integrity across iterations.

This module implements the data model, the placeholder-token extraction
helpers, and the full loop logic including the placeholder-token
integrity check across iterations.
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from src.detector.models import PLACEHOLDER_TOKEN_PATTERN
from src.pipeline.single_pass import run_single_pass

if TYPE_CHECKING:
    from src.detector.models import DetectorConfig
    from src.pipeline.single_pass import SinglePassResult


# Compile the shared placeholder pattern once at module level.
_PLACEHOLDER_RE = re.compile(PLACEHOLDER_TOKEN_PATTERN)


@dataclass(frozen=True)
class IterationResult:
    """Result of running the iteration loop (SPEC-005 section 2).

    Attributes
    ----------
    final_text:
        The best text produced by the loop — either the first passing
        attempt, the best-scoring attempt when the cap is reached, or the
        last known-good text when token integrity breaks.
    passed:
        ``True`` only when a pass's ``DetectorReport.passed`` was True.
    iterations_completed:
        Number of iterations actually completed.
    history:
        Every ``SinglePassResult`` attempt, in order, for audit/debugging.
    warning:
        Set on max-iterations-reached OR token-integrity failure; ``None``
        on a clean pass.
    """

    final_text: str
    passed: bool
    iterations_completed: int
    history: tuple["SinglePassResult", ...]  # every attempt, in order, for audit/debugging
    warning: str | None  # set on max-iterations-reached OR token-integrity failure


def extract_placeholder_token_set(text: str) -> frozenset[str]:
    """Return the set of distinct placeholder tokens found in *text*.

    Uses SPEC-001's ``PLACEHOLDER_TOKEN_PATTERN``.  Returns a SET, not a
    count — per SPEC-002 section 3's rule, a token may be duplicated
    legitimately if the original text cited the same footnote twice (rare
    but valid per SPEC-003 section 5's "Duplicate legitimate reference"
    case) — so integrity checking compares the MULTISET (count per
    distinct token), not just set membership.  See SPEC-005 section 4 for
    the exact comparison this function's result feeds into.
    """
    return frozenset(match.group() for match in _PLACEHOLDER_RE.finditer(text))


def extract_placeholder_token_multiset(text: str) -> Counter:
    """Return a ``collections.Counter`` of placeholder tokens in *text*.

    Counts each distinct placeholder token (e.g. ``[[FN:3]]``) by how many
    times it appears, using SPEC-001's ``PLACEHOLDER_TOKEN_PATTERN``.  This
    is the count-aware multiset used for the token-integrity comparison in
    SPEC-005 section 4 — a plain set would silently accept a pass that
    drops one of two legitimate duplicate citations of the same footnote.
    """
    return Counter(match.group() for match in _PLACEHOLDER_RE.finditer(text))


def detect_dominant_script(text: str) -> str:
    """
    Returns "cyrillic", "latin", or "unknown" based on which script's
    letters dominate the alphabetic characters in text. Cheap heuristic
    (character-class counting, not a real language detector) —
    sufficient to catch a full RU<->EN drift, the only failure mode
    observed so far. Non-alphabetic characters (digits, punctuation,
    whitespace, placeholder tokens) are ignored in the count. Returns
    "unknown" if there are fewer than 20 alphabetic characters total
    (too short to judge reliably — avoid false positives on short text).
    """
    # Strip placeholder tokens before counting, reusing the shared
    # compiled pattern (same extract-and-strip approach as the rest of
    # this module) so token text never skews the script count.
    stripped = _PLACEHOLDER_RE.sub("", text)

    cyrillic = 0
    latin = 0
    for ch in stripped:
        code = ord(ch)
        if 0x0400 <= code <= 0x04FF:
            # Cyrillic block (U+0400-U+04FF).
            cyrillic += 1
        elif ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            # ASCII Latin letters (sufficient for RU vs EN distinction).
            latin += 1

    total = cyrillic + latin
    if total < 20:
        return "unknown"
    if cyrillic > latin:
        return "cyrillic"
    if latin > cyrillic:
        return "latin"
    # Equal counts — neither script dominates; too ambiguous to judge.
    return "unknown"


def check_language_integrity(text: str, expected_language: str) -> tuple[bool, str | None]:
    """
    expected_language is "ru" or "en". Maps to the script that should
    dominate: "ru" -> "cyrillic", "en" -> "latin". Calls
    detect_dominant_script(text); if the result is "unknown" (too short
    to judge), returns (True, None) — do not flag short text as a
    false positive. If the dominant script doesn't match what
    expected_language implies, returns (False, a warning message naming
    the expected vs detected script). Otherwise (True, None).
    """
    expected_script = "cyrillic" if expected_language == "ru" else "latin"
    dominant = detect_dominant_script(text)
    if dominant == "unknown":
        return (True, None)
    if dominant != expected_script:
        return (
            False,
            f"Expected {expected_script} script (language={expected_language}), "
            f"detected {dominant}.",
        )
    return (True, None)


def run_iteration_loop(
    text: str,
    language: str,
    ollama_call: Callable[[str, float], str],
    detector_config: "DetectorConfig",
    max_iterations: int = 3,
    persona: str | None = None,
) -> IterationResult:
    """Repeats ``run_single_pass`` up to ``max_iterations`` times.

    Repeats ``run_single_pass`` up to ``max_iterations`` times, feeding
    each pass's output back in as the next pass's input, until
    ``detector_report.passed`` is True or the fixed iteration cap is
    reached.  Raises ``ValueError`` if ``max_iterations < 1`` (fail fast
    on misconfiguration rather than silently doing zero useful work).

    After each pass, verifies placeholder-token integrity by comparing the
    multiset of tokens before and after the pass (via
    ``extract_placeholder_token_multiset``).  If the multisets differ —
    a token was dropped, duplicated, or swapped for a wrong one — the loop
    stops immediately and returns the last known-good text (the input to
    the corrupting pass), not the corrupted output.

    Does not catch exceptions from ``ollama_call`` (via ``run_single_pass``)
    — connection/timeout errors propagate to the caller, consistent with
    SPEC-002 section 6 and SPEC-004 section 3's "propagate, don't catch"
    convention.

    """
    if max_iterations < 1:
        raise ValueError(
            f"max_iterations must be >= 1, got {max_iterations}"
        )

    current_text = text
    history: list["SinglePassResult"] = []

    for i in range(max_iterations):
        tokens_before = extract_placeholder_token_multiset(current_text)

        result = run_single_pass(
            current_text, language, ollama_call, detector_config, persona
        )
        history.append(result)

        tokens_after = extract_placeholder_token_multiset(result.final_text)

        if tokens_before != tokens_after:
            # Token integrity broken THIS iteration — stop immediately,
            # do NOT use result.final_text.  Return the last known-good
            # text (the input to this corrupting pass).  Counter equality
            # catches dropped, duplicated, AND swapped-in wrong tokens.
            return IterationResult(
                final_text=current_text,  # the text BEFORE this corrupting pass
                passed=False,
                iterations_completed=i,  # this iteration did not complete cleanly
                history=tuple(history),
                warning=(
                    f"Placeholder token integrity broken at iteration {i+1}: "
                    f"before={dict(tokens_before)}, after={dict(tokens_after)}. "
                    f"Returning last known-good text from before this iteration."
                ),
            )

        lang_ok, lang_warning = check_language_integrity(result.final_text, language)
        if not lang_ok:
            return IterationResult(
                final_text=current_text,  # same pre-corruption pattern as token integrity
                passed=False,
                iterations_completed=i,
                history=tuple(history),
                warning=(
                    f"Language integrity broken at iteration {i+1}: {lang_warning} "
                    f"Returning last known-good text from before this iteration."
                ),
            )

        if result.detector_report.passed:

            return IterationResult(
                final_text=result.final_text,
                passed=True,
                iterations_completed=i + 1,
                history=tuple(history),
                warning=None,
            )

        current_text = result.final_text

    # Loop exhausted without passing.  ``min`` returns the FIRST entry on
    # ties — the desired tie-breaking behavior per SPEC-005 section 3's
    # note (an earlier attempt with equally-many failures is preferred).
    best = min(history, key=lambda r: len(r.detector_report.failed_rules))
    return IterationResult(
        final_text=best.final_text,
        passed=False,
        iterations_completed=max_iterations,
        history=tuple(history),
        warning=(
            f"Max iterations ({max_iterations}) reached without passing all "
            f"thresholds; returning best-scoring attempt "
            f"({len(best.detector_report.failed_rules)} failed rule(s) remaining)."
        ),
    )
