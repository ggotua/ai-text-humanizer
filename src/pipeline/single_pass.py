"""Single-pass pipeline engine (SPEC-004 В§3).

This module contains the pure-Python orchestration layer for one
grammar-pass + style-pass cycle.  It has no Langflow dependency вЂ” the
Langflow adapter in ``custom_components/humanizer_pipeline.py`` is a thin
wrapper around ``run_single_pass`` (implemented in a later step).
"""

from dataclasses import dataclass
from typing import Callable

from src.detector.report import build_detector_report
from src.promptgen.feedback import build_feedback_from_report
from src.promptgen.prompts import (
    build_grammar_pass_prompt,
    build_style_pass_prompt,
)

GRAMMAR_PASS_TEMPERATURE: float = 0.3
STYLE_PASS_TEMPERATURE: float = 0.8


@dataclass(frozen=True)
class SinglePassResult:
    grammar_corrected_text: str
    detector_report: "DetectorReport"  # from SPEC-001, computed post-grammar-pass
    feedback: "RewriteFeedback"  # from SPEC-002
    final_text: str
    grammar_prompt: str  # kept for debugging/logging
    style_prompt: str  # kept for debugging/logging


def make_ollama_call_fn(
    model: str = "mistral",
    host: str = "http://localhost:11434",
    timeout: int = 300,
) -> Callable[[str, float], str]:
    """Return a closure suitable for use as ``ollama_call`` in run_single_pass.

    The returned function POSTs to ``{host}/api/generate`` with the given
    model, the prompt, ``stream=False``, and
    ``options={"temperature": temperature}``.  Raises
    ``requests.exceptions.RequestException`` subtypes on failure вЂ” does not
    swallow them (per the "propagate, don't catch" rule above).

    ``timeout=60`` (not 30, unlike SPEC-002's test helper) because a full
    grammar+style pass on a real paragraph takes longer than a single
    short title-echo judgment call.
    """

    def ollama_call(prompt: str, temperature: float) -> str:
        import requests

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        resp = requests.post(
            f"{host}/api/generate",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["response"]

    return ollama_call


def run_single_pass(
    text: str,
    language: str,
    ollama_call: Callable[[str, float], str],
    detector_config: "DetectorConfig",
    persona: str | None = None,
) -> SinglePassResult:
    """Run one grammar-pass + style-pass cycle per SPEC-004 section 2's sequence.

    ``ollama_call`` takes ``(prompt, temperature)`` -> response text вЂ”
    temperature is passed explicitly here (unlike SPEC-002's
    ``judge_title_echo``, which didn't need temperature control since it's
    a deterministic yes/no judgment call).

    Grammar pass uses ``GRAMMAR_PASS_TEMPERATURE`` (0.3).  Style pass uses
    ``STYLE_PASS_TEMPERATURE`` (0.8) вЂ” both hardcoded as named module-level
    constants, not magic numbers inline, so SPEC-005 or future tuning can
    reference/override them by name.

    Does not catch exceptions from ``ollama_call`` вЂ” connection/timeout
    errors propagate to the caller, consistent with SPEC-002 section 6's
    pattern (the orchestration layer, i.e. whatever calls ``run_single_pass``,
    decides retry/timeout policy).
    """
    grammar_prompt = build_grammar_pass_prompt(text, language)
    grammar_corrected_text = ollama_call(grammar_prompt, GRAMMAR_PASS_TEMPERATURE)
    detector_report = build_detector_report(grammar_corrected_text, detector_config)
    feedback = build_feedback_from_report(detector_report, language)
    style_prompt = build_style_pass_prompt(
        grammar_corrected_text, feedback, language, persona
    )
    final_text = ollama_call(style_prompt, STYLE_PASS_TEMPERATURE)

    return SinglePassResult(
        grammar_corrected_text=grammar_corrected_text,
        detector_report=detector_report,
        feedback=feedback,
        final_text=final_text,
        grammar_prompt=grammar_prompt,
        style_prompt=style_prompt,
    )
