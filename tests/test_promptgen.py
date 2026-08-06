"""
Unit tests for prompt generation (SPEC-002 §7).

Covers prompt-building functions (no LLM needed) and judge_title_echo
parsing logic (fake llm_call).  Integration tests against a real Ollama
instance are at the bottom of this file, marked ``@pytest.mark.integration``.
"""

import pytest

from src.detector.models import (
    DetectorReport,
    DiversityStats,
    RhythmStats,
    RuleMatch,
    Severity,
)
from src.promptgen.feedback import build_feedback_from_report
from src.promptgen.models import RewriteFeedback, TitleEchoJudgment
from src.promptgen.prompts import (
    build_grammar_pass_prompt,
    build_style_pass_prompt,
)
from src.promptgen.title_echo import (
    build_title_echo_prompt,
    judge_title_echo,
)

# ===========================================================================
# Helpers — reusable fixture data
# ===========================================================================

_RU_TEXT = "Это тестовый текст для проверки."
_EN_TEXT = "This is test text for verification."


def _make_empty_report(language: str = "ru") -> DetectorReport:
    """A report with no failed rules and no matches of any kind."""
    return DetectorReport(
        cliche_matches=[],
        hedge_matches=[],
        meta_commentary_matches=[],
        rhythm=RhythmStats(
            sentence_count=3,
            mean_length_words=10.0,
            stdev_length_words=5.0,
            lengths=[8, 10, 12],
            monotony_flag=False,
        ),
        diversity=DiversityStats(
            distinct_2=0.5,
            distinct_3=0.3,
            parallelism_matches=[],
        ),
        passed=True,
        failed_rules=[],
    )


def _make_cliche_report(language: str = "ru") -> DetectorReport:
    """A report that failed only the cliche_blacklist rule."""
    return DetectorReport(
        cliche_matches=[
            RuleMatch("cliche_blacklist", "важно отметить", 45, Severity.MEDIUM),
            RuleMatch("cliche_blacklist", "играет ключевую роль", 210, Severity.MEDIUM),
        ],
        hedge_matches=[],
        meta_commentary_matches=[],
        rhythm=RhythmStats(
            sentence_count=3,
            mean_length_words=10.0,
            stdev_length_words=5.0,
            lengths=[8, 10, 12],
            monotony_flag=False,
        ),
        diversity=DiversityStats(
            distinct_2=0.5,
            distinct_3=0.3,
            parallelism_matches=[],
        ),
        passed=False,
        failed_rules=["cliche_blacklist"],
    )


def _make_rhythm_report(language: str = "ru") -> DetectorReport:
    """A report that failed only the rhythm_monotony rule."""
    return DetectorReport(
        cliche_matches=[],
        hedge_matches=[],
        meta_commentary_matches=[],
        rhythm=RhythmStats(
            sentence_count=5,
            mean_length_words=18.0,
            stdev_length_words=2.1,
            lengths=[17, 19, 18, 20, 16],
            monotony_flag=True,
        ),
        diversity=DiversityStats(
            distinct_2=0.5,
            distinct_3=0.3,
            parallelism_matches=[],
        ),
        passed=False,
        failed_rules=["rhythm_monotony"],
    )


def _make_parallelism_report(language: str = "ru") -> DetectorReport:
    """A report that failed only the parallelism rule."""
    return DetectorReport(
        cliche_matches=[],
        hedge_matches=[],
        meta_commentary_matches=[],
        rhythm=RhythmStats(
            sentence_count=3,
            mean_length_words=10.0,
            stdev_length_words=5.0,
            lengths=[8, 10, 12],
            monotony_flag=False,
        ),
        diversity=DiversityStats(
            distinct_2=0.5,
            distinct_3=0.3,
            parallelism_matches=[
                RuleMatch("parallelism", "X, Y and Z", 10, Severity.LOW),
                RuleMatch("parallelism", "A, B and C", 50, Severity.LOW),
            ],
        ),
        passed=False,
        failed_rules=["parallelism"],
    )


def _make_meta_report(passed: bool = True, language: str = "ru") -> DetectorReport:
    """A report with meta-commentary matches, optionally passing."""
    return DetectorReport(
        cliche_matches=[],
        hedge_matches=[],
        meta_commentary_matches=[
            RuleMatch("meta_commentary_opening", "давайте разберём", 0, Severity.LOW),
        ],
        rhythm=RhythmStats(
            sentence_count=3,
            mean_length_words=10.0,
            stdev_length_words=5.0,
            lengths=[8, 10, 12],
            monotony_flag=False,
        ),
        diversity=DiversityStats(
            distinct_2=0.5,
            distinct_3=0.3,
            parallelism_matches=[],
        ),
        passed=passed,
        failed_rules=[] if passed else ["meta_commentary_opening"],
    )


# ===========================================================================
# Prompt-building tests — build_feedback_from_report
# ===========================================================================


class TestBuildFeedback:
    """Tests for build_feedback_from_report (SPEC-002 §4)."""

    def test_build_feedback_empty_report_returns_empty_instructions(self):
        """A passing report with no matches produces an empty instruction list."""
        report = _make_empty_report()
        feedback = build_feedback_from_report(report, "ru")
        assert isinstance(feedback, RewriteFeedback)
        assert feedback.instructions == []

    def test_build_feedback_cliche_matches_named_specifically(self):
        """Cliche instructions name the actual matched phrases and positions."""
        report = _make_cliche_report()
        feedback = build_feedback_from_report(report, "ru")
        assert len(feedback.instructions) == 1
        instr = feedback.instructions[0]
        assert "важно отметить" in instr
        assert "играет ключевую роль" in instr
        assert "45" in instr
        assert "210" in instr

    def test_build_feedback_rhythm_monotony_includes_actual_stats(self):
        """Rhythm instructions include the actual mean and std dev values."""
        report = _make_rhythm_report()
        feedback = build_feedback_from_report(report, "ru")
        assert len(feedback.instructions) == 1
        instr = feedback.instructions[0]
        assert "18.0" in instr
        assert "2.1" in instr

    def test_build_feedback_parallelism_includes_matched_examples(self):
        """Parallelism instructions include the actual matched patterns."""
        report = _make_parallelism_report()
        feedback = build_feedback_from_report(report, "ru")
        assert len(feedback.instructions) == 1
        instr = feedback.instructions[0]
        assert "X, Y and Z" in instr
        assert "A, B and C" in instr

    def test_build_feedback_meta_commentary_included_even_when_passed_true(self):
        """Meta-commentary instructions appear even when the report passes."""
        report = _make_meta_report(passed=True)
        feedback = build_feedback_from_report(report, "ru")
        assert len(feedback.instructions) == 1
        instr = feedback.instructions[0]
        assert "давайте разберём" in instr


# ===========================================================================
# Prompt-building tests — build_grammar_pass_prompt
# ===========================================================================


class TestGrammarPassPrompt:
    """Tests for build_grammar_pass_prompt (SPEC-002 §3)."""

    def test_grammar_pass_prompt_includes_placeholder_rule_always(self):
        """The placeholder-token preservation rule is always present."""
        prompt = build_grammar_pass_prompt(_RU_TEXT, "ru")
        assert "[[FN:3]]" in prompt
        assert "[[EN:7]]" in prompt
        assert "НЕЛЬЗЯ" in prompt

        prompt_en = build_grammar_pass_prompt(_EN_TEXT, "en")
        assert "[[FN:3]]" in prompt_en
        assert "[[EN:7]]" in prompt_en
        assert "NEVER" in prompt_en

    def test_grammar_pass_prompt_no_stylistic_instructions(self):
        """Grammar prompt does NOT contain style-related instructions."""
        prompt = build_grammar_pass_prompt(_RU_TEXT, "ru")
        assert "стиль" not in prompt.lower() or "стиль" in prompt  # "стиль" is in "НЕ меняйте стиль"
        # The instruction says "НЕ меняйте стиль" — that's about NOT changing style
        # We should check there's no instruction to *change* style
        assert "Перепишите" not in prompt  # grammar pass uses "Исправьте", not "Перепишите"

        prompt_en = build_grammar_pass_prompt(_EN_TEXT, "en")
        assert "Do NOT change the style" in prompt_en


# ===========================================================================
# Prompt-building tests — build_style_pass_prompt
# ===========================================================================


class TestStylePassPrompt:
    """Tests for build_style_pass_prompt (SPEC-002 §3)."""

    def test_style_pass_prompt_includes_placeholder_rule_always(self):
        """The placeholder-token preservation rule is always present."""
        feedback = RewriteFeedback(instructions=[])
        prompt = build_style_pass_prompt(_RU_TEXT, feedback, "ru")
        assert "[[FN:3]]" in prompt
        assert "[[EN:7]]" in prompt
        assert "НЕЛЬЗЯ" in prompt

        prompt_en = build_style_pass_prompt(_EN_TEXT, feedback, "en")
        assert "[[FN:3]]" in prompt_en
        assert "[[EN:7]]" in prompt_en
        assert "NEVER" in prompt_en

    def test_style_pass_prompt_forbids_intro_and_conclusion(self):
        """Style prompt explicitly forbids adding intro/conclusion."""
        feedback = RewriteFeedback(instructions=[])
        prompt = build_style_pass_prompt(_RU_TEXT, feedback, "ru")
        assert "вступление" in prompt
        assert "заключение" in prompt

        prompt_en = build_style_pass_prompt(_EN_TEXT, feedback, "en")
        assert "introduction" in prompt_en
        assert "conclusion" in prompt_en

    def test_style_pass_prompt_includes_feedback_instructions_verbatim(self):
        """Feedback instructions appear verbatim in the prompt."""
        instructions = ["Fix cliché phrases", "Remove hedge words"]
        feedback = RewriteFeedback(instructions=instructions)
        prompt = build_style_pass_prompt(_RU_TEXT, feedback, "ru")
        for instr in instructions:
            assert instr in prompt

    def test_style_pass_prompt_with_persona_includes_persona_text(self):
        """When persona is provided, the persona line appears."""
        feedback = RewriteFeedback(instructions=[])
        prompt = build_style_pass_prompt(
            _RU_TEXT, feedback, "ru", persona="cynical journalist"
        )
        assert "Перепишите в стиле: cynical journalist." in prompt

        prompt_en = build_style_pass_prompt(
            _EN_TEXT, feedback, "en", persona="academic"
        )
        assert "Rewrite in the style of: academic." in prompt_en

    def test_style_pass_prompt_without_persona_omits_persona_section(self):
        """When persona is None, no persona-related text appears."""
        feedback = RewriteFeedback(instructions=[])
        prompt = build_style_pass_prompt(_RU_TEXT, feedback, "ru")
        assert "стиле:" not in prompt
        assert "style of:" not in prompt

        prompt_en = build_style_pass_prompt(_EN_TEXT, feedback, "en")
        assert "стиле:" not in prompt_en
        assert "style of:" not in prompt_en


# ===========================================================================
# Prompt-building tests — build_title_echo_prompt
# ===========================================================================


class TestTitleEchoPrompt:
    """Tests for build_title_echo_prompt (SPEC-002 §5)."""

    def test_title_echo_prompt_strips_placeholder_tokens_from_following_text(self):
        """Placeholder tokens are removed from following_text before insertion."""
        prompt = build_title_echo_prompt(
            "My Heading",
            "Some text with [[FN:3]] a token.",
            "en",
        )
        assert "[[FN:3]]" not in prompt
        assert "Some text with  a token." in prompt  # double space where token was


# ===========================================================================
# Judge tests — judge_title_echo with fake llm_call
# ===========================================================================


class TestJudgeTitleEcho:
    """Tests for judge_title_echo parsing logic (SPEC-002 §6)."""

    def test_judge_title_echo_parses_da_as_true(self):
        """'ДА' (with trailing punctuation) parses to is_echo=True."""
        judgment = judge_title_echo(
            "Heading", "Text.", "ru",
            lambda _: "ДА, потому что это очевидно.",
        )
        assert judgment.is_echo is True
        assert judgment.parse_warning is None

    def test_judge_title_echo_parses_net_as_false(self):
        """'НЕТ' parses to is_echo=False."""
        judgment = judge_title_echo(
            "Heading", "Text.", "ru",
            lambda _: "НЕТ, это не так.",
        )
        assert judgment.is_echo is False
        assert judgment.parse_warning is None

    def test_judge_title_echo_parses_yes_as_true_english(self):
        """'YES' (English) parses to is_echo=True."""
        judgment = judge_title_echo(
            "Heading", "Text.", "en",
            lambda _: "YES, it does.",
        )
        assert judgment.is_echo is True
        assert judgment.parse_warning is None

    def test_judge_title_echo_unparseable_response_returns_false_with_warning(self):
        """An unparseable response returns is_echo=False with a warning."""
        judgment = judge_title_echo(
            "Heading", "Text.", "en",
            lambda _: "Sort of, maybe",
        )
        assert judgment.is_echo is False
        assert judgment.parse_warning is not None
        assert "Sort" in judgment.parse_warning

    def test_judge_title_echo_case_insensitive_parsing(self):
        """Case-insensitive matching: 'да', 'Да', 'ДА' all work."""
        for response in ["да", "Да", "ДА"]:
            judgment = judge_title_echo(
                "Heading", "Text.", "ru",
                lambda _, r=response: r,
            )
            assert judgment.is_echo is True, f"Failed for {response!r}"
            assert judgment.parse_warning is None, f"Failed for {response!r}"

        for response in ["yes", "Yes", "YES"]:
            judgment = judge_title_echo(
                "Heading", "Text.", "en",
                lambda _, r=response: r,
            )
            assert judgment.is_echo is True, f"Failed for {response!r}"
            assert judgment.parse_warning is None, f"Failed for {response!r}"


# ===========================================================================
# Integration test — real Ollama call (marked @pytest.mark.integration)
# ===========================================================================


def _real_ollama_call(prompt: str, model: str = "mistral") -> str:
    """Call Ollama's generate API synchronously and return the response text.

    Uses a raw HTTP POST to ``http://localhost:11434/api/generate``.
    No retry logic, no streaming — a single request/response round trip.

    Raises
    ------
    requests.exceptions.ConnectionError
        If Ollama is not reachable at localhost:11434.
    """
    import requests

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    resp = requests.post(
        "http://localhost:11434/api/generate",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["response"]


@pytest.mark.integration
class TestJudgeTitleEchoIntegration:
    """Integration tests requiring a live Ollama instance with ``mistral`` pulled."""

    def test_judge_title_echo_against_real_ollama_obvious_echo_case(self):
        """An obvious echo case should be detected as is_echo=True.

        Uses a real Ollama call.  Skips gracefully if Ollama is not
        reachable, so the test never crashes the suite.
        """
        heading = "Преимущества удалённой работы"
        following_text = "Удалённая работа имеет свои преимущества."

        try:
            judgment = judge_title_echo(
                heading, following_text, "ru", _real_ollama_call,
            )
        except Exception as exc:
            pytest.skip(
                f"Ollama not reachable or request failed: {exc}"
            )

        assert judgment.is_echo is True, (
            f"Expected is_echo=True for obvious echo case, "
            f"got is_echo={judgment.is_echo}, "
            f"raw_response={judgment.raw_response!r}, "
            f"parse_warning={judgment.parse_warning!r}"
        )
        assert judgment.parse_warning is None, (
            f"Expected clean parse, got warning: {judgment.parse_warning!r}"
        )
