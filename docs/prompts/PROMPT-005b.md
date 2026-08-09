# PROMPT-005b: Language Integrity Fix — Implementation Prompts

Reference: docs/specs/SPEC-005b-language-integrity.md (read in full first).
This is a fix to two already-COMPLETE specs (SPEC-002's prompts.py,
SPEC-005's iteration_loop.py) — modify existing files in place, do not
recreate them from scratch.

Execute these 3 steps in order.

---

## STEP 1: Prompt Hardening (SPEC-002 fix)

```
@workspace
Reference: docs/specs/SPEC-005b-language-integrity.md, section 2 — use
the EXACT RU/EN instruction text given there, verbatim, same discipline
as the placeholder-token rule (do not paraphrase). Read
src/promptgen/prompts.py — modify build_grammar_pass_prompt and
build_style_pass_prompt IN PLACE, do not change anything else in the file.

IMPLEMENTER RULES — follow without exception:
1. Return the COMPLETE file — no partial output.
2. Do not paraphrase the given instruction text — insert exactly as given.
3. Implement only what is asked.

In BOTH build_grammar_pass_prompt and build_style_pass_prompt, add the
anti-translation instruction (RU or EN version matching the `language`
parameter, per SPEC-005b section 2) near the top of the prompt, BEFORE
the existing placeholder-token preservation rule.

### Acceptance Check
```
python -c "
from src.promptgen.prompts import build_grammar_pass_prompt
p = build_grammar_pass_prompt('Тестовый текст.', 'ru')
assert 'СТРОГО на русском' in p
print('anti-translation rule present: ok')
"
```
Should print "ok". Also manually verify the instruction appears BEFORE
the placeholder-token rule in the printed prompt (order matters per the
spec's stated rationale — language constraint established first).
```

---

## STEP 2: Language Detection and Integrity Check (SPEC-005 fix)

```
@workspace
Reference: docs/specs/SPEC-005b-language-integrity.md, section 3 (both
function signatures and the exact integration point — AFTER the token
check, BEFORE the passed check). Read src/pipeline/iteration_loop.py —
modify run_iteration_loop IN PLACE, add the two new functions to the
same file.

IMPLEMENTER RULES — same as Step 1, plus:
4. For any constrained iteration: `for` loops with a fixed limit, never `while`.

Add to src/pipeline/iteration_loop.py (return the complete file):

1. `detect_dominant_script(text: str) -> str` per SPEC-005b section 3's
   docstring exactly — count Cyrillic vs Latin alphabetic characters
   (use Python's unicodedata or explicit Unicode range checks: Cyrillic
   is U+0400-U+04FF, Latin is standard ASCII a-z/A-Z plus Latin-1
   supplement if you want to be thorough, but ASCII range is sufficient
   for RU vs EN distinction). Ignore digits, punctuation, whitespace,
   and characters inside placeholder tokens (strip those first via
   PLACEHOLDER_TOKEN_PATTERN before counting — reuse the existing
   extract-and-strip approach already used elsewhere in this file for
   consistency, do not write a third different way of handling tokens).
   Return "unknown" if total alphabetic character count < 20.

2. `check_language_integrity(text: str, expected_language: str) -> tuple[bool, str | None]`
   per SPEC-005b section 3's docstring exactly.

3. Modify `run_iteration_loop`: inside the for loop, immediately AFTER
   the existing token-integrity check block and BEFORE the
   `if result.detector_report.passed:` check, add the language-integrity
   check per SPEC-005b section 3's exact code block. Use the same
   "return current_text (pre-corruption), passed=False,
   iterations_completed=i" pattern as the token check.

### Acceptance Check
```
python -c "
from src.pipeline.iteration_loop import detect_dominant_script, check_language_integrity
print(detect_dominant_script('Это русский текст для проверки скрипта.'))
print(detect_dominant_script('This is English text for script checking.'))
print(detect_dominant_script('Hi'))
ok, warn = check_language_integrity('This is English text here obviously.', 'ru')
print(ok, warn)
"
```
Should print: cyrillic / latin / unknown / False <some warning message>
```

---

## STEP 3: Tests

```
@workspace
Reference: docs/specs/SPEC-005b-language-integrity.md, section 5 (exact
test list). Read src/pipeline/iteration_loop.py from Steps 1-2 and the
existing tests/test_iteration_loop.py — add new tests there, do not
remove or modify existing passing tests.

IMPLEMENTER RULES — same as Step 1.

Add to tests/test_iteration_loop.py:

Requirements:
- Implement all 8 test functions listed in SPEC-005b section 5.
- For test_run_iteration_loop_stops_on_language_drift_returns_pre_corruption_text:
  use a fake ollama_call that returns fully-English text on its first
  call when language="ru" is expected (simulating the real bug found
  in SPEC-006's manual review) — confirm the loop stops immediately,
  final_text equals the original Russian input unchanged, and the
  warning string mentions "language" or "Language".
- Run the FULL existing test suite in this file afterward to confirm no
  regression: all previously-passing tests (token integrity, max
  iterations, tie-breaking, etc.) must still pass unchanged.

### Acceptance Check
Run: `pytest tests/test_iteration_loop.py -v`
All tests (old + new 8) must pass — expect roughly 22 total (14 existing
+ 8 new), all green.
```

---

## After All 3 Steps Complete

```powershell
pytest tests/test_iteration_loop.py -v
pytest tests/test_promptgen.py -v -m "not integration"
git add src/promptgen/prompts.py src/pipeline/iteration_loop.py tests/test_iteration_loop.py
git commit -m "fix: Add anti-translation prompt rule and language integrity check [SPEC-005b]"
git push
git status
```

Then re-run SPEC-006's manual round-trip check (the same
`manual_check_output.docx` generation command from before) on a fresh
run against the same or a different RU fixture — confirm the output is
actually in Russian this time before considering SPEC-005b's fix verified.
This is the one bug in the whole project that was found by a human eye,
not a test — closing the loop by re-checking with a human eye is
appropriate here too, not just re-running pytest.
