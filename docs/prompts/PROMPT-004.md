# PROMPT-004: Single-Pass Pipeline Engine & Langflow Adapter — Implementation Prompts

Reference: docs/specs/SPEC-004-langflow-flow-assembly.md (read in full —
especially section 2, the grammar-then-detect-then-style sequencing
decision, and section 4, which is verified against the live Langflow UI).

Execute these 5 steps in order. Steps 1-3 need no Langflow/Ollama running.
Step 4 needs Ollama running. Step 5 needs Langflow running.

---

## STEP 1: Engine Models and Ollama Call Function

```
@workspace
Reference: docs/specs/SPEC-004-langflow-flow-assembly.md, section 3.

IMPLEMENTER RULES — follow without exception:
1. Return the COMPLETE file — no partial output.
2. Functional style: pure functions, explicit inputs/outputs.
3. Do not assume imports/variables not shown here.
4. Implement only what is asked.

Create file: src/pipeline/single_pass.py

Requirements for this step — implement ONLY these, not run_single_pass yet:

- Module-level constants: GRAMMAR_PASS_TEMPERATURE: float = 0.3,
  STYLE_PASS_TEMPERATURE: float = 0.8
- SinglePassResult frozen dataclass exactly as shown in SPEC-004 section 3
  (grammar_corrected_text, detector_report, feedback, final_text,
  grammar_prompt, style_prompt)
- `make_ollama_call_fn(model: str = "mistral", host: str = "http://localhost:11434", timeout: int = 60) -> Callable[[str, float], str]`
  per SPEC-004 section 3's docstring: returns a closure that POSTs to
  {host}/api/generate with json={"model": model, "prompt": prompt,
  "stream": False, "options": {"temperature": temperature}}, calls
  resp.raise_for_status(), returns resp.json()["response"]. Import
  `requests` inside the closure (consistent with the pattern already
  used in tests/test_promptgen.py's _real_ollama_call). Do not catch
  any exceptions — let them propagate.

Do not implement run_single_pass in this step.

### Acceptance Check
`python -c "from src.pipeline.single_pass import make_ollama_call_fn, GRAMMAR_PASS_TEMPERATURE, STYLE_PASS_TEMPERATURE; print(GRAMMAR_PASS_TEMPERATURE, STYLE_PASS_TEMPERATURE)"`
Should print `0.3 0.8`.
```

---

## STEP 2: run_single_pass Orchestration Function

```
@workspace
Reference: docs/specs/SPEC-004-langflow-flow-assembly.md, section 2
(the exact sequencing — grammar pass FIRST, then detector report on the
GRAMMAR-CORRECTED text, then style pass) and section 3
(run_single_pass signature). Read src/pipeline/single_pass.py from Step 1
— add to it, don't recreate the constants/dataclass. Also read
src/detector/report.py (build_detector_report) and
src/promptgen/feedback.py, src/promptgen/prompts.py
(build_feedback_from_report, build_grammar_pass_prompt,
build_style_pass_prompt) — import from there, do not reimplement.

IMPLEMENTER RULES — same as Step 1.

Add to src/pipeline/single_pass.py (return the complete file including
everything from Step 1 unchanged):

`run_single_pass(text, language, ollama_call, detector_config, persona=None) -> SinglePassResult`

Implement EXACTLY this sequence (per SPEC-004 section 2 — do not
reorder, do not compute the detector report on the raw input text):

1. grammar_prompt = build_grammar_pass_prompt(text, language)
2. grammar_corrected_text = ollama_call(grammar_prompt, GRAMMAR_PASS_TEMPERATURE)
3. detector_report = build_detector_report(grammar_corrected_text, detector_config)
4. feedback = build_feedback_from_report(detector_report, language)
5. style_prompt = build_style_pass_prompt(grammar_corrected_text, feedback, language, persona)
6. final_text = ollama_call(style_prompt, STYLE_PASS_TEMPERATURE)
7. Return SinglePassResult(grammar_corrected_text, detector_report,
   feedback, final_text, grammar_prompt, style_prompt)

Do not catch exceptions from ollama_call — let them propagate per
SPEC-004 section 3.

### Acceptance Check
Manually construct a fake ollama_call that records its calls (e.g. a
closure appending (prompt, temperature) tuples to a list) and returns a
distinguishable fixed string for each call. Run run_single_pass with it,
then inspect the recorded calls list — confirm exactly 2 calls were
made, first with GRAMMAR_PASS_TEMPERATURE, second with
STYLE_PASS_TEMPERATURE.
```

---

## STEP 3: Unit Tests (Fake Ollama Call)

```
@workspace
Reference: docs/specs/SPEC-004-langflow-flow-assembly.md, section 6's
first test list (engine layer, no Langflow needed). Read
src/pipeline/single_pass.py from Steps 1-2.

IMPLEMENTER RULES — same as Step 1.

Create file: tests/test_single_pass.py

Requirements:
- Implement all 6 test functions listed in SPEC-004 section 6's engine
  layer list.
- For test_run_single_pass_detector_report_computed_on_grammar_corrected_text_not_raw:
  use a fake ollama_call where the FIRST call (grammar pass) returns
  text with a known cliché REMOVED compared to the input, and assert
  the resulting detector_report does NOT flag that cliché (proving the
  report was computed on the grammar-corrected text, not the original
  input which still had the cliché).
- Use a fake ollama_call that's a plain function or closure recording
  (prompt, temperature) pairs it was called with, for the temperature
  assertion tests.
- Each test asserts specific values, not just "no exception raised."

### Acceptance Check
Run: `pytest tests/test_single_pass.py -v`
All 6 tests must pass.
```

---

## STEP 4: Integration Test (Real Ollama)

```
@workspace
Reference: docs/specs/SPEC-004-langflow-flow-assembly.md, section 6's
integration test requirement. Read tests/test_promptgen.py's existing
integration test pattern (skip-on-unreachable, pytest.ini marker) and
reuse the same approach — do not invent a different skip pattern.

IMPLEMENTER RULES — same as Step 1.

Add to tests/test_single_pass.py:

Requirements:
- Implement test_run_single_pass_against_real_ollama_produces_different_output()
  marked @pytest.mark.integration.
- Use make_ollama_call_fn() (real Ollama call, model="mistral") wrapped
  so run_single_pass's Callable[[str, float], str] signature is satisfied
  — make_ollama_call_fn already returns exactly that shape, use it directly.
- Use a short RU paragraph with an obvious cliché as input text, and a
  real DetectorConfig loaded via load_config("config", "ru").
- Wrap the whole call in try/except catching any exception, and
  pytest.skip() with a clear message on failure — same pattern as
  SPEC-002's integration test.
- Assert (only if no skip occurred): result.final_text != <original text>,
  result.grammar_prompt is non-empty, result.style_prompt is non-empty.
- This test does NOT assert the output is objectively "better" — only
  that something changed and the pipeline ran end-to-end without error.

### Acceptance Check
With Ollama running and mistral pulled:
`pytest tests/test_single_pass.py -v -m integration`
Should pass (not skip) if Ollama is reachable. Confirm actual runtime —
two real model calls (grammar+style) will take longer than SPEC-002's
single-call integration test (14.8s) — expect roughly 20-60s depending
on your hardware, this is normal, not a hang.
```

---

## STEP 5: Langflow Adapter

```
@workspace
Reference: docs/specs/SPEC-004-langflow-flow-assembly.md, section 4 —
this code is verified against the live running Langflow 1.11.1 UI, use
it exactly as given, do not "improve" the import paths or class
structure. Read src/pipeline/single_pass.py and
src/detector/config_loader.py — import from there.

IMPLEMENTER RULES — same as Step 1, EXCEPT this file is NOT expected to
be testable via pytest (it requires the Langflow runtime) — do not
write pytest tests for this file.

Create file: custom_components/humanizer_pipeline.py

Requirements:
- Implement it exactly per SPEC-004 section 4's verified code — the
  HumanizerPipelineComponent class with the 4 inputs (input_text,
  language, persona, ollama_model) and the single output producing a
  Data object.
- Match the exact import paths given (lfx.custom.custom_component.component,
  lfx.io, lfx.schema.data) — these are confirmed correct for the
  installed Langflow 1.11.1, do not substitute alternatives.

### Acceptance Check (manual, not pytest — per SPEC-004 section 6's checklist)
1. Copy or symlink custom_components/humanizer_pipeline.py into wherever
   Langflow loads custom components from for this installation (check
   Langflow's settings/docs for the custom components directory path —
   commonly configured via the LANGFLOW_COMPONENTS_PATH environment
   variable, or a default folder Langflow watches; confirm which applies
   to this 1.11.1 install rather than assuming).
2. Start Langflow (`uv run langflow run` or however it was started
   previously), open a flow, search for "Text Humanizer" in the
   component panel — confirm it appears.
3. Drag it onto canvas, provide a sample RU paragraph with a cliché as
   input_text, leave language="ru", persona empty, ollama_model="mistral".
4. Run it (with Ollama running). Confirm output_text is produced and
   differs from the input.
5. Stop Ollama, run again — confirm the UI shows a clear error rather
   than hanging silently.

Report back which of these 5 checklist items passed and which didn't —
do not mark this step done without actually performing the manual check.
```

---

## After All 5 Steps Complete

```powershell
pytest tests/test_single_pass.py -v
git add src/pipeline custom_components tests/test_single_pass.py
git commit -m "feat: Implement single-pass pipeline engine and Langflow adapter [SPEC-004]"
git push
git status
```

Then bring results back to Claude for Stage 6 (validity review), including
the manual Langflow checklist results from Step 5 — before marking SPEC-004
complete.
