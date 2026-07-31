# SPEC-004: Langflow Flow Assembly (.txt path, single-pass)

Feature:      Wires SPEC-001 (detector) and SPEC-002 (prompt builders) into
              an actual running Langflow flow with real Ollama calls, for
              plain .txt input. ONE pass only (grammar + style) — no
              iteration loop yet (that's SPEC-005), no .docx yet (SPEC-006).
Priority:     P2
Status:       Planning
Dependencies: SPEC-001 (DetectorReport, build_detector_report),
              SPEC-002 (RewriteFeedback, build_feedback_from_report,
              build_grammar_pass_prompt, build_style_pass_prompt)
Related Docs: APP-OVERVIEW.md §2.3, §2.4, §3 (architecture diagram);
              SPEC-005 (wraps this spec's single-pass function in a loop)

---

## 1. Overview — Two Layers, Different Rigor

This spec has two genuinely different parts, and they get different
testing standards:

1. **The engine** (`src/pipeline/single_pass.py`) — a pure Python
   orchestration function with no Langflow dependency. Fully unit-testable
   with a fake Ollama call, exactly like SPEC-001/002. This is where the
   actual logic lives and where correctness matters most.
2. **The Langflow adapter** (`custom_components/humanizer_pipeline.py`) —
   a thin wrapper exposing the engine as a Langflow Component. This is
   verified manually in the Langflow UI, not via pytest, because testing
   a Langflow component in isolation requires the running Langflow app
   and its API surface changes between versions.

**Confirmed 2026-07-24:** installed Langflow version is 1.11.1, using the
`lfx` import path (the legacy `langflow.custom` path was replaced in
Langflow 1.7). Section 4's code skeleton is written against this
confirmed version — residual risk is limited to documentation-vs-actual-
behavior discrepancies (docs can lag a shipped release), not
version-family uncertainty.

**Acceptance criteria:**
- Engine layer: `run_single_pass` fully unit-tested with a fake Ollama call
- Engine layer: one integration test against real Ollama proves an actual
  two-call pass (grammar then style) produces non-empty, non-identical output
- Adapter layer: loads into Langflow's UI without error, and running it
  manually on a sample paragraph produces visibly different (improved)
  output — this is a manual check, documented as such, not a pytest test

---

## 2. Critical Design Decision: When Is the Detector Report Computed?

**The DetectorReport used for style-pass feedback is computed on the
GRAMMAR-CORRECTED text, not on the raw input.** Sequence:

```
raw_text
   → build_grammar_pass_prompt(raw_text) → ollama_call → grammar_corrected_text
   → build_detector_report(grammar_corrected_text)   [SPEC-001]
   → build_feedback_from_report(report)               [SPEC-002]
   → build_style_pass_prompt(grammar_corrected_text, feedback) → ollama_call → final_text
```

**Rationale:** detecting clichés/rhythm/etc. on text that hasn't had its
grammar fixed yet risks flagging issues that the grammar pass would have
changed anyway (e.g. a run-on sentence merged during grammar correction
would have a different rhythm profile after the merge). Running detection
on the post-grammar-pass text gives the style pass accurate, current
feedback about the text it's actually about to rewrite.

---

## 3. Engine Layer Interface

```python
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class SinglePassResult:
    grammar_corrected_text: str
    detector_report: "DetectorReport"        # from SPEC-001, computed post-grammar-pass
    feedback: "RewriteFeedback"               # from SPEC-002
    final_text: str
    grammar_prompt: str                        # kept for debugging/logging
    style_prompt: str                          # kept for debugging/logging

def run_single_pass(
    text: str,
    language: str,
    ollama_call: Callable[[str, float], str],
    detector_config: "DetectorConfig",
    persona: str | None = None,
) -> SinglePassResult:
    """
    Runs one grammar-pass + style-pass cycle per section 2's sequence.
    ollama_call takes (prompt, temperature) -> response text — temperature
    is passed explicitly here (unlike SPEC-002's judge_title_echo, which
    didn't need temperature control since it's a deterministic yes/no
    judgment call).

    Grammar pass uses temperature=0.3. Style pass uses temperature=0.8
    (both hardcoded as named constants GRAMMAR_PASS_TEMPERATURE and
    STYLE_PASS_TEMPERATURE at module level — not magic numbers inline —
    so SPEC-005 or future tuning can reference/override them by name).

    Does not catch exceptions from ollama_call — connection/timeout
    errors propagate to the caller, consistent with SPEC-002 section 6's
    pattern (orchestration layer, i.e. whatever calls run_single_pass,
    decides retry/timeout policy).
    """

GRAMMAR_PASS_TEMPERATURE: float = 0.3
STYLE_PASS_TEMPERATURE: float = 0.8

def make_ollama_call_fn(model: str = "mistral", host: str = "http://localhost:11434", timeout: int = 60) -> Callable[[str, float], str]:
    """
    Returns a closure suitable for use as `ollama_call` in run_single_pass.
    The returned function POSTs to {host}/api/generate with the given
    model, the prompt, stream=False, and options={"temperature": temperature}.
    Raises requests.exceptions.RequestException subtypes on failure —
    does not swallow them (per the "propagate, don't catch" rule above).
    timeout=60 (not 30, unlike SPEC-002's test helper) because a full
    grammar+style pass on a real paragraph takes longer than a single
    short title-echo judgment call.
    """
```

---

## 4. Langflow Adapter (verified against installed version 1.11.1)

**Confirmed 2026-07-24:** installed Langflow version is 1.11.1, which uses
the `lfx` import path (not the legacy `langflow.custom`, which was
replaced in Langflow 1.7). The skeleton below uses `lfx` imports directly
— no fallback needed, this is the correct path for the installed version.

```python
# custom_components/humanizer_pipeline.py

from lfx.custom import Component
from lfx.io import MessageTextInput, DropdownInput, Output
from lfx.schema import Message

from src.pipeline.single_pass import run_single_pass, make_ollama_call_fn
from src.detector.config_loader import load_config

class HumanizerPipelineComponent(Component):
    display_name = "Text Humanizer (single pass)"
    description = "Runs one grammar+style rewrite pass using the local detector and Ollama."
    icon = "custom_components"
    name = "HumanizerPipelineComponent"

    inputs = [
        MessageTextInput(name="input_text", display_name="Input Text"),
        DropdownInput(name="language", display_name="Language", options=["ru", "en"], value="ru"),
        MessageTextInput(name="persona", display_name="Persona (optional)", value=""),
        MessageTextInput(name="ollama_model", display_name="Ollama Model", value="mistral"),
    ]

    outputs = [
        Output(display_name="Humanized Text", name="output_text", method="build_output"),
    ]

    def build_output(self) -> Message:
        config = load_config("config", self.language)
        ollama_call = make_ollama_call_fn(model=self.ollama_model)
        persona = self.persona if self.persona else None
        result = run_single_pass(
            text=self.input_text,
            language=self.language,
            ollama_call=ollama_call,
            detector_config=config,
            persona=persona,
        )
        return Message(text=result.final_text)
```

If this still fails to load in the Langflow UI despite matching the
documented 1.11.1 API, check Langflow's own "Custom Component" starter
(Helpers panel → drag Custom Component → Code) as the definitive
version-correct reference — this skeleton is verified against
documentation, not against a live load test, so a small discrepancy is
still possible.

---

## 5. Edge Cases & Constraints

| Case | Behavior |
|---|---|
| Ollama unreachable during a run | Exception propagates (per section 3) — the Langflow UI will show the error, which is acceptable for MVP; no silent empty output |
| Empty input text | `run_single_pass` still calls through — grammar pass on empty text is the model's problem to handle gracefully or not; not specifically guarded against in this spec (SPEC-001's detector already handles empty text correctly per its own spec, so `build_detector_report` won't crash) |
| persona="" (empty string) vs persona=None | Adapter converts empty string to None before calling the engine (per section 4) — SPEC-002's build_style_pass_prompt already treats None as "no persona section" |
| Very long input (multi-page document) | Out of scope for this spec's testing — SPEC-004 targets short-to-medium paragraphs; document as a known untested case, not a guaranteed limitation |

---

## 6. Testing Requirements

Engine layer (no Langflow needed):
```
test_run_single_pass_calls_ollama_twice_grammar_then_style()
test_run_single_pass_grammar_call_uses_correct_temperature()
test_run_single_pass_style_call_uses_correct_temperature()
test_run_single_pass_detector_report_computed_on_grammar_corrected_text_not_raw()
test_run_single_pass_result_contains_both_prompts_for_debugging()
test_make_ollama_call_fn_posts_correct_payload_shape()
```

For `test_run_single_pass_detector_report_computed_on_grammar_corrected_text_not_raw`:
use a fake `ollama_call` where the grammar-pass response is DIFFERENT
text from the input (e.g. input has a cliché, fake grammar-pass response
has the cliché removed) — assert the report reflects the grammar-corrected
text's content, not the original input's.

Integration test (marked `@pytest.mark.integration`, same skip-on-unreachable
pattern as SPEC-002):
```
test_run_single_pass_against_real_ollama_produces_different_output()
```
Asserts: `final_text != raw_text` (something changed), both prompts are
non-empty, and no exception was raised — does NOT assert the output is
"better" in any measurable sense (that's what SPEC-001's detector
re-run in SPEC-005's loop is for).

**Langflow adapter — manual verification checklist (not automated):**
- [ ] Component loads in Langflow UI without error
- [ ] Running with a sample RU paragraph containing a known cliché
      produces output with the cliché addressed
- [ ] Running with persona left empty behaves same as SPEC-002's
      no-persona case
- [ ] Running with Ollama stopped shows a clear error in the UI, not a
      silent hang or blank output

---

## 7. What SPEC-005 Depends On From This Spec

- `run_single_pass` as the atomic "one iteration" building block —
  SPEC-005's loop calls this repeatedly, re-checking
  `result.detector_report.passed` after each call to decide whether to
  continue or stop
- `GRAMMAR_PASS_TEMPERATURE`/`STYLE_PASS_TEMPERATURE` constants — SPEC-005
  should reference these by name, not redefine its own temperature values
- `make_ollama_call_fn` — reused as-is, SPEC-005 doesn't need its own
  Ollama-calling logic
