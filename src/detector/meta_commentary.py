"""
Meta-commentary detection for the detector component (SPEC-001 §2 item 3).

Checks only the first paragraph against opening-pattern regexes and only
the last paragraph against closing-pattern regexes.  Mid-text matches are
*not* meta-commentary and must not be flagged.
"""

import re

from src.detector.models import RuleMatch, Severity


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_meta_commentary(
    text: str,
    opening_patterns: tuple[str, ...],
    closing_patterns: tuple[str, ...],
) -> list[RuleMatch]:
    """Detect meta-commentary boilerplate at the first and last paragraphs.

    Splits *text* into paragraphs on ``\\n\\n`` (consistent with SPEC-003's
    paragraph-boundary convention).  Only the **first** paragraph is checked
    against *opening_patterns* and only the **last** paragraph is checked
    against *closing_patterns*.  Matches in any other paragraph are
    deliberately ignored — those are not meta-commentary per SPEC-001 §2.

    ``Severity.MEDIUM`` is used for both opening and closing matches.
    This is a starting assumption and can be tuned later without changing
    callers.

    Parameters
    ----------
    text:
        Input text that may contain multiple paragraphs separated by
        blank lines (``\\n\\n``).
    opening_patterns:
        Regex patterns to match against the first paragraph (e.g.
        ``r"(?i)^(давайте\\s+разбер[её]м|let'?s\\s+break\\s+down)"``).
    closing_patterns:
        Regex patterns to match against the last paragraph (e.g.
        ``r"(?i)^(в\\s+заключение|in\\s+conclusion)"``).

    Returns
    -------
    A list of ``RuleMatch`` instances, one per matching pattern found
    (opening or closing).  Returns an empty list for empty input or
    when no patterns match.
    """
    matches: list[RuleMatch] = []

    if not text.strip():
        return matches

    # Split on \n\n to get paragraphs (consistent with SPEC-003).
    # Using split("\n\n") means consecutive blank lines produce empty
    # paragraphs; filter those out to get real-content paragraphs only.
    raw_paragraphs = text.split("\n\n")
    paragraphs = [p for p in raw_paragraphs if p.strip()]
    if not paragraphs:
        return matches

    # --- Check first paragraph for opening patterns ---
    first = paragraphs[0]
    for pattern in opening_patterns:
        compiled = re.compile(pattern)
        m = compiled.search(first)
        if m is not None:
            matches.append(
                RuleMatch(
                    rule_name="meta_commentary_opening",
                    matched_text=m.group(),
                    position=m.start(),
                    severity=Severity.MEDIUM,
                )
            )

    # --- Check last paragraph for closing patterns ---
    if len(paragraphs) > 1:
        last = paragraphs[-1]
        for pattern in closing_patterns:
            compiled = re.compile(pattern)
            m = compiled.search(last)
            if m is not None:
                matches.append(
                    RuleMatch(
                        rule_name="meta_commentary_closing",
                        matched_text=m.group(),
                        position=m.start(),
                        severity=Severity.MEDIUM,
                    )
                )

    return matches