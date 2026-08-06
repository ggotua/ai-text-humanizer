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
