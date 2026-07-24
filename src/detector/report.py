"""
Detector report assembly (SPEC-001 §3).

Runs all 5 metrics and assembles the final ``DetectorReport``, including
``passed`` / ``failed_rules`` based on config thresholds.
"""

from src.detector.models import (
    DetectorConfig,
    DetectorReport,
    Severity,
)
from src.detector.blacklist import (
    detect_blacklist_phrases_ru,
    detect_blacklist_phrases_en,
)
from src.detector.meta_commentary import detect_meta_commentary
from src.detector.rhythm import compute_rhythm_stats
from src.detector.diversity import compute_diversity_stats


def build_detector_report(
    text: str,
    config: DetectorConfig,
) -> DetectorReport:
    """Run all 5 metrics and assemble the final report.

    Dispatches blacklist/hedge detection based on ``config.language``:
    Russian uses lemma-based matching, English uses literal substring
    matching.  Meta-commentary, rhythm, and diversity are language-
    agnostic and run for both.

    ``passed`` is ``True`` only when **all** configured thresholds are
    satisfied.  ``failed_rules`` lists the human-readable rule names
    that caused ``passed == False``, so SPEC-002 can produce targeted
    rewrite instructions.

    Note: meta-commentary matches are **informational** in this report —
    they are always included but do **not** contribute to ``failed_rules``
    or ``passed`` in this spec.  SPEC-002 decides how to weight them in
    the rewrite prompt.

    Parameters
    ----------
    text:
        Input text to analyse (may contain ``[[FN:n]]`` / ``[[EN:n]]``
        tokens).
    config:
        Tunable parameters loaded via ``load_config``.

    Returns
    -------
    A fully-populated ``DetectorReport``.
    """
    # --- 1. Blacklist / hedge detection (language-dependent) ---
    if config.language == "ru":
        cliche_matches = detect_blacklist_phrases_ru(
            text=text,
            blacklist_lemmas=config.cliche_blacklist_lemmas,
            rule_name="cliche_blacklist",
            severity=Severity.HIGH,
        )
        hedge_matches = detect_blacklist_phrases_ru(
            text=text,
            blacklist_lemmas=config.hedge_blacklist_lemmas,
            rule_name="hedge_blacklist",
            severity=Severity.MEDIUM,
        )
    elif config.language == "en":
        cliche_matches = detect_blacklist_phrases_en(
            text=text,
            blacklist_literal=config.cliche_blacklist_literal,
            rule_name="cliche_blacklist",
            severity=Severity.HIGH,
        )
        hedge_matches = detect_blacklist_phrases_en(
            text=text,
            blacklist_literal=config.hedge_blacklist_literal,
            rule_name="hedge_blacklist",
            severity=Severity.MEDIUM,
        )
    else:
        # This should not happen if config was loaded through load_config,
        # but guard against it to avoid silent mis-routing.
        cliche_matches = []
        hedge_matches = []

    # --- 2. Meta-commentary (informational) ---
    meta_commentary_matches = detect_meta_commentary(
        text=text,
        opening_patterns=config.meta_opening_patterns,
        closing_patterns=config.meta_closing_patterns,
    )

    # --- 3. Rhythm stats ---
    rhythm = compute_rhythm_stats(
        text=text,
        min_stdev_threshold=config.min_stdev_threshold,
    )

    # --- 4. Diversity stats ---
    diversity = compute_diversity_stats(
        text=text,
        min_distinct_2=config.min_distinct_2,
        min_distinct_3=config.min_distinct_3,
        max_parallelism_matches=config.max_parallelism_matches,
    )

    # --- 5. Determine passed / failed_rules ---
    # Per SPEC-001 §5: empty/whitespace-only input (sentence_count == 0)
    # produces passed=True with empty failed_rules — there is nothing to
    # evaluate, so no rule can fail.
    if rhythm.sentence_count == 0:
        return DetectorReport(
            cliche_matches=cliche_matches,
            hedge_matches=hedge_matches,
            meta_commentary_matches=meta_commentary_matches,
            rhythm=rhythm,
            diversity=diversity,
            passed=True,
            failed_rules=[],
        )

    failed_rules: list[str] = []

    if len(cliche_matches) > config.max_cliche_matches:
        failed_rules.append("cliche_blacklist")

    if len(hedge_matches) > config.max_hedge_matches:
        failed_rules.append("hedge_blacklist")

    if rhythm.monotony_flag:
        failed_rules.append("rhythm_monotony")

    if diversity.distinct_2 < config.min_distinct_2 or diversity.distinct_3 < config.min_distinct_3:
        failed_rules.append("lexical_diversity")

    if len(diversity.parallelism_matches) > config.max_parallelism_matches:
        failed_rules.append("parallelism")

    passed = len(failed_rules) == 0

    return DetectorReport(
        cliche_matches=cliche_matches,
        hedge_matches=hedge_matches,
        meta_commentary_matches=meta_commentary_matches,
        rhythm=rhythm,
        diversity=diversity,
        passed=passed,
        failed_rules=failed_rules,
    )