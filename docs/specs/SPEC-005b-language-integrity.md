# SPEC-005b: Language Integrity Check (addendum to SPEC-002 and SPEC-005)

Feature:      Fixes a real bug found during SPEC-006's manual Word
              verification: Mistral fully translated a Russian document
              into English during humanization, despite language="ru"
              being passed throughout the pipeline. Adds (1) an explicit
              anti-translation instruction to SPEC-002's prompts, and
              (2) an automated language-integrity check in SPEC-005's
              iteration loop, following the same architectural pattern
              as the existing placeholder-token integrity check.
Priority:     P1 (real, observed bug — not hypothetical)
Status:       Planning
Dependencies: SPEC-002 (prompts.py, both marked COMPLETE — modified in place),
              SPEC-005 (iteration_loop.py, marked COMPLETE — modified in place)
Related Docs: SPEC-005 §4 (the multiset-comparison pattern this mirrors)

---

## 1. Overview — What Happened

During SPEC-006's manual verification (opening a real output `.docx` in
Word), the entire document was found to be in English, though the input
was Russian and `language="ru"` was passed at every layer. The footnote
itself survived correctly (SPEC-006's core guarantee held) — but the
prose was translated, not just rewritten. Root cause: SPEC-002's prompts
signal the target language only implicitly (RU-language template text,
RU section headers) — there was never an explicit instruction telling
the model not to translate. Small local models drift languages under
looser (higher-temperature, more "creative") generation, which is
exactly what the style-pass does.

**Two-part fix, same architectural pattern as SPEC-005's token-integrity
check:** strengthen the prompt first (cheap, may reduce frequency), AND
add a deterministic post-hoc check that catches it when the prompt fix
alone isn't enough (models don't reliably follow instructions 100% of
the time — this is the same reasoning that justified building SPEC-005's
token check instead of only relying on SPEC-002's placeholder-preservation
prompt rule).

---

## 2. Part 1: Prompt Hardening (SPEC-002)

In `src/promptgen/prompts.py`, add this instruction to BOTH
`build_grammar_pass_prompt` and `build_style_pass_prompt`, alongside the
existing placeholder-token rule (same verbatim-text discipline as before
— insert exactly, do not paraphrase):

RU version (when `language == "ru"`):
> "Отвечай СТРОГО на русском языке. Ни в коем случае не переводи текст
> на другой язык — только переписывай или исправляй его, сохраняя русский."

EN version (when `language == "en"`):
> "Respond STRICTLY in English. Under no circumstances translate the
> text into another language — only rewrite or correct it, keeping it in English."

Place this instruction near the top of the prompt (before the
placeholder-token rule), since language is a more fundamental constraint
than token preservation — establish it first.

---

## 3. Part 2: Automated Language Integrity Check (SPEC-005)

### Interface Addition

```python
def detect_dominant_script(text: str) -> str:
    """
    Returns "cyrillic", "latin", or "unknown" based on which script's
    letters dominate the alphabetic characters in text. Cheap heuristic
    (character-class counting, not a real language detector) —
    sufficient to catch a full RU<->EN drift, the only failure mode
    observed so far. Non-alphabetic characters (digits, punctuation,
    whitespace, placeholder tokens) are ignored in the count. Returns
    "unknown" if there are fewer than 20 alphabetic characters total
    (too short to judge reliably — avoid false positives on short text).
    """

def check_language_integrity(text: str, expected_language: str) -> tuple[bool, str | None]:
    """
    expected_language is "ru" or "en". Maps to the script that should
    dominate: "ru" -> "cyrillic", "en" -> "latin". Calls
    detect_dominant_script(text); if the result is "unknown" (too short
    to judge), returns (True, None) — do not flag short text as a
    false positive. If the dominant script doesn't match what
    expected_language implies, returns (False, a warning message naming
    the expected vs detected script). Otherwise (True, None).
    """
```

### Integration into `run_iteration_loop`

In `src/pipeline/iteration_loop.py`, inside the `for` loop, AFTER the
existing token-integrity check (tokens_before != tokens_after) and
BEFORE the `result.detector_report.passed` check, add:

```python
lang_ok, lang_warning = check_language_integrity(result.final_text, language)
if not lang_ok:
    return IterationResult(
        final_text=current_text,  # same pre-corruption pattern as token integrity
        passed=False,
        iterations_completed=i,
        history=tuple(history),
        warning=(
            f"Language integrity broken at iteration {i+1}: {lang_warning} "
            f"Returning last known-good text from before this iteration."
        ),
    )
```

This follows the exact same "abort and return pre-corruption text" pattern
as the token-integrity check — a language-drifted pass is treated with
the same severity as a footnote-dropping pass, because losing the
source language is just as much a correctness failure as losing a citation.

---

## 4. Edge Cases

| Case | Behavior |
|---|---|
| Very short text (a few words) | `detect_dominant_script` returns "unknown", check passes trivially — avoids false positives on short fragments |
| Text mixing Russian with English technical terms/proper nouns (common in policy/research writing) | As long as Cyrillic dominates overall character count, check passes — this is a coarse guard, not a purist language check |
| `expected_language` is neither "ru" nor "en" | Not expected given SPEC-001's existing language validation (only "ru"/"en" supported) — if it somehow happens, treat as a programming error and let it surface naturally rather than adding new defensive code for an already-validated precondition |

---

## 5. Testing Requirements

```
test_detect_dominant_script_cyrillic_text()
test_detect_dominant_script_latin_text()
test_detect_dominant_script_short_text_returns_unknown()
test_detect_dominant_script_ignores_placeholder_tokens_and_digits()
test_check_language_integrity_passes_when_matching()
test_check_language_integrity_fails_when_mismatched()
test_check_language_integrity_passes_on_unknown_short_text()
test_run_iteration_loop_stops_on_language_drift_returns_pre_corruption_text()
```

For the last test: use a fake `ollama_call` that returns fully-English
text when the loop expects Russian (simulating the real observed bug) —
confirm the loop stops immediately, `final_text` equals the original
Russian input, and the warning mentions language.

**Regression check:** re-run all existing SPEC-005 tests after this
change — the new check must not break any passing case (RU-to-RU passes
should be unaffected).

---

## 6. What This Does Not Fix

This is a heuristic guard, not a guarantee the model will never drift —
it catches a FULL document-level language flip after the fact and
discards that attempt, same philosophy as token-integrity checking. It
does not prevent the model from attempting a translation in the first
place (Part 1's prompt hardening is the only preventive measure; Part 2
is the safety net). If both fail to catch a case in practice, that's a
signal to strengthen the prompt further or investigate a different model.
