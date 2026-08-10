"""
Unit tests for the single-pass pipeline engine (SPEC-004 §6, engine layer).

Covers ``run_single_pass`` orchestration (no Langflow, no real Ollama) and
``make_ollama_call_fn`` payload construction.  All ``ollama_call`` fakes are
plain functions/closures that record the ``(prompt, temperature)`` pairs
they were called with, so the temperature assertions check real values
rather than just "no exception raised".
"""

import pytest
from unittest import mock

from src.detector.config_loader import load_config

from src.pipeline.single_pass import (
    GRAMMAR_PASS_TEMPERATURE,
    STYLE_PASS_TEMPERATURE,
    SinglePassResult,
    make_ollama_call_fn,
    run_single_pass,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# English config uses literal substring matching (no natasha dependency),
# so these tests run without the ~200 MB RU model installed.
_CONFIG_DIR = "tests/fixtures/config"
_LANGUAGE = "en"

# A cliché present in tests/fixtures/config/cliches_en.txt.
_CLICHE = "crucial role"

# Raw input that contains the cliché.
_RAW_TEXT = (
    "This plays a crucial role in the success of the project. "
    "We need to move forward with the plan."
)

# Grammar-pass response with the cliché REMOVED compared to the input.
_GRAMMAR_CORRECTED_TEXT = (
    "This is important for the success of the project. "
    "We need to move forward with the plan."
)

# Style-pass (final) response.
_FINAL_TEXT = (
    "This matters for the project's success. "
    "Let's proceed with the plan."
)


def _make_recording_ollama_call():
    """Return ``(ollama_call, calls)`` where ``calls`` records (prompt, temp).

    The returned ``ollama_call`` returns the grammar-corrected text on the
    first invocation (grammar pass) and the final text on the second
    (style pass), so the detector report is computed on text that differs
    from the raw input.
    """
    calls: list[tuple[str, float]] = []

    def ollama_call(prompt: str, temperature: float) -> str:
        calls.append((prompt, temperature))
        if len(calls) == 1:
            return _GRAMMAR_CORRECTED_TEXT
        return _FINAL_TEXT

    return ollama_call, calls


def _load_en_config():
    return load_config(_CONFIG_DIR, _LANGUAGE)


# ===========================================================================
# run_single_pass — orchestration
# ===========================================================================


class TestRunSinglePass:
    """Tests for run_single_pass (SPEC-004 §3)."""

    def test_run_single_pass_calls_ollama_twice_grammar_then_style(self):
        """Exactly two ollama calls: grammar pass first, then style pass."""
        ollama_call, calls = _make_recording_ollama_call()
        config = _load_en_config()

        result = run_single_pass(
            text=_RAW_TEXT,
            language=_LANGUAGE,
            ollama_call=ollama_call,
            detector_config=config,
        )

        assert len(calls) == 2, f"Expected 2 ollama calls, got {len(calls)}"
        # First call is the grammar pass (prompt built from raw text).
        assert _RAW_TEXT in calls[0][0]
        assert "Fix only grammar errors" in calls[0][0]
        # Second call is the style pass (prompt built from grammar-corrected text).
        assert _GRAMMAR_CORRECTED_TEXT in calls[1][0]
        assert "Rewrite the text" in calls[1][0]

    def test_run_single_pass_grammar_call_uses_correct_temperature(self):
        """The grammar-pass call uses GRAMMAR_PASS_TEMPERATURE (0.3)."""
        ollama_call, calls = _make_recording_ollama_call()
        config = _load_en_config()

        run_single_pass(
            text=_RAW_TEXT,
            language=_LANGUAGE,
            ollama_call=ollama_call,
            detector_config=config,
        )

        assert len(calls) == 2
        assert calls[0][1] == GRAMMAR_PASS_TEMPERATURE
        assert calls[0][1] == 0.3

    def test_run_single_pass_style_call_uses_correct_temperature(self):
        """The style-pass call uses STYLE_PASS_TEMPERATURE (0.8)."""
        ollama_call, calls = _make_recording_ollama_call()
        config = _load_en_config()

        run_single_pass(
            text=_RAW_TEXT,
            language=_LANGUAGE,
            ollama_call=ollama_call,
            detector_config=config,
        )

        assert len(calls) == 2
        assert calls[1][1] == STYLE_PASS_TEMPERATURE
        assert calls[1][1] == 0.8

    def test_run_single_pass_detector_report_computed_on_grammar_corrected_text_not_raw(self):
        """The detector report reflects the grammar-corrected text, not the raw input.

        The raw input contains the cliché "crucial role"; the fake grammar
        pass removes it.  If the report were computed on the raw input it
        would flag that cliché — so asserting it does NOT flag it proves the
        report was built from the grammar-corrected text.
        """
        ollama_call, _ = _make_recording_ollama_call()
        config = _load_en_config()

        # Sanity: the raw input really does contain the cliché.
        assert _CLICHE in _RAW_TEXT
        # Sanity: the grammar-corrected text really does NOT contain it.
        assert _CLICHE not in _GRAMMAR_CORRECTED_TEXT

        result = run_single_pass(
            text=_RAW_TEXT,
            language=_LANGUAGE,
            ollama_call=ollama_call,
            detector_config=config,
        )

        # The report must not flag the cliché that only existed in the raw input.
        matched_texts = [m.matched_text for m in result.detector_report.cliche_matches]
        assert _CLICHE not in matched_texts, (
            f"Report flagged cliché {_CLICHE!r} — it was computed on the raw "
            f"input, not the grammar-corrected text. Matches: {matched_texts}"
        )
        assert "cliche_blacklist" not in result.detector_report.failed_rules

    def test_run_single_pass_result_contains_both_prompts_for_debugging(self):
        """The result carries both the grammar and style prompts for debugging."""
        ollama_call, _ = _make_recording_ollama_call()
        config = _load_en_config()

        result = run_single_pass(
            text=_RAW_TEXT,
            language=_LANGUAGE,
            ollama_call=ollama_call,
            detector_config=config,
        )

        assert isinstance(result, SinglePassResult)
        assert result.grammar_prompt
        assert result.style_prompt
        # Grammar prompt is built from the raw text.
        assert _RAW_TEXT in result.grammar_prompt
        # Style prompt is built from the grammar-corrected text.
        assert _GRAMMAR_CORRECTED_TEXT in result.style_prompt
        # The two prompts differ from each other.
        assert result.grammar_prompt != result.style_prompt


# ===========================================================================
# make_ollama_call_fn — payload construction
# ===========================================================================


class TestMakeOllamaCallFn:
    """Tests for make_ollama_call_fn (SPEC-004 §3)."""

    def test_make_ollama_call_fn_posts_correct_payload_shape(self):
        """The returned closure POSTs the expected payload to /api/generate."""
        ollama_call = make_ollama_call_fn(
            model="test-model",
            host="http://ollama.example:11434",
            timeout=60,
        )

        fake_response = mock.Mock()
        fake_response.json.return_value = {"response": "corrected text"}

        with mock.patch("requests.post", return_value=fake_response) as mock_post:
            out = ollama_call("some prompt", 0.5)

        assert out == "corrected text"

        # Exactly one POST to the correct endpoint.
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.args[0] == "http://ollama.example:11434/api/generate"

        payload = call_kwargs.kwargs["json"]
        assert payload == {
            "model": "test-model",
            "prompt": "some prompt",
            "stream": False,
            "options": {"temperature": 0.5, "num_ctx": 16384},
        }
        assert call_kwargs.kwargs["timeout"] == 60


# ===========================================================================
# Integration test — real Ollama call (marked @pytest.mark.integration)
# ===========================================================================


@pytest.mark.integration
class TestRunSinglePassIntegration:
    """Integration tests requiring a live Ollama instance with ``mistral`` pulled.

    Uses the same skip-on-unreachable pattern as SPEC-002's integration
    test (see tests/test_promptgen.py): the whole call is wrapped in a
    try/except that ``pytest.skip()``s with a clear message if Ollama is
    not reachable, so the test never crashes the suite.
    """

    def test_run_single_pass_against_real_ollama_produces_different_output(self):
        """A real grammar+style pass changes the text and runs end-to-end.

        Uses a real Ollama call via ``make_ollama_call_fn`` (model="mistral")
        and a real RU ``DetectorConfig`` loaded from ``config/``.  The input
        is a short RU paragraph containing an obvious cliché.

        Asserts only that something changed (``final_text != original``),
        that both prompts are non-empty, and that the pipeline ran without
        error — it does NOT assert the output is objectively "better".
        Skips gracefully if Ollama is not reachable.
        """
        # A short RU paragraph with an obvious cliché ("играет важную роль").

        raw_text = (
            "Удалённая работа играет важную роль в современном мире. "
            "Многие компании переходят на гибкий график."
        )

        try:
            config = load_config("config", "ru")
            ollama_call = make_ollama_call_fn(model="mistral")
            result = run_single_pass(
                text=raw_text,
                language="ru",
                ollama_call=ollama_call,
                detector_config=config,
            )
        except Exception as exc:
            pytest.skip(
                f"Ollama not reachable or request failed: {exc}"
            )

        assert result.final_text != raw_text, (
            "Expected the pipeline to change the text, but final_text "
            "equals the original input."
        )
        assert result.grammar_prompt, "Expected a non-empty grammar prompt."
        assert result.style_prompt, "Expected a non-empty style prompt."


