# PROMPT-002: Prompt Templates & Title-Echo Judge — Implementation Prompts

Reference: docs/specs/SPEC-002-prompt-templates.md (read in full first).
Also read src/detector/models.py and src/detector/report.py from SPEC-001 —
this spec consumes DetectorReport/RuleMatch, do not redefine them.

Execute these 6 steps in order, testing after each.

---

## STEP 1: Models

```
@workspace
Reference: docs/specs/SPEC-002-prompt-templates.md, section 2.

IMPLEMENTER RULES — follow without exception:
1. Return the COMPLETE file — no partial output.
2. Functional style: pure functions, explicit inputs/outputs. Dataclasses
   only as plain data containers.
3. For any constrained iteration: `for` loops with a fixed limit, never `while`.
4. Do not assume imports/variables not shown here.
5. Implement only what is asked.

Create file: src/promptgen/models.py

Requirements:
- Implement RewriteFeedback and TitleEchoJudgment dataclasses exactly as
  shown in SPEC-002 section 2 — frozen, with the exact field names/types.
- Add docstrings explaining each field's purpose, referencing SPEC-002
  where relevant (e.g. why parse_warning exists rather than raising).

### Acceptance Check
`python -c "from src.promptgen.models import RewriteFeedback, TitleEchoJudgment; print('ok')"`
```

---

## STEP 2: Feedback Construction

```
@workspace
Reference: docs/specs/SPEC-002-prompt-templates.md, section 4 (Feedback
Construction — read the exact wording patterns for each rule category)
and section 2 (build_feedback_from_report signature). Also read
src/detector/models.py (DetectorReport, RuleMatch, RhythmStats,
DiversityStats) — import from there, do not redefine.

IMPLEMENTER RULES — same as Step 1.

Create file: src/promptgen/feedback.py

Requirements:
- Implement `build_feedback_from_report(report: DetectorReport, language: str) -> RewriteFeedback`
- For each rule name present in report.failed_rules, generate ONE
  instruction string following the patterns in SPEC-002 section 4:
  - "cliche_blacklist": name each matched phrase from report.cliche_matches
    with its position, e.g. "Обнаружены штампы: «важно отметить» (позиция 45)..."
    (RU) or "Cliché phrases found: ..." (EN) — join multiple matches with
    commas, cap at listing the first 5 if there are many (add "и ещё N"/
    "and N more" if truncated)
  - "hedge_blacklist": same pattern, using report.hedge_matches
  - "rhythm_monotony": include the actual mean_length_words and
    stdev_length_words numbers from report.rhythm in the instruction text
  - "lexical_diversity": include actual distinct_2/distinct_3 values and
    the configured thresholds (note: thresholds aren't on DetectorReport
    itself — if not available, phrase the instruction without the
    threshold number, just the actual score, and note this as a TODO
    comment for a future refinement, don't invent a number)
  - "parallelism": include actual matched_text examples from
    report.diversity.parallelism_matches (up to 3 examples)
- ADDITIONALLY (regardless of failed_rules — per SPEC-002 section 4's
  note that meta-commentary is informational, not pass/fail): if
  report.meta_commentary_matches is non-empty, add an instruction
  naming the matched opening/closing phrase, distinguishing
  "meta_commentary_opening" vs "meta_commentary_closing" wording.
- If there is nothing to report (empty failed_rules AND empty
  meta_commentary_matches), return RewriteFeedback(instructions=[]).
- Support both language="ru" and language="en" — write out both full
  phrasings, don't machine-translate on the fly.

### Acceptance Check
Manually construct a DetectorReport with 2 cliche_matches and
failed_rules=["cliche_blacklist"], call build_feedback_from_report,
confirm the instruction string names both matched phrases and their
positions, not just "clichés were found."
```

---

## STEP 3: Grammar-Pass and Style-Pass Prompt Builders

```
@workspace
Reference: docs/specs/SPEC-002-prompt-templates.md, sections 2, 3
(placeholder-token rule — copy the RU/EN text VERBATIM as given in the
spec, do not paraphrase it) and section 4. Also read
src/promptgen/feedback.py from Step 2 — RewriteFeedback comes from there.

IMPLEMENTER RULES — same as Step 1.

Create file: src/promptgen/prompts.py

Requirements:
- Implement `build_grammar_pass_prompt(text: str, language: str) -> str`:
  - Instructs the model to fix ONLY grammar, logical flow, and factual
    consistency — explicitly say NOT to change style, vocabulary, or tone
  - Includes the placeholder-token preservation rule from SPEC-002
    section 3, verbatim (RU or EN version depending on `language`)
  - Includes the input `text` clearly delimited (e.g. under a "### Текст"
    / "### Text" heading) so the model knows exactly what to rewrite
- Implement `build_style_pass_prompt(text, feedback, language, persona=None) -> str`:
  - Includes the placeholder-token rule verbatim (same as grammar pass)
  - Explicitly forbids adding an introduction or conclusion unless
    already present and requested — state this as a firm rule, not a
    suggestion
  - If feedback.instructions is non-empty, includes them as a numbered
    list under a clear heading (e.g. "### Замечания для исправления" /
    "### Issues to fix") — if empty, omit this section entirely (no
    empty heading with nothing under it)
  - If persona is not None, includes an instruction like "Перепишите в
    стиле {persona}." (RU) / "Rewrite in the style of {persona}." (EN)
    as a distinct section — if persona is None, omit this section
    entirely (test must confirm no persona section appears when None)
  - Includes the input `text` clearly delimited, same convention as
    grammar pass

### Acceptance Check
Print both prompts for a sample text and feedback with 2 instructions —
visually confirm: placeholder rule present in both, style prompt has
the numbered feedback list, no persona section when persona=None,
persona section appears with exact text when persona="cynical journalist".
```

---

## STEP 4: Title-Echo Prompt and Judge

```
@workspace
Reference: docs/specs/SPEC-002-prompt-templates.md, sections 2, 5, 6.
Also read src/detector/config_loader.py's strip_placeholder_tokens
function (from SPEC-001) — reuse it, do not reimplement.

IMPLEMENTER RULES — same as Step 1.

Create file: src/promptgen/title_echo.py

Requirements:
- Implement `build_title_echo_prompt(heading: str, following_text: str, language: str) -> str`:
  - Strip placeholder tokens from following_text first (using
    strip_placeholder_tokens from src.detector.config_loader) before
    inserting it into the prompt
  - Use the exact template structure from SPEC-002 section 5 (heading,
    following text, the direct yes/no question, instruction to answer
    with one word first)
- Implement `judge_title_echo(heading, following_text, language, llm_call: Callable[[str], str]) -> TitleEchoJudgment`:
  - Build the prompt via build_title_echo_prompt
  - Call `response = llm_call(prompt)` — do NOT catch exceptions from
    this call (per SPEC-002 section 6, that's the caller's job)
  - Parse response: strip whitespace, take the first word, compare
    case-insensitively against {"да", "yes"} → is_echo=True, or
    {"нет", "no"} → is_echo=False
  - If the first word matches neither set, return
    TitleEchoJudgment(is_echo=False, raw_response=response,
    parse_warning=f"Unparseable response, first word was: {first_word!r}")
  - On a clean parse, parse_warning=None

### Acceptance Check
Test with a fake llm_call returning "ДА, потому что..." → is_echo=True,
parse_warning=None. Test with fake returning "Sort of, maybe" →
is_echo=False, parse_warning is not None.
```

---

## STEP 5: Unit Tests

```
@workspace
Reference: docs/specs/SPEC-002-prompt-templates.md, section 7 (exact
list of test names, split into prompt-building tests and judge tests).
Read all files from Steps 1-4.

IMPLEMENTER RULES — same as Step 1.

Create file: tests/test_promptgen.py

Requirements:
- Implement every test function listed in SPEC-002 section 7's first
  two lists (prompt-building tests and judge tests with fake llm_call).
  Do NOT implement the integration test yet — that's Step 6.
- For judge tests, use a simple fake function (a lambda or small local
  function) as llm_call — no real Ollama calls in this file.
- Each test asserts on specific string content or field values, not
  just "no exception raised."

### Acceptance Check
Run: `pytest tests/test_promptgen.py -v`
All non-integration tests must pass.
```

---

## STEP 6: Integration Test (Real Ollama)

```
@workspace
Reference: docs/specs/SPEC-002-prompt-templates.md, section 7's
integration test requirement.

IMPLEMENTER RULES — same as Step 1.

Add to tests/test_promptgen.py (or a new file tests/test_promptgen_integration.py,
your choice — keep it separably runnable):

Requirements:
- Implement a real Ollama-calling function suitable for use as llm_call:
  something like `def _real_ollama_call(prompt: str, model: str = "mistral") -> str`
  using either the `ollama` Python package if installed, or a raw HTTP
  POST to http://localhost:11434/api/generate — your choice, but keep
  it simple (no retry logic, no streaming, single request/response).
- Mark the test with `@pytest.mark.integration` (add the marker
  registration to a pytest.ini or pyproject.toml [tool.pytest.ini_options]
  section if one doesn't already exist, so pytest doesn't warn about an
  unknown marker).
- Write test_judge_title_echo_against_real_ollama_obvious_echo_case():
  use an obvious echo case (heading "Преимущества удалённой работы",
  following_text "Удалённая работа имеет свои преимущества.") and assert
  is_echo is True. This test requires Ollama running locally with
  mistral pulled — do not make it fail the whole suite if Ollama isn't
  reachable; instead use pytest.skip() with a clear message if the
  connection fails, so a normal `pytest` run (without -m integration)
  simply doesn't run it, and even an explicit `-m integration` run
  skips gracefully rather than erroring if Ollama isn't up.

### Acceptance Check
Run: `pytest tests/test_promptgen.py -v` (without -m integration) —
confirm the integration test is NOT executed (deselected by marker).
Then run: `pytest tests/test_promptgen.py -v -m integration` with Ollama
running and mistral pulled — confirm it passes or skips cleanly, never
crashes with an unhandled connection error.
```

---

## After All 6 Steps Complete

```powershell
pytest tests/test_promptgen.py -v
git add src/promptgen tests/test_promptgen.py pytest.ini
git commit -m "feat: Implement prompt templates and title-echo LLM-as-judge [SPEC-002]"
git push
git status
```

Then bring results back to Claude for Stage 6 (validity review) before
marking SPEC-002 complete — same discipline as SPEC-001 and SPEC-003.
Pay particular attention during review to whether the placeholder-token
rule text was copied verbatim (per Step 3's instruction) rather than
paraphrased — a paraphrased version might accidentally weaken the rule's
clarity for the actual rewrite model downstream.
