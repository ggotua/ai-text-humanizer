"""
Title-echo LLM-as-judge (SPEC-002 §5, §6).

Two functions:

* ``build_title_echo_prompt`` — constructs a direct yes/no judge prompt
  asking whether *following_text* merely restates *heading*.
* ``judge_title_echo`` — calls an injected ``llm_call`` with that prompt
  and parses the response into a structured ``TitleEchoJudgment``.
"""

from typing import Callable

from src.detector.config_loader import strip_placeholder_tokens
from src.promptgen.models import TitleEchoJudgment


def build_title_echo_prompt(
    heading: str,
    following_text: str,
    language: str,
) -> str:
    """Construct a direct yes/no judge prompt for title-echo detection.

    Placeholder tokens (``[[FN:n]]`` / ``[[EN:n]]``) are stripped from
    *following_text* before insertion — they are irrelevant to the
    judgment and could confuse a small model.

    Parameters
    ----------
    heading:
        The subheading to check.
    following_text:
        The text immediately following the subheading.
    language:
        ``"ru"`` or ``"en"``.

    Returns
    -------
    A complete prompt string ready to send to the LLM.
    """
    cleaned_text, _ = strip_placeholder_tokens(following_text)

    if language == "ru":
        parts = [
            f"Заголовок: «{heading}»",
            f"Следующий текст: «{cleaned_text}»",
            "Вопрос: пересказывает ли следующий текст заголовок другими"
            " словами, не добавляя данных, примеров или нарратива?",
            "Ответьте одним словом: ДА или НЕТ. Затем, если хотите,"
            " кратко поясните.",
        ]
    else:
        parts = [
            f"Heading: «{heading}»",
            f"Following text: «{cleaned_text}»",
            "Question: does the following text restate the heading in"
            " other words, without adding data, examples, or narrative?",
            "Answer with one word: YES or NO. Then, if you wish, briefly"
            " explain.",
        ]

    return "\n".join(parts)


def judge_title_echo(
    heading: str,
    following_text: str,
    language: str,
    llm_call: Callable[[str], str],
) -> TitleEchoJudgment:
    """Build the title-echo prompt, invoke *llm_call*, and parse the response.

    Parameters
    ----------
    heading:
        The subheading to check.
    following_text:
        The text immediately following the subheading.
    language:
        ``"ru"`` or ``"en"``.
    llm_call:
        Injected callable that takes a prompt string and returns the
        model's raw text response.  Exceptions from this call are
        **not** caught here — that is the caller's responsibility
        (SPEC-002 §6).

    Returns
    -------
    A ``TitleEchoJudgment`` with the parsed result.
    """
    prompt = build_title_echo_prompt(heading, following_text, language)
    response = llm_call(prompt)

    stripped = response.strip()
    first_word_raw = stripped.split(maxsplit=1)[0] if stripped else ""
    # Strip common punctuation that may be attached to the first word
    first_word = first_word_raw.strip(".,!?;:\"'«»()[]")
    first_word_lower = first_word.lower()

    if first_word_lower in {"да", "yes"}:
        return TitleEchoJudgment(
            is_echo=True,
            raw_response=response,
            parse_warning=None,
        )

    if first_word_lower in {"нет", "no"}:
        return TitleEchoJudgment(
            is_echo=False,
            raw_response=response,
            parse_warning=None,
        )

    return TitleEchoJudgment(
        is_echo=False,
        raw_response=response,
        parse_warning=(
            f"Unparseable response, first word was: {first_word_raw!r}"
        ),
    )
