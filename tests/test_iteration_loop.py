"""
Unit tests for the iteration loop engine (SPEC-005 §7, engine layer).

Covers ``run_iteration_loop`` orchestration (no Langflow, no real Ollama)
and the placeholder-token multiset helpers.  All ``ollama_call`` fakes are
plain functions/closures that track the call count and deliberately drop
or duplicate a placeholder token on a specific call number, to simulate
the realistic LLM failure mode of not honoring the preservation rule.

``run_single_pass`` is patched (via ``mock.patch`` on the name bound in
``src.pipeline.iteration_loop``) so the loop's orchestration is tested in
isolation from the real single-pass engine.
"""

from collections import Counter
from unittest import mock

import pytest

from src.detector.config_loader import load_config

from src.pipeline.iteration_loop import (
    IterationResult,
    check_language_integrity,
    detect_dominant_script,
    extract_placeholder_token_multiset,
    extract_placeholder_token_set,
    run_iteration_loop,
)
from src.pipeline.single_pass import SinglePassResult, make_ollama_call_fn


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# A text with two distinct placeholder tokens, one of which appears twice
# (a legitimate duplicate citation per SPEC-003 §5).
_ORIGINAL = "Текст [[FN:3]] с сноской [[FN:3]] дважды и [[EN:1]] один раз."

# The same text with the duplicate [[FN:3]] collapsed to a single occurrence
# (a realistic LLM failure: dropping one of two legitimate citations).
_DROPPED_DUPLICATE = "Текст [[FN:3]] с сноской дважды и [[EN:1]] один раз."

# The same text with [[EN:1]] removed entirely.
_DROPPED_EN = "Текст [[FN:3]] с сноской [[FN:3]] дважды и один раз."

# The same text with [[FN:3]] duplicated to three occurrences.
_DUPLICATED_FN = (
    "Текст [[FN:3]] с сноской [[FN:3]] дважды и [[FN:3]] ещё и [[EN:1]] один раз."
)


class _FakeReport:
    """Minimal stand-in for DetectorReport exposing ``passed``/``failed_rules``."""

    def __init__(self, passed: bool, failed_rules: list[str]) -> None:
        self.passed = passed
        self.failed_rules = failed_rules


class _FakeDetectorConfig:
    """Minimal stand-in for DetectorConfig (never inspected by the loop)."""


def _make_fake_single_pass(sequence):
    """Return a fake ``run_single_pass`` yielding results from *sequence*.

    *sequence* is a list of ``(passed, failed_rules)`` tuples, one per
    call, in order.  The fake tracks the call count in a closure so each
    call returns the next entry in the sequence.

    The fake's ``final_text`` is produced by invoking the passed-in
    ``ollama_call`` on the input text (with a dummy temperature).  This lets
    the token-integrity tests drive corruption through a real ``ollama_call``
    fake that drops or duplicates a placeholder token, while the
    pass/fail/max-iterations tests use an ``ollama_call`` that echoes the
    input unchanged.
    """
    calls = {"n": 0}

    def fake_run_single_pass(
        text, language, ollama_call, detector_config, persona=None
    ):
        idx = calls["n"]
        calls["n"] += 1
        passed, failed_rules = sequence[idx]
        final_text = ollama_call(text, 0.0)
        return SinglePassResult(
            grammar_corrected_text=text,
            detector_report=_FakeReport(passed, failed_rules),
            feedback=None,
            final_text=final_text,
            grammar_prompt="",
            style_prompt="",
        )

    return fake_run_single_pass



def _make_dropping_ollama_call(drop_on_call: int, token: str):
    """Return an ``ollama_call`` that drops *token* on the *drop_on_call*-th call.

    The returned closure tracks the call count and, on the specified call
    number, returns a response with *token* removed from the input prompt.
    On all other calls it echoes the input unchanged.  This simulates the
    LLM failing to honor the placeholder-preservation rule on one specific
    pass.
    """
    calls = {"n": 0}

    def ollama_call(prompt: str, temperature: float) -> str:
        calls["n"] += 1
        if calls["n"] == drop_on_call:
            return prompt.replace(token, "")
        return prompt

    return ollama_call


def _make_duplicating_ollama_call(duplicate_on_call: int, token: str):
    """Return an ``ollama_call`` that duplicates *token* on a specific call.

    On the specified call number, the returned text contains *token* twice
    in a row; otherwise it echoes the input unchanged.
    """
    calls = {"n": 0}

    def ollama_call(prompt: str, temperature: float) -> str:
        calls["n"] += 1
        if calls["n"] == duplicate_on_call:
            return prompt.replace(token, token + token)
        return prompt

    return ollama_call


# ===========================================================================
# run_iteration_loop — pass/fail/max-iterations logic
# ===========================================================================


def test_run_iteration_loop_stops_immediately_when_first_pass_passes():
    """A passing first pass stops the loop after exactly one iteration."""
    fake = _make_fake_single_pass([(True, [])])

    with mock.patch(
        "src.pipeline.iteration_loop.run_single_pass", fake
    ):
        result = run_iteration_loop(
            _ORIGINAL, "ru", lambda p, t: _ORIGINAL, _FakeDetectorConfig(), max_iterations=3
        )

    assert isinstance(result, IterationResult)
    assert result.passed is True
    assert result.iterations_completed == 1
    assert result.final_text == _ORIGINAL
    assert result.warning is None
    assert len(result.history) == 1


def test_run_iteration_loop_continues_when_first_pass_fails():
    """A failing first pass feeds forward and runs a second iteration."""
    fake = _make_fake_single_pass(
        [(False, ["cliche_blacklist"]), (True, [])]
    )

    with mock.patch(
        "src.pipeline.iteration_loop.run_single_pass", fake
    ):
        result = run_iteration_loop(
            _ORIGINAL, "ru", lambda p, t: _ORIGINAL, _FakeDetectorConfig(), max_iterations=3
        )

    assert result.passed is True
    assert result.iterations_completed == 2
    assert len(result.history) == 2


def test_run_iteration_loop_stops_at_max_iterations_returns_best_scoring():
    """When the cap is reached, the best-scoring attempt is returned.

    failed_rules counts: [3, 1, 2] -> best is the middle attempt (1 rule).
    The ``ollama_call`` returns a distinct final text per call so the
    best-scoring attempt's text can be asserted precisely.
    """
    fake = _make_fake_single_pass(
        [
            (False, ["a", "b", "c"]),
            (False, ["x"]),
            (False, ["p", "q"]),
        ]
    )
    final_texts = ["final_0", "final_1", "final_2"]
    calls = {"n": 0}

    def ollama_call(prompt, temperature):
        idx = calls["n"]
        calls["n"] += 1
        return final_texts[idx]

    with mock.patch(
        "src.pipeline.iteration_loop.run_single_pass", fake
    ):
        result = run_iteration_loop(
            "start", "ru", ollama_call, _FakeDetectorConfig(), max_iterations=3
        )

    assert result.passed is False
    assert result.iterations_completed == 3
    assert len(result.history) == 3
    # Best-scoring attempt is index 1 (fewest failed rules), not the last.
    assert result.final_text == "final_1"
    assert "1 failed rule(s)" in result.warning


def test_run_iteration_loop_tie_breaking_returns_earliest_attempt():
    """On a tie in failed_rules, the EARLIEST attempt is returned.

    failed_rules counts: [2, 2, 2] -> all three tie at 2; min() returns the
    first (index 0), which is the desired tie-breaking behavior.
    """
    fake = _make_fake_single_pass(
        [
            (False, ["a", "b"]),
            (False, ["c", "d"]),
            (False, ["e", "f"]),
        ]
    )
    final_texts = ["final_0", "final_1", "final_2"]
    calls = {"n": 0}

    def ollama_call(prompt, temperature):
        idx = calls["n"]
        calls["n"] += 1
        return final_texts[idx]

    with mock.patch(
        "src.pipeline.iteration_loop.run_single_pass", fake
    ):
        result = run_iteration_loop(
            "start", "ru", ollama_call, _FakeDetectorConfig(), max_iterations=3
        )

    assert result.passed is False
    assert result.iterations_completed == 3
    # All three tie at 2 failed rules; earliest (index 0) wins.
    assert result.final_text == "final_0"



def test_run_iteration_loop_raises_value_error_for_max_iterations_zero():
    """max_iterations=0 raises ValueError before any pass runs."""
    with pytest.raises(ValueError):
        run_iteration_loop(
            _ORIGINAL, "ru", lambda p, t: "", _FakeDetectorConfig(), max_iterations=0
        )


def test_run_iteration_loop_raises_value_error_for_max_iterations_negative():
    """max_iterations=-1 raises ValueError before any pass runs."""
    with pytest.raises(ValueError):
        run_iteration_loop(
            _ORIGINAL, "ru", lambda p, t: "", _FakeDetectorConfig(), max_iterations=-1
        )


# ===========================================================================
# run_iteration_loop — token-integrity logic
# ===========================================================================


def test_token_integrity_broken_stops_loop_immediately():
    """A token dropped on iteration 1 stops the loop immediately.

    The fake ``ollama_call`` drops [[EN:1]] on the first call (the grammar
    pass of iteration 1).  The loop must return right away and not run a
    second iteration.
    """
    ollama_call = _make_dropping_ollama_call(drop_on_call=1, token="[[EN:1]]")
    fake = _make_fake_single_pass([(False, ["a"])])

    with mock.patch(
        "src.pipeline.iteration_loop.run_single_pass", fake
    ):
        result = run_iteration_loop(
            _ORIGINAL, "ru", ollama_call, _FakeDetectorConfig(), max_iterations=3
        )

    assert result.passed is False
    assert result.iterations_completed == 0
    assert len(result.history) == 1
    assert "integrity broken" in result.warning



def test_token_integrity_broken_returns_pre_corruption_text_not_corrupted_output():
    """On a break, the returned text is the pre-corruption input, not the output.

    Iteration 1 completes cleanly (the fake echoes the input unchanged), then
    iteration 2 drops [[EN:1]].  The returned ``final_text`` must be the
    last known-good text — the input to the corrupting pass (iteration 1's
    output, which equals the original) — not the corrupted output.
    """
    ollama_call = _make_dropping_ollama_call(drop_on_call=2, token="[[EN:1]]")
    fake = _make_fake_single_pass([(False, ["a"]), (False, ["b"])])

    with mock.patch(
        "src.pipeline.iteration_loop.run_single_pass", fake
    ):
        result = run_iteration_loop(
            _ORIGINAL, "ru", ollama_call, _FakeDetectorConfig(), max_iterations=3
        )

    assert result.passed is False
    assert result.final_text == _ORIGINAL
    assert result.final_text != _DROPPED_EN
    assert result.iterations_completed == 1
    assert len(result.history) == 2
    assert "integrity broken" in result.warning




def test_token_integrity_broken_on_first_iteration_returns_original_input():
    """A break on iteration 1 returns the original input unchanged.

    The fake drops [[FN:3]] on the first call.  Since this is the very first
    pass, the only known-good text is the original input.
    """
    ollama_call = _make_dropping_ollama_call(drop_on_call=1, token="[[FN:3]]")
    fake = _make_fake_single_pass([(False, ["a"])])

    with mock.patch(
        "src.pipeline.iteration_loop.run_single_pass", fake
    ):
        result = run_iteration_loop(
            _ORIGINAL, "ru", ollama_call, _FakeDetectorConfig(), max_iterations=3
        )

    assert result.passed is False
    assert result.final_text == _ORIGINAL
    assert len(result.history) == 1
    assert "integrity broken" in result.warning



def test_token_integrity_broken_when_token_duplicated():
    """A duplicated token is caught by Counter equality, not just a dropped one.

    The fake duplicates [[FN:3]] on the first call.  A plain set comparison
    would miss this (the set of tokens is unchanged); the multiset comparison
    must catch it and return the original input.
    """
    ollama_call = _make_duplicating_ollama_call(duplicate_on_call=1, token="[[FN:3]]")
    fake = _make_fake_single_pass([(False, ["a"])])

    with mock.patch(
        "src.pipeline.iteration_loop.run_single_pass", fake
    ):
        result = run_iteration_loop(
            _ORIGINAL, "ru", ollama_call, _FakeDetectorConfig(), max_iterations=3
        )

    assert result.passed is False
    assert result.final_text == _ORIGINAL
    assert result.final_text != _DUPLICATED_FN
    assert len(result.history) == 1
    assert "integrity broken" in result.warning



# ===========================================================================
# Placeholder-token multiset helpers
# ===========================================================================


def test_token_multiset_correctly_distinguishes_duplicate_vs_single_occurrence():
    """Counter counts each distinct token by its multiplicity.

    [[FN:3]] appears twice in the original; [[EN:1]] appears once.  A plain
    set would collapse both to a single occurrence — the Counter must not.
    """
    multiset = extract_placeholder_token_multiset(_ORIGINAL)

    assert multiset["[[FN:3]]"] == 2
    assert multiset["[[EN:1]]"] == 1

    # The set collapses duplicates; the multiset does not.
    token_set = extract_placeholder_token_set(_ORIGINAL)
    assert token_set == {"[[FN:3]]", "[[EN:1]]"}
    assert len(token_set) == 2
    assert len(multiset) == 2  # two distinct token types
    assert sum(multiset.values()) == 3  # three total token occurrences


def test_token_multiset_comparison_passes_for_text_with_zero_placeholder_tokens():
    """Text with no placeholder tokens yields an empty Counter.

    Two such texts compare equal (Counter() == Counter()), so the integrity
    check produces no false positives for plain text without footnotes.
    """
    plain_a = "Просто текст без сносок."
    plain_b = "Другой текст, тоже без сносок."

    assert extract_placeholder_token_multiset(plain_a) == Counter()
    assert extract_placeholder_token_multiset(plain_b) == Counter()
    assert extract_placeholder_token_multiset(plain_a) == extract_placeholder_token_multiset(plain_b)


def test_iteration_history_contains_all_attempts_in_order():
    """history records every attempt in the order they ran.

    With a never-passing setup over 3 iterations, history must contain all
    three attempts in order, and the returned result's history must match.
    """
    fake = _make_fake_single_pass(
        [
            (False, ["a"]),
            (False, ["b"]),
            (False, ["c"]),
        ]
    )
    final_texts = ["final_0", "final_1", "final_2"]
    calls = {"n": 0}

    def ollama_call(prompt, temperature):
        idx = calls["n"]
        calls["n"] += 1
        return final_texts[idx]

    with mock.patch(
        "src.pipeline.iteration_loop.run_single_pass", fake
    ):
        result = run_iteration_loop(
            "start", "ru", ollama_call, _FakeDetectorConfig(), max_iterations=3
        )

    assert len(result.history) == 3
    assert [r.final_text for r in result.history] == ["final_0", "final_1", "final_2"]
    # The returned history is the same tuple as the one on the result.
    assert result.history == tuple(result.history)


# ===========================================================================
# Integration test — real Ollama call (marked @pytest.mark.integration)
# ===========================================================================


@pytest.mark.integration
def test_run_iteration_loop_against_real_ollama_multi_iteration_case():
    """Runs the full iteration loop against a real Ollama instance.

    Uses a real Ollama call via ``make_ollama_call_fn`` (model="mistral")
    and a real RU ``DetectorConfig`` loaded from ``config/``.  The input is
    a short RU paragraph containing several obvious clichés, and the loop
    runs with ``max_iterations=2`` (up to 2 full single-pass cycles, i.e.
    up to 4 real model calls).

    Asserts only that the loop ran without error, that at least one
    iteration completed, and that the history is non-empty.  It does NOT
    assert ``passed=True`` — a real model is not guaranteed to fully satisfy
    all thresholds within 2 passes.  Skips gracefully if Ollama is not
    reachable, using the same skip-on-unreachable pattern as SPEC-002/004's
    integration tests.
    """
    # A short RU paragraph with several obvious clichés.
    raw_text = (
        "Удалённая работа играет важную роль в современном мире. "
        "Многие компании переходят на гибкий график, чтобы повысить "
        "эффективность труда и улучшить качество жизни сотрудников."
    )

    try:
        config = load_config("config", "ru")
        ollama_call = make_ollama_call_fn(model="mistral")
        result = run_iteration_loop(
            text=raw_text,
            language="ru",
            ollama_call=ollama_call,
            detector_config=config,
            max_iterations=2,
        )
    except Exception as exc:
        pytest.skip(
            f"Ollama not reachable or request failed: {exc}"
        )

    assert result.iterations_completed >= 1, (
        "Expected at least one iteration to complete, but "
        f"iterations_completed={result.iterations_completed}."
    )
    assert result.history, (
        "Expected a non-empty history of attempts, but history was empty."
    )


# ===========================================================================
# Language-integrity helpers (SPEC-005b §3) — detect_dominant_script
# ===========================================================================


def test_detect_dominant_script_cyrillic_text():
    """A Russian sentence is detected as cyrillic."""
    text = "Это русский текст для проверки определения доминирующего скрипта."
    assert detect_dominant_script(text) == "cyrillic"


def test_detect_dominant_script_latin_text():
    """An English sentence is detected as latin."""
    text = "This is an English sentence used to check the dominant script detection."
    assert detect_dominant_script(text) == "latin"


def test_detect_dominant_script_short_text_returns_unknown():
    """Very short text (fewer than 20 alphabetic chars) returns unknown."""
    assert detect_dominant_script("Hi") == "unknown"
    assert detect_dominant_script("Привет") == "unknown"


def test_detect_dominant_script_ignores_placeholder_tokens_and_digits():
    """Placeholder tokens and digits must not skew the script count.

    The tokens [[FN:3]] and [[EN:7]] contain Latin letters (FN, EN) and
    digits; if they were counted they'd add Latin characters.  They must be
    stripped before counting, and digits ignored, so the dominant script is
    still correctly detected as cyrillic.
    """
    text = (
        "Это [[FN:3]] русский текст [[EN:7]] с цифрами 12345 "
        "и ещё достаточно букв для проверки скрипта."
    )
    assert detect_dominant_script(text) == "cyrillic"


# ===========================================================================
# Language-integrity helpers (SPEC-005b §3) — check_language_integrity
# ===========================================================================


def test_check_language_integrity_passes_when_matching():
    """Matching language/script returns (True, None)."""
    ru_text = "Это русский текст для проверки целостности языка в системе."
    en_text = "This is English text used to verify language integrity checking."
    assert check_language_integrity(ru_text, "ru") == (True, None)
    assert check_language_integrity(en_text, "en") == (True, None)


def test_check_language_integrity_fails_when_mismatched():
    """A mismatched language/script returns (False, a warning)."""
    en_text = "This is English text here obviously and clearly for the test."
    ok, warning = check_language_integrity(en_text, "ru")
    assert ok is False
    assert warning is not None
    assert "cyrillic" in warning
    assert "latin" in warning


def test_check_language_integrity_passes_on_unknown_short_text():
    """Short text (unknown script) passes without a warning."""
    ok, warning = check_language_integrity("Hi", "ru")
    assert ok is True
    assert warning is None


# ===========================================================================
# run_iteration_loop — language-integrity logic (SPEC-005b §3)
# ===========================================================================


def test_run_iteration_loop_stops_on_language_drift_returns_pre_corruption_text():
    """A fully-English pass when Russian is expected stops the loop.

    Simulates the real bug found in SPEC-006's manual review: the model
    translated a Russian document into English.  The fake ``ollama_call``
    returns fully-English text on its first call.  The loop must stop
    immediately, return the original Russian input unchanged, and warn
    about the language drift.
    """
    ru_input = (
        "Это русский текст для проверки языка. Он содержит достаточно "
        "букв для определения доминирующего скрипта."
    )
    en_output = (
        "This is fully English text that has drifted away from the "
        "original Russian language entirely."
    )

    def ollama_call(prompt, temperature):
        return en_output

    fake = _make_fake_single_pass([(False, ["a"])])

    with mock.patch(
        "src.pipeline.iteration_loop.run_single_pass", fake
    ):
        result = run_iteration_loop(
            ru_input, "ru", ollama_call, _FakeDetectorConfig(), max_iterations=3
        )

    assert result.passed is False
    assert result.final_text == ru_input
    assert result.iterations_completed == 0
    assert len(result.history) == 1
    assert "language" in result.warning.lower()



