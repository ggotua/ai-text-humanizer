# PROMPT-005: Iteration Loop & Threshold Controller — Implementation Prompts

Reference: docs/specs/SPEC-005-iteration-loop.md (read in full first —
especially section 4, the multiset-vs-set distinction, before Step 3).
Also read src/pipeline/single_pass.py (SPEC-004) and
src/detector/config_loader.py (PLACEHOLDER_TOKEN_PATTERN, SPEC-001) —
import from there, do not redefine.

Execute these 5 steps in order, testing after each.

---

## STEP 1: Models and Token Multiset Helper

```
@workspace
Reference: docs/specs/SPEC-005-iteration-loop.md, sections 2 and 4.

IMPLEMENTER RULES — follow without exception:
1. Return the COMPLETE file — no partial output.
2. Functional style: pure functions, explicit inputs/outputs.
3. For any constrained iteration: `for` loops with a fixed limit, never `while`.
4. Do not assume imports/variables not shown here.
5. Implement only what is asked.

Create file: src/pipeline/iteration_loop.py

Requirements for this step — implement ONLY these, not run_iteration_loop yet:

- IterationResult frozen dataclass exactly as shown in SPEC-005 section 2
  (final_text, passed, iterations_completed, history, warning).
- `extract_placeholder_token_set(text: str) -> frozenset[str]` per SPEC-005
  section 2's docstring — use PLACEHOLDER_TOKEN_PATTERN imported from
  src.detector.config_loader (or wherever it's actually defined per
  SPEC-001 — check and import from the correct location, do not redefine
  the regex here).
- `extract_placeholder_token_multiset(text: str) -> "collections.Counter"`
  — a separate function (name it exactly this) that returns a
  collections.Counter of the same tokens, for the count-aware comparison
  described in SPEC-005 section 4. Import Counter from collections.

Do not implement run_iteration_loop in this step.

### Acceptance Check
```
python -c "
from src.pipeline.iteration_loop import extract_placeholder_token_multiset
text = 'Текст [[FN:3]] с сноской [[FN:3]] дважды и [[EN:1]] один раз.'
c = extract_placeholder_token_multiset(text)
print(c)
assert c['[[FN:3]]'] == 2
assert c['[[EN:1]]'] == 1
print('ok')
"
```
Should print the Counter and "ok".
```

---

## STEP 2: Basic Loop (no token integrity check yet)

```
@workspace
Reference: docs/specs/SPEC-005-iteration-loop.md, section 3 — implement
ONLY the pass/fail/max-iterations logic for now, SKIP the token-integrity
branch entirely (that's added in Step 3). Read
src/pipeline/iteration_loop.py from Step 1 — add to it, don't recreate
the dataclass/helpers. Also read src/pipeline/single_pass.py
(run_single_pass, SinglePassResult).

IMPLEMENTER RULES — same as Step 1.

Add to src/pipeline/iteration_loop.py (return the complete file including
Step 1's content unchanged):

`run_iteration_loop(text, language, ollama_call, detector_config, max_iterations=3, persona=None) -> IterationResult`

Implement THIS SIMPLIFIED version (no token-integrity check yet):

1. Raise ValueError immediately if max_iterations < 1.
2. current_text = text; history = []
3. For i in range(max_iterations):
   a. result = run_single_pass(current_text, language, ollama_call, detector_config, persona)
   b. history.append(result)
   c. If result.detector_report.passed: return IterationResult(
      final_text=result.final_text, passed=True,
      iterations_completed=i+1, history=tuple(history), warning=None)
   d. current_text = result.final_text
4. After the loop (exhausted without passing): find best = the entry in
   history with the FEWEST failed_rules (use Python's min() with a key
   function — this naturally returns the first occurrence on ties, which
   is the desired tie-breaking behavior per SPEC-005 section 3's note).
   Return IterationResult(final_text=best.final_text, passed=False,
   iterations_completed=max_iterations, history=tuple(history),
   warning=f"Max iterations ({max_iterations}) reached without passing "
   f"all thresholds; returning best-scoring attempt "
   f"({len(best.detector_report.failed_rules)} failed rule(s) remaining).")

### Acceptance Check
Manually construct a fake ollama_call/detector_config setup where the
report never passes — confirm the loop runs exactly max_iterations times
(check history length) and returns the entry with fewest failed_rules,
not just the last one. Construct a separate case where a fake setup
passes on iteration 2 of 3 — confirm iterations_completed == 2 and only
2 entries in history (loop stopped early, did not run a 3rd time).
```

---

## STEP 3: Add Token Integrity Check

```
@workspace
Reference: docs/specs/SPEC-005-iteration-loop.md, section 3 (the
tokens_before/tokens_after branch you skipped in Step 2) and section 4
(why Counter comparison, not set). Read src/pipeline/iteration_loop.py
from Step 2 — modify run_iteration_loop in place.

IMPLEMENTER RULES — same as Step 1.

Modify `run_iteration_loop` (return the complete file):

Inside the for loop, BEFORE calling run_single_pass, compute
`tokens_before = extract_placeholder_token_multiset(current_text)`.
AFTER calling run_single_pass and appending to history, compute
`tokens_after = extract_placeholder_token_multiset(result.final_text)`.

If `tokens_before != tokens_after` (Counter equality — this correctly
catches both dropped tokens AND duplicated tokens AND swapped-in wrong
tokens): return IMMEDIATELY (do not check result.detector_report.passed
at all this iteration — token integrity takes priority):

IterationResult(
    final_text=current_text,   # the PRE-this-iteration text, NOT result.final_text
    passed=False,
    iterations_completed=i,     # note: i, not i+1 — this iteration did not complete cleanly
    history=tuple(history),
    warning=(
        f"Placeholder token integrity broken at iteration {i+1}: "
        f"before={dict(tokens_before)}, after={dict(tokens_after)}. "
        f"Returning last known-good text from before this iteration."
    ),
)

Do not change any other part of the function (the passed/max-iterations
logic from Step 2 stays exactly as it was, this check just goes first).

### Acceptance Check
Construct a fake ollama_call where, on the grammar-pass or style-pass
call, the returned text has one [[FN:3]] token from the input simply
removed (simulating the LLM dropping it). Run run_iteration_loop with
this fake — confirm: the function returns immediately (does not run
further iterations), final_text equals the ORIGINAL input text (not the
corrupted output), passed=False, and warning mentions "integrity broken".
```

---

## STEP 4: Unit Tests

```
@workspace
Reference: docs/specs/SPEC-005-iteration-loop.md, section 7 (exact list
of required test names, excluding the integration test). Read
src/pipeline/iteration_loop.py from Steps 1-3.

IMPLEMENTER RULES — same as Step 1.

Create file: tests/test_iteration_loop.py

Requirements:
- Implement all test functions from SPEC-005 section 7's first list
  (everything except test_run_iteration_loop_against_real_ollama_multi_iteration_case,
  which is Step 5).
- For token-integrity tests: use a fake ollama_call that deliberately
  drops or duplicates a token on a specific call number (track call
  count in a closure) to simulate realistic LLM failure to honor the
  preservation rule.
- Each test asserts specific values (exact iteration counts, exact
  final_text content, exact warning substring presence), not just
  "no exception raised."

### Acceptance Check
Run: `pytest tests/test_iteration_loop.py -v`
All tests must pass.
```

---

## STEP 5: Integration Test (Real Ollama)

```
@workspace
Reference: docs/specs/SPEC-005-iteration-loop.md, section 7's
integration test requirement. Read tests/test_single_pass.py's existing
integration test pattern (skip-on-unreachable, make_ollama_call_fn usage)
and reuse the same approach.

IMPLEMENTER RULES — same as Step 1.

Add to tests/test_iteration_loop.py:

Requirements:
- Implement test_run_iteration_loop_against_real_ollama_multi_iteration_case()
  marked @pytest.mark.integration.
- Use make_ollama_call_fn() (model="mistral"), a real DetectorConfig via
  load_config("config", "ru"), a RU paragraph with 2-3 known cliché
  violations, max_iterations=2.
- Wrap in try/except, pytest.skip() with clear message on any failure —
  same pattern as SPEC-002/004's integration tests.
- Assert (only if no skip): no exception was raised, result.iterations_completed >= 1,
  result.history is non-empty. Do NOT assert passed=True — real model
  behavior isn't guaranteed to fully satisfy thresholds in 2 passes.

### Acceptance Check
With Ollama running and mistral pulled:
`pytest tests/test_iteration_loop.py -v -m integration`
Expect this to take longer than SPEC-004's single-pass integration test
(97s) — up to 2 full single-pass cycles (4 real model calls total) could
take 3-4 minutes. This is normal, not a hang — let it run.
```

---

## After All 5 Steps Complete

```powershell
pytest tests/test_iteration_loop.py -v
git add src/pipeline/iteration_loop.py tests/test_iteration_loop.py
git commit -m "feat: Implement iteration loop with placeholder-token integrity checking [SPEC-005]"
git push
git status
```

Then bring results back to Claude for Stage 6 (validity review) before
marking SPEC-005 complete. Given this spec's correctness-critical nature
(token integrity is what protects the whole footnote-preservation
guarantee from SPEC-003 through the rest of the pipeline), expect this
review to be more thorough than usual — pay particular attention to
whether Step 3's "return BEFORE checking passed" ordering was preserved
correctly (token integrity must take priority over threshold-passing on
every iteration, not just be checked afterward).
