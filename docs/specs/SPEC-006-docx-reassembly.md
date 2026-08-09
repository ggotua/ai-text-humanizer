# SPEC-006: .docx Reassembly & Footnote QA Check

Feature:      Closes the full pipeline — takes a humanized text (with
              placeholder tokens intact, from SPEC-005's IterationResult)
              and the original .docx's extraction metadata (from SPEC-003),
              and produces a real .docx file with footnotes/endnotes
              correctly re-attached, plus an automated QA check.
Priority:     P1 (final integration — closes the loop APP-OVERVIEW opened
              with the 2026-07-23 footnote-preservation requirement)
Status:       Planning
Dependencies: SPEC-003 (ExtractionResult, NoteReference, extract_docx_text,
              PLACEHOLDER_TOKEN_PATTERN), SPEC-005 (IterationResult —
              specifically its token-multiset-preservation guarantee)
Related Docs: APP-OVERVIEW.md §2.6, §9 (success criteria); SPEC-003 §9
              (open questions this spec resolves)

---

## 1. Overview

This is the spec that makes the footnote-preservation architecture (first
raised all the way back when `.docx` input was added to scope) actually
pay off. Everything before this point — placeholder tokens, lemma-aware
detection, prompt rules never to touch them, multiset integrity checks
across iterations — exists to make THIS spec possible: reliably putting
real footnotes back where they belong after the text around them has
been substantially rewritten.

**Key architectural decision: copy the original .docx, replace only
`word/document.xml`.** Rather than rebuilding a `.docx` from scratch
(which SPEC-003's test fixture generator does, deliberately, for
controlled testing), production reassembly copies the ENTIRE original
`.docx` zip archive byte-for-byte and replaces only the `word/document.xml`
entry. This preserves everything the pipeline never touched — styles,
theme, headers/footers, embedded images, `footnotes.xml`/`endnotes.xml`
themselves (their content is never modified, only referenced) — without
having to reconstruct any of it.

**Resolving SPEC-003 §9's open question:** paragraph (`\n\n`) boundaries
in the humanized text are NOT forced back to match the original document's
paragraph count. If the LLM merged or split paragraphs during rewriting,
the output `.docx` will have a different paragraph count than the input —
and that's fine. The reassembly algorithm builds paragraphs from whatever
`\n\n` structure exists in the final humanized text, not from the
original's structure. Forcing alignment back to the original would fight
against legitimate stylistic rewriting (e.g. splitting one long sentence
into two shorter ones is exactly what the rhythm-monotony fix does).

**Why this is safe — the guarantee SPEC-005 already proved:** every
placeholder token surviving into `IterationResult.final_text` is
guaranteed (by SPEC-005's multiset-equality invariant, checked at every
iteration) to have the exact same count as the original extracted text.
This spec doesn't need to re-derive that guarantee — it can rely on it
and focus purely on the mechanical XML reconstruction.

**Acceptance criteria (per APP-OVERVIEW §9, made concrete here):**
- Footnote/endnote count in output `.docx` matches the original exactly
- Every footnote/endnote reference in the output attaches to a real
  paragraph in the document body (not orphaned/dangling outside any `<w:p>`)
- "Position consistent with the original" is interpreted as: the note is
  still attached near the text that discusses whatever it originally
  supported, NOT byte-identical position — legitimate rewriting can move
  a citation's exact word position within its sentence or to an adjacent
  sentence during merge/split, and that's acceptable
- All other zip contents (`footnotes.xml`, `styles.xml`, images, etc.)
  are byte-identical between input and output

---

## 2. Interface Definition

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ReassemblyResult:
    output_docx_path: str
    original_reference_count: int
    output_reference_count: int
    qa_passed: bool
    qa_warnings: list[str]

def reassemble_docx(
    original_docx_path: str,
    extraction_result: "ExtractionResult",   # from SPEC-003
    final_text: str,                          # from SPEC-005's IterationResult.final_text
    output_docx_path: str,
) -> ReassemblyResult:
    """
    Produces a .docx at output_docx_path: original_docx_path's zip
    contents copied through unchanged, EXCEPT word/document.xml, which
    is rebuilt from final_text per section 3's algorithm. Runs the QA
    check (section 4) automatically before returning.

    Raises InvalidDocxError (from SPEC-003) if original_docx_path can't
    be opened. Does not raise on QA check failures — those are reported
    via ReassemblyResult.qa_passed/qa_warnings, since a QA failure means
    "review this before sending it to the client," not "the program is broken."
    """

def build_document_xml(
    final_text: str,
    references: tuple["NoteReference", ...],   # from extraction_result
    original_document_xml: str,                 # raw XML string, re-read from original_docx_path
) -> str:
    """
    Pure function: constructs the new word/document.xml content.
    Splits final_text into paragraphs on \\n\\n, splits each paragraph
    into text/token segments via PLACEHOLDER_TOKEN_PATTERN, builds
    <w:p> elements per section 3. Extracts and reuses the original
    document's <w:sectPr> (section properties — page size, margins) from
    original_document_xml, appended as the last element in the new body,
    per section 3 step 5. Does NOT touch footnotes.xml/endnotes.xml
    content — only builds references to existing note IDs.
    """

def run_footnote_qa_check(
    extraction_result: "ExtractionResult",
    output_docx_path: str,
) -> tuple[bool, list[str]]:
    """
    Re-extracts from output_docx_path using SPEC-003's extract_docx_text,
    and compares:
    1. Total reference count (original vs output) — must match exactly
    2. Every note_id present in extraction_result.references also present
       in the output's references (same type, same id) — order doesn't matter
    3. Every reference in the output resolves inside SOME <w:p> element
       (not dangling directly under <w:body>) — this is implicitly true
       given how build_document_xml constructs paragraphs, but verified
       here as a genuine independent check, not assumed
    Returns (passed, warnings) — warnings list is empty iff passed is True.
    """
```

---

## 3. Reassembly Algorithm

1. Open `original_docx_path` as a zip archive (reuse the same zip-reading
   approach as SPEC-003). Read `word/document.xml` as a raw string.
2. Parse out the `<w:sectPr>` element that is a direct child of `<w:body>`
   (the section properties — page size, margins, orientation). **MVP
   limitation, stated explicitly:** documents with multiple sections
   (multiple `<w:sectPr>` elements, e.g. a report with a landscape-oriented
   appendix) are not specifically handled — only the body-level `<w:sectPr>`
   is preserved; per-section properties nested inside paragraph `<w:pPr>`
   elements for multi-section documents are out of scope for this spec.
   Document this in DATA-DECISIONS.md if a real document turns out to need it.
3. Split `final_text` on `\n\n` into paragraphs.
4. For each paragraph:
   a. Split the paragraph text using `PLACEHOLDER_TOKEN_PATTERN`, alternating
      plain-text segments and token matches, preserving order.
   b. For each plain-text segment (even if empty string between two
      adjacent tokens): build `<w:r><w:t xml:space="preserve">{escaped text}</w:t></w:r>`.
      XML-escape the text (`&`, `<`, `>` at minimum) — do not assume
      input text is already escape-safe.
   c. For each token match: parse its type (`FN` or `EN`) and id from the
      token string. Look up the matching `NoteReference` in `references`
      by (type, id) to confirm it's a real reference (should always
      succeed per SPEC-005's guarantee — see section 1 — but if it
      somehow doesn't, treat it as an orphan: build the reference run
      anyway, matching SPEC-003's tolerant-of-orphans behavior, and add
      a QA warning rather than raising).
      Build `<w:r><w:footnoteReference w:id="{id}"/></w:r>` or
      `<w:r><w:endnoteReference w:id="{id}"/></w:r>` accordingly.
   d. Wrap all runs for this paragraph in `<w:p>...</w:p>`.
5. Concatenate all `<w:p>` elements, followed by the preserved `<w:sectPr>`
   from step 2, to form the new `<w:body>` content. Splice this into the
   original `document.xml`'s namespace/root-element wrapper (reuse the
   original file's `<w:document ...>` opening tag with all its namespace
   declarations — do not reconstruct these by hand, copy them verbatim
   from the original to avoid missing a namespace Word requires).
6. Write `output_docx_path`: copy every entry from `original_docx_path`'s
   zip EXCEPT `word/document.xml`, byte-for-byte unchanged. Write the new
   XML from step 5 as `word/document.xml` in the output zip.
7. Run `run_footnote_qa_check` (section 2) against the newly written file.
8. Return `ReassemblyResult` with the QA outcome.

---

## 4. Edge Cases & Constraints

| Case | Behavior |
|---|---|
| Original document has zero footnotes/endnotes | `references` is empty; no `<w:footnoteReference>`/`<w:endnoteReference>` runs generated; QA check trivially passes (0 == 0) |
| A paragraph in `final_text` has no placeholder tokens (majority case) | Single plain-text run (or a few, if XML-escaping splits weren't needed) — no reference runs, straightforward |
| `final_text` has a token whose id isn't in `references` at all | Per step 4c — build the run anyway, add a QA warning; should not happen given SPEC-005's guarantee, but never silently ignore if it does |
| Multi-section original document (multiple `<w:sectPr>`) | Documented MVP limitation (step 2) — only body-level section properties preserved |
| Empty paragraph (two consecutive `\n\n\n\n` in final_text, i.e. a blank paragraph) | Produces an empty `<w:p></w:p>` — valid OOXML, renders as a blank line in Word, not an error |
| Original `.docx` has content in headers/footers/tables referencing footnotes | Per SPEC-003 §5's own documented limitation (table/textbox footnotes out of scope) — this spec inherits that same limitation, does not attempt to fix it |

---

## 5. Error Handling Requirements

- `reassemble_docx` raises `InvalidDocxError` (SPEC-003's exception class)
  if `original_docx_path` can't be opened as a valid `.docx` — fail fast,
  don't produce a broken output file.
- QA check failures (section check) never raise — communicated via the
  return value, per section 2's docstring. A human should review a QA
  failure before shipping the document, but the pipeline itself
  completing without an exception is still a meaningful signal that
  nothing catastrophic happened.
- If writing `output_docx_path` fails partway through (disk full, etc.),
  do not leave a corrupt partial zip at that path — write to a temporary
  path first and rename/move only on full success (atomic write pattern).

---

## 6. Testing Requirements

Reuse SPEC-003's fixture generator (`tests/fixtures/generate_fixtures.py`)
as a starting point — these fixtures already have known, controlled
footnote/endnote structures, ideal for round-trip testing.

Required unit tests:
```
test_reassemble_docx_zero_footnotes_qa_passes()
test_reassemble_docx_preserves_footnote_count()
test_reassemble_docx_preserves_endnote_count()
test_reassemble_docx_handles_duplicate_reference_to_same_id()
test_reassemble_docx_multi_paragraph_final_text_produces_multiple_w_p_elements()
test_reassemble_docx_xml_escapes_special_characters_in_text()
test_reassemble_docx_preserves_other_zip_contents_byte_identical()
test_reassemble_docx_preserves_sectpr()
test_qa_check_detects_missing_reference()
test_qa_check_detects_count_mismatch()
test_qa_check_passes_on_clean_round_trip()
test_reassemble_raises_invalid_docx_error_on_corrupt_input()
```

For `test_reassemble_docx_preserves_other_zip_contents_byte_identical`:
open both the input and output `.docx` as zip archives, compare every
entry EXCEPT `word/document.xml` by raw bytes — this directly verifies
the "copy everything, replace only document.xml" architectural promise
from section 1.

**Full round-trip integration test** (uses real Ollama, marked
`@pytest.mark.integration`, same skip pattern as prior specs):
```
test_full_pipeline_docx_to_docx_round_trip()
```
Takes a real fixture `.docx` with footnotes (e.g. `footnotes_only.docx`
from SPEC-003, or a new fixture with actual cliché-laden sentences, since
the existing SPEC-003 fixtures were built for extraction testing, not
realistic prose) through: `extract_docx_text` → `run_iteration_loop`
(max_iterations=1, to keep runtime reasonable) → `reassemble_docx`. Asserts
the final `ReassemblyResult.qa_passed` is True and the output file opens
successfully as a valid `.docx` (e.g. via `python-docx`'s own loader as
an independent validity check, not just "our zip writer didn't crash").

---

## 7. What This Closes

This is the last spec in the original SPEC-001 through SPEC-006 plan
(APP-OVERVIEW §10). After this spec, per APP-OVERVIEW §9's success
criteria: a real `.docx` with footnotes can go in, get humanized, and
come out the other side with its citations intact and automatically
verified. Remaining work (SPEC-004a's file-upload UX, any future
threshold tuning against real SIDA/TVNAIA drafts) is refinement, not
architecture.
