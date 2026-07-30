"""
Feedback construction (SPEC-002 §4).

Converts a ``DetectorReport`` into a ``RewriteFeedback`` with specific,
concrete instruction strings — one per rule in ``report.failed_rules``,
plus a meta-commentary instruction if any meta-commentary matches exist
(regardless of pass/fail status).
"""

from src.detector.models import DetectorReport, RuleMatch
from src.promptgen.models import RewriteFeedback


# ---------------------------------------------------------------------------
# Formatting helpers: truncate long match lists at 5 items
# ---------------------------------------------------------------------------

_MAX_EXAMPLES = 5


def _format_match_list_ru(
    matches: list[RuleMatch],
    phrase_label: str,
    remaining_label: str,
) -> str:
    """Build RU instruction fragment listing matched phrases with positions.

    Parameters
    ----------
    matches:
        The matches to list (e.g. ``report.cliche_matches``).
    phrase_label:
        Opening label, e.g. ``"Обнаружены штампы"``.
    remaining_label:
        Suffix for truncated items, e.g. ``"и ещё {n}"``.
    """
    items = []
    for m in matches[:_MAX_EXAMPLES]:
        items.append(f"«{m.matched_text}» (позиция {m.position})")
    fragment = ", ".join(items)
    if len(matches) > _MAX_EXAMPLES:
        fragment += f", {remaining_label.format(n=len(matches) - _MAX_EXAMPLES)}"
    return f"{phrase_label}: {fragment}. Перефразируйте эти места, убрав штампы, не меняя смысл."


def _format_match_list_en(
    matches: list[RuleMatch],
    phrase_label: str,
    remaining_label: str,
) -> str:
    """Build EN instruction fragment listing matched phrases with positions."""
    items = []
    for m in matches[:_MAX_EXAMPLES]:
        items.append(f"'{m.matched_text}' (position {m.position})")
    fragment = ", ".join(items)
    if len(matches) > _MAX_EXAMPLES:
        fragment += f", {remaining_label.format(n=len(matches) - _MAX_EXAMPLES)}"
    return f"{phrase_label}: {fragment}. Rephrase these to remove clichés without changing meaning."


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_feedback_from_report(
    report: DetectorReport,
    language: str,
) -> RewriteFeedback:
    """Convert a ``DetectorReport`` into specific rewrite instructions.

    For each rule name in ``report.failed_rules``, generates exactly one
    instruction string following the wording patterns from SPEC-002 §4.
    Meta-commentary matches (``report.meta_commentary_matches``) are
    always included as an additional instruction when non-empty, regardless
    of whether the report passed or failed.

    Parameters
    ----------
    report:
        The fully-populated detector report.
    language:
        ``"ru"`` or ``"en"``.

    Returns
    -------
    ``RewriteFeedback`` with zero or more instructions.
    """
    instructions: list[str] = []

    for rule in report.failed_rules:
        if rule == "cliche_blacklist":
            instructions.append(
                _build_cliche_instruction(report.cliche_matches, language)
            )
        elif rule == "hedge_blacklist":
            instructions.append(
                _build_hedge_instruction(report.hedge_matches, language)
            )
        elif rule == "rhythm_monotony":
            instructions.append(
                _build_rhythm_instruction(report.rhythm, language)
            )
        elif rule == "lexical_diversity":
            instructions.append(
                _build_diversity_instruction(report.diversity, language)
            )
        elif rule == "parallelism":
            instructions.append(
                _build_parallelism_instruction(report.diversity.parallelism_matches, language)
            )

    # Meta-commentary is informational — included whenever non-empty,
    # regardless of pass/fail status.
    if report.meta_commentary_matches:
        instructions.append(
            _build_meta_commentary_instruction(report.meta_commentary_matches, language)
        )

    return RewriteFeedback(instructions=instructions)


# ---------------------------------------------------------------------------
# Per-rule instruction builders (private)
# ---------------------------------------------------------------------------


def _build_cliche_instruction(
    matches: list[RuleMatch], language: str,
) -> str:
    if language == "ru":
        return _format_match_list_ru(
            matches,
            phrase_label="Обнаружены штампы",
            remaining_label="и ещё {n}",
        )
    return _format_match_list_en(
        matches,
        phrase_label="Cliché phrases found",
        remaining_label="and {n} more",
    )


def _build_hedge_instruction(
    matches: list[RuleMatch], language: str,
) -> str:
    if language == "ru":
        return _format_match_list_ru(
            matches,
            phrase_label="Обнаружены слова-паразиты",
            remaining_label="и ещё {n}",
        )
    return _format_match_list_en(
        matches,
        phrase_label="Hedge phrases found",
        remaining_label="and {n} more",
    )


def _build_rhythm_instruction(
    rhythm: "RhythmStats", language: str,
) -> str:
    if language == "ru":
        return (
            f"Предложения слишком однородны по длине "
            f"(среднее: {rhythm.mean_length_words:.1f} слов, "
            f"разброс: {rhythm.stdev_length_words:.1f}). "
            "Чередуйте: после длинного сложного предложения "
            "ставьте короткое, до 5 слов."
        )
    return (
        f"Sentences are too uniform in length "
        f"(mean: {rhythm.mean_length_words:.1f} words, "
        f"std dev: {rhythm.stdev_length_words:.1f}). "
        "Vary sentence length: after a long complex sentence "
        "use a short one (up to 5 words)."
    )


def _build_diversity_instruction(
    diversity: "DiversityStats", language: str,
) -> str:
    if language == "ru":
        # TODO: thresholds are not available on DiversityStats/DetectorReport.
        # Future refinement should add them or pass config separately.
        # For now, phrase without the threshold number.
        return (
            f"Словарь повторяется (distinct-2: {diversity.distinct_2:.2f}, "
            f"distinct-3: {diversity.distinct_3:.2f}). "
            "Используйте более разнообразную лексику, "
            "избегайте повтора одних и тех же слов в соседних предложениях."
        )
    return (
        f"Vocabulary repetition detected "
        f"(distinct-2: {diversity.distinct_2:.2f}, "
        f"distinct-3: {diversity.distinct_3:.2f}). "
        "Use more varied vocabulary; avoid repeating the same words "
        "in adjacent sentences."
    )


def _build_parallelism_instruction(
    matches: list[RuleMatch], language: str,
) -> str:
    # Up to 3 examples
    examples = [f"'{m.matched_text}'" for m in matches[:3]]
    examples_text = ", ".join(examples)
    if len(matches) > 3:
        examples_text += ", ..."

    if language == "ru":
        return (
            f"Обнаружен повторяющийся синтаксический паттерн "
            f"«X, Y и Z»: {examples_text}. "
            "Разбейте на отдельные предложения или буллеты."
        )
    return (
        f"Repetitive syntactic pattern 'X, Y and Z' detected: "
        f"{examples_text}. "
        "Break into separate sentences or bullet points."
    )


def _build_meta_commentary_instruction(
    matches: list[RuleMatch], language: str,
) -> str:
    # Group by rule_name to distinguish opening vs closing
    opening_phrases: list[str] = []
    closing_phrases: list[str] = []
    for m in matches:
        if m.rule_name == "meta_commentary_opening":
            opening_phrases.append(f"«{m.matched_text}»")
        elif m.rule_name == "meta_commentary_closing":
            closing_phrases.append(f"«{m.matched_text}»")

    parts: list[str] = []
    if opening_phrases:
        parts.append(", ".join(opening_phrases))
    if closing_phrases:
        parts.append(", ".join(closing_phrases))

    phrases_text = ", ".join(parts)

    if language == "ru":
        return (
            f"Текст содержит шаблонные фразы: {phrases_text}. "
            "Уберите их — не добавляйте вступление или заключение, "
            "если это не запрошено."
        )
    return (
        f"Text contains formulaic phrases: {phrases_text}. "
        "Remove them — do not add an introduction or conclusion "
        "unless requested."
    )