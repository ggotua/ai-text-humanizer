# SPEC-005: Iteration Loop & Threshold Controller

Feature:      Wraps SPEC-004's run_single_pass in a loop, re-checking
              DetectorReport.passed after each pass, up to a fixed
              iteration cap. Also enforces placeholder-token integrity
              across iterations — the correctness-critical guarantee
              the whole project depends on.
Priority:     P2
Status:       Planning
Dependencies: SPEC-001 (PLACEHOLDER_TOKEN_PATTERN), SPEC-004 (run_single_pass,
              SinglePassResult, GRAMMAR_PASS_TEMPERATURE, STYLE_PASS_TEMPERATURE)
Related Docs: APP-OVERVIEW.md §2.4 (Iteration Loop); SPEC-006 (consumes
              this spec's loop for the .docx-integrated pipeline)

---

## 1. Overview

A single `run_single_pass` call (SPEC-004) may not be enough — the
detector might still flag issues after one grammar+style cycle. This
spec adds the loop: repeat `run_single_pass`, feeding each pass's output
back in as the next pass's input, until `detector_report.passed` is
True or a fixed iteration cap is reached.

**Two responsibilities, both correctness-critical:**
1. The loop itself (repeat, check, stop-or-continue) — a `for` loop with
   a hard cap, per APP-OVERVIEW §2.4 and the project's established
   convention (never `while` for anything with a termination condition
   that depends on model output).
2. **Placeholder-token integrity checking across iterations.** This is
   new to this spec and is arguably the most important part of it: SPEC-002's
   placeholder-preservation prompt rule is a request to the LLM, not a
   guarantee. A single pass might survive it; three chained passes multiply
   the chance that some pass silently drops or duplicates a token. Since
   footnote/endnote preservation is this project's core value proposition
   (APP-OVERVIEW §2.6), the loop must actively verify token integrity after
   every single pass, not just hope the prompt rule held.

**Acceptance criteria:**
- Loop stops as soon as a pass's `detector_report.passed` is True — no
  wasted iterations
- Loop never exceeds `max_iterations` — hard cap via `for`, never `while`
- If the cap is reached without passing, the best-scoring attempt is
  returned with a clear warning, not a silent failure
- If placeholder-token integrity breaks at any iteration, the loop stops
  immediately and returns the last known-good text (before the corrupting
  pass), not the corrupted output — losing one iteration's stylistic
  improvement is an acceptable cost; losing a footnote anchor is not

---

## 2. Interface Definition

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class IterationResult:
    final_text: str
    passed: bool
    iterations_completed: int
    history: tuple["SinglePassResult", ...]   # every attempt, in order, for audit/debugging
    warning: str | None                        # set on max-iterations-reached OR token-integrity failure

def run_iteration_loop(
    text: str,
    language: str,
    ollama_call: Callable[[str, float], str],
    detector_config: "DetectorConfig",
    max_iterations: int = 3,
    persona: str | None = None,
) -> IterationResult:
    """
    Repeats run_single_pass up to max_iterations times, per section 3's
    algorithm. Raises ValueError if max_iterations < 1 (fail fast on
    misconfiguration rather than silently doing zero useful work).
    """

def extract_placeholder_token_set(text: str) -> frozenset[str]:
    """
    Returns the set of distinct placeholder tokens (e.g. {"[[FN:3]]",
    "[[EN:7]]"}) found in text, using SPEC-001's PLACEHOLDER_TOKEN_PATTERN.
    Note: SET, not count — per SPEC-002 section 3's rule, a token may be
    duplicated legitimately if the original text cited the same footnote
    twice (rare but valid per SPEC-003 section 5's "Duplicate legitimate
    reference" case) — so integrity checking in this spec compares the
    MULTISET (count per distinct token), not just set membership. See
    section 4 for the exact comparison this function's result feeds into.
    """
```

---

## 3. Loop Algorithm

```
current_text = text
history = []

for i in range(max_iterations):
    tokens_before = extract_placeholder_token_multiset(current_text)   # see section 4

    result = run_single_pass(current_text, language, ollama_call, detector_config, persona)
    history.append(result)

    tokens_after = extract_placeholder_token_multiset(result.final_text)

    if tokens_before != tokens_after:
        # Token integrity broken THIS iteration — stop immediately,
        # do NOT use result.final_text. Return the last known-good text.
        return IterationResult(
            final_text=current_text,   # the text BEFORE this corrupting pass
            passed=False,
            iterations_completed=i,     # this iteration did not complete successfully
            history=tuple(history),
            warning=(
                f"Placeholder token integrity broken at iteration {i+1}: "
                f"before={dict-like summary}, after={dict-like summary}. "
                f"Returning last known-good text from before this iteration."
            ),
        )

    if result.detector_report.passed:
        return IterationResult(
            final_text=result.final_text,
            passed=True,
            iterations_completed=i + 1,
            history=tuple(history),
            warning=None,
        )

    current_text = result.final_text   # feed forward, try again

# Loop exhausted without passing (and without a token-integrity break)
best = min(history, key=lambda r: len(r.detector_report.failed_rules))
return IterationResult(
    final_text=best.final_text,
    passed=False,
    iterations_completed=max_iterations,
    history=tuple(history),
    warning=(
        f"Max iterations ({max_iterations}) reached without passing all "
        f"thresholds; returning best-scoring attempt "
        f"({len(best.detector_report.failed_rules)} failed rule(s) remaining)."
    ),
)
```

**Tie-breaking in the `min()` call:** if multiple attempts have the same
`failed_rules` count, Python's `min()` returns the FIRST one encountered
— i.e. the earliest iteration, not the latest. This is a deliberate,
documented choice: an earlier attempt with equally-many failures is
preferred over a later one, since later passes have had more
grammar+style rewrite cycles applied and are further from the original
meaning without a clear correctness benefit to show for it.

---

## 4. Placeholder Token Multiset Comparison — Why Multiset, Not Set

A plain set comparison (`{"[[FN:3]]", "[[FN:3]]"}` collapses to
`{"[[FN:3]]"}`) would silently accept a pass that drops one of two
legitimate duplicate citations of the same footnote. The check must be a
**multiset** (count per distinct token string), so:

- Input has `[[FN:3]]` appearing twice (legitimate duplicate citation,
  per SPEC-003 §5) → output must also have it exactly twice, not once,
  not three times.
- Use `collections.Counter` over the tokens extracted via
  `PLACEHOLDER_TOKEN_PATTERN`, compare `Counter` objects for equality
  (this correctly handles both count and which specific tokens are present).

`extract_placeholder_token_set`'s docstring name is slightly misleading
given this — implementers should build the actual comparison using
`collections.Counter`, not a bare `set`/`frozenset`, for the integrity
check itself; the "set" function is for identifying which tokens exist at
all (useful for logging), while the loop's correctness check in section 3
must use counts.

---

## 5. Edge Cases & Constraints

| Case | Behavior |
|---|---|
| `max_iterations < 1` | Raise `ValueError` immediately — fail fast, don't silently do nothing |
| First pass already passes | Loop stops after 1 iteration — no wasted calls |
| Text has zero placeholder tokens (plain `.txt` input, no footnotes) | Multiset comparison is `Counter() == Counter()` — trivially equal, no false positives |
| All `max_iterations` attempts fail thresholds, none break token integrity | Returns best-scoring attempt with the max-iterations warning (section 3's final branch) |
| Token integrity breaks on iteration 1 (the very first pass) | `history` has exactly one entry; returned `final_text` is the ORIGINAL input text unchanged — the only "known-good" text available |
| `ollama_call` raises an exception mid-iteration | Propagates uncaught, per SPEC-004's established pattern — the loop does not add its own exception handling around individual pass failures |

---

## 6. Error Handling Requirements

- `run_iteration_loop` raises `ValueError` for `max_iterations < 1` before
  any Ollama calls are made.
- Does not catch exceptions from `ollama_call` (via `run_single_pass`) —
  consistent with SPEC-002 §6 and SPEC-004 §3's "propagate, don't catch"
  convention throughout this pipeline; only the caller (ultimately the
  Langflow component) decides retry/timeout policy.
- Token-integrity failures are NOT exceptions — they're a normal,
  expected-to-happen outcome communicated via `IterationResult.warning`
  and `passed=False`, not a raised error. This is a judgment call: a
  dropped footnote token is a data-quality problem for the caller to
  see and decide about, not a program bug that should crash the pipeline.

---

## 7. Testing Requirements

```
test_run_iteration_loop_stops_immediately_when_first_pass_passes()
test_run_iteration_loop_continues_when_first_pass_fails()
test_run_iteration_loop_stops_at_max_iterations_returns_best_scoring()
test_run_iteration_loop_tie_breaking_returns_earliest_attempt()
test_run_iteration_loop_raises_value_error_for_max_iterations_zero()
test_run_iteration_loop_raises_value_error_for_max_iterations_negative()
test_token_integrity_broken_stops_loop_immediately()
test_token_integrity_broken_returns_pre_corruption_text_not_corrupted_output()
test_token_integrity_broken_on_first_iteration_returns_original_input()
test_token_multiset_correctly_distinguishes_duplicate_vs_single_occurrence()
test_token_multiset_comparison_passes_for_text_with_zero_placeholder_tokens()
test_iteration_history_contains_all_attempts_in_order()
```

For token-integrity tests: use a fake `ollama_call` that deliberately
drops a `[[FN:n]]` token from its response on a specific call, to
simulate the LLM failing to honor the preservation rule — this is the
realistic failure mode being guarded against, not a hypothetical.

**Separate integration test** (marked `@pytest.mark.integration`, same
skip-on-unreachable pattern as SPEC-002/004):
```
test_run_iteration_loop_against_real_ollama_multi_iteration_case()
```
Uses text with 2-3 known cliché violations that a single pass likely
won't fully fix, with a low `max_iterations` (e.g. 2) — asserts the loop
completes without error and `iterations_completed >= 1`. Does not assert
`passed=True` (real model behavior isn't guaranteed to fully satisfy
thresholds even after 2 passes) — asserts the loop's mechanics work
correctly against a real, unpredictable model.

---

## 8. What SPEC-006 Depends On From This Spec

- `run_iteration_loop` as the core engine SPEC-006 wires into the
  `.docx`-aware pipeline — SPEC-006 will call this with text that's
  already been through SPEC-003's placeholder extraction, so token
  integrity checking here is exactly what protects SPEC-006's
  reassembly step from receiving corrupted input
- `IterationResult.warning` — SPEC-006 should surface this to the user
  somehow (at minimum, log it) rather than silently discarding it, since
  a token-integrity warning at this stage means a footnote may need
  manual attention before the final `.docx` is produced
