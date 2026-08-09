"""
Prompt builders for the two-pass LLM rewrite pipeline (SPEC-002 §3).

Grammar pass — fixes grammar, logic, and factual consistency only.
Style pass  — applies feedback-driven rewrite instructions and optional persona.
"""

from src.promptgen.models import RewriteFeedback

# ---------------------------------------------------------------------------
# Placeholder-token preservation rule (verbatim strings)
# ---------------------------------------------------------------------------

_PLACEHOLDER_RULE_RU: str = (
    "Текст может содержать служебные метки вида [[FN:3]] или [[EN:7]]"
    " — это привязки к сноскам/концевым сноскам. Эти метки НЕЛЬЗЯ"
    " удалять, дублировать или терять. Каждая метка, присутствующая"
    " во входном тексте, должна присутствовать в вашем ответе ровно"
    " один раз — можно менять её положение внутри предложения, но не"
    " количество."
)

_PLACEHOLDER_RULE_EN: str = (
    "The text may contain marker tokens like [[FN:3]] or [[EN:7]]"
    " — these anchor footnotes/endnotes. These tokens must NEVER be"
    " deleted, duplicated, or lost. Each token present in the input"
    " must appear in your output exactly once — you may reposition"
    " it within a sentence, but never change its count."
)


def _placeholder_rule(language: str) -> str:
    """Return the placeholder-token rule text matching *language*."""
    if language == "ru":
        return _PLACEHOLDER_RULE_RU
    return _PLACEHOLDER_RULE_EN


# ---------------------------------------------------------------------------
# Anti-translation rule (SPEC-005b §2, verbatim strings)
# ---------------------------------------------------------------------------

_ANTI_TRANSLATION_RULE_RU: str = (
    "Отвечай СТРОГО на русском языке. Ни в коем случае не переводи текст"
    " на другой язык — только переписывай или исправляй его, сохраняя русский."
)

_ANTI_TRANSLATION_RULE_EN: str = (
    "Respond STRICTLY in English. Under no circumstances translate the"
    " text into another language — only rewrite or correct it, keeping it in English."
)


def _anti_translation_rule(language: str) -> str:
    """Return the anti-translation rule text matching *language*."""
    if language == "ru":
        return _ANTI_TRANSLATION_RULE_RU
    return _ANTI_TRANSLATION_RULE_EN


def _text_heading(language: str) -> str:
    """Return the heading for the input-text section."""
    if language == "ru":
        return "### Текст"
    return "### Text"


# ---------------------------------------------------------------------------
# Grammar pass
# ---------------------------------------------------------------------------


def build_grammar_pass_prompt(text: str, language: str) -> str:
    """Build the grammar-pass system prompt.

    Parameters
    ----------
    text:
        The input text to be corrected.
    language:
        ``"ru"`` or ``"en"``.

    Returns
    -------
    A complete prompt string ready to send to the LLM.
    """
    if language == "ru":
        instruction = (
            "Исправьте только грамматические ошибки, логическую"
            " связность и фактические противоречия. НЕ меняйте стиль,"
            " лексику или тон текста."
        )
    else:
        instruction = (
            "Fix only grammar errors, logical flow, and factual"
            " inconsistencies. Do NOT change the style, vocabulary, or"
            " tone of the text."
        )

    parts = [
        instruction,
        "",
        _anti_translation_rule(language),
        "",
        _placeholder_rule(language),
        "",
        _text_heading(language),
        text,
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Style pass
# ---------------------------------------------------------------------------


def build_style_pass_prompt(
    text: str,
    feedback: RewriteFeedback,
    language: str,
    persona: str | None = None,
) -> str:
    """Build the style-pass system prompt.

    Parameters
    ----------
    text:
        The input text to rewrite.
    feedback:
        Structured rewrite instructions from the detector pipeline.
    language:
        ``"ru"`` or ``"en"``.
    persona:
        Optional persona to style the rewrite after.  When provided,
        a ``"Rewrite in the style of: ..."`` line is added to the
        prompt.  When ``None`` (default), no persona section appears.

    Returns
    -------
    A complete prompt string ready to send to the LLM.
    """
    parts: list[str] = []

    # --- Main instruction ---
    if language == "ru":
        parts.append(
            "Перепишите текст, устраняя указанные ниже замечания."
        )
    else:
        parts.append(
            "Rewrite the text, addressing the issues listed below."
        )

    parts.append("")

    # --- Anti-translation rule (SPEC-005b §2) ---
    parts.append(_anti_translation_rule(language))
    parts.append("")

    # --- Placeholder-token rule ---
    parts.append(_placeholder_rule(language))
    parts.append("")

    # --- No intro/conclusion rule ---
    if language == "ru":
        parts.append(
            "НЕ добавляйте вступление или заключение, если они"
            " уже не были в исходном тексте и это не было"
            " специально запрошено."
        )
    else:
        parts.append(
            "Do NOT add an introduction or conclusion unless one"
            " was already present in the input and specifically"
            " requested."
        )

    parts.append("")

    # --- Persona (optional) ---
    if persona is not None:
        if language == "ru":
            parts.append(f"Перепишите в стиле: {persona}.")
        else:
            parts.append(f"Rewrite in the style of: {persona}.")
        parts.append("")

    # --- Feedback instructions (numbered list) ---
    if feedback.instructions:
        if language == "ru":
            parts.append("### Замечания для исправления")
        else:
            parts.append("### Issues to fix")

        for i, instr in enumerate(feedback.instructions, start=1):
            parts.append(f"{i}. {instr}")

        parts.append("")

    # --- Input text ---
    parts.append(_text_heading(language))
    parts.append(text)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Title-echo judge prompt
# ---------------------------------------------------------------------------


def build_title_echo_judge_prompt(subheading: str, context: str, language: str) -> str:
    """Build the title-echo judge system prompt.

    Parameters
    ----------
    subheading:
        The subheading to check for being an echo of the preceding text.
    context:
        The text immediately preceding the subheading.
    language:
        ``"ru"`` or ``"en"``.

    Returns
    -------
    A prompt string asking the LLM to judge whether the subheading is an echo
    of the context, to be answered with 'yes' or 'no' (or 'да'/'нет').
    """
    if language == "ru":
        question_line = "Является ли подзаголовок эхом предыдущего текста? Ответьте 'да' или 'нет'."
    else:
        question_line = "Is the subheader an echo of the previous text? Answer 'yes' or 'no'."

    parts = [
        "Determine if the subheading is an echo of the preceding text." if language == "en" else "Определите, является ли подзаголовок эхом предыдущего текста.",
        "",
        f"Preceding text: {context}" if language == "en" else f"Предыдущий текст: {context}",
        f"Subheading: {subheading}" if language == "en" else f"Подзаголовок: {subheading}",
        "",
        question_line,
    ]
    return "\n".join(parts)