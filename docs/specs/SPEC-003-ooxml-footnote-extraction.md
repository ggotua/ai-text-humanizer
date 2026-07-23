# SPEC-003: OOXML Parsing — Footnote/Endnote Placeholder Extraction

Feature:      OOXML footnote/endnote-aware text extraction from `.docx`
Priority:     P1 (highest technical risk — de-risk early, per replan 2026-07-23)
Status:       Planning
Dependencies: None (standalone; can be built/tested before the LLM pipeline exists)
Related Docs: APP-OVERVIEW.md §2.6, SPEC-006 (consumes this component's output)

---

## 1. Overview

Extracts plain text from a `.docx` file's body, replacing every footnote and
endnote reference with a stable inline placeholder token, so that downstream
text processing (detector, LLM rewrite) can treat the token as an
untouchable anchor rather than losing the footnote/endnote connection.

This spec covers **extraction only** (docx → text-with-placeholders +
metadata). Reassembly (text-with-placeholders → docx) is SPEC-006.

**Acceptance criteria:**
- Every real footnote/endnote reference in the body becomes exactly one
  placeholder token in the output text
- Separator/continuation-separator footnotes are excluded (not real content)
- Placeholder count in output text == count of real footnote/endnote
  references in the source document.xml
- Works correctly on documents with: no notes, only footnotes, only
  endnotes, both, multiple notes in one paragraph, notes at the start/end
  of a paragraph

---

## 2. Placeholder Token Contract

Format: `[[FN:{id}]]` for footnotes, `[[EN:{id}]]` for endnotes, where `{id}`
is the exact `w:id` attribute value from the source XML (not a re-numbered
sequential index — this preserves a direct, unambiguous link back to the
original note for SPEC-006).

Example: a footnote with `<w:footnoteReference w:id="3"/>` becomes the
literal substring `[[FN:3]]` at that position in the extracted text.

Paragraph boundaries in the source become `\n\n` (double newline) in the
extracted text.

**Known risk (not resolved in this spec):** if the LLM rewrite pass (SPEC-004)
merges or splits paragraphs, the `\n\n` boundaries may not map 1:1 back to
original paragraphs at reassembly time. SPEC-006 must handle this — likely
by treating placeholder-token position *within the flowing text* as the
source of truth, not paragraph index. Flagged here so SPEC-004's prompt
rules can be written knowing this constraint exists.

---

## 3. Interface Definition

Functional style — pure functions, explicit inputs/outputs, no hidden state.

```python
from dataclasses import dataclass
from enum import Enum

class NoteType(Enum):
    FOOTNOTE = "FN"
    ENDNOTE = "EN"

@dataclass(frozen=True)
class NoteReference:
    note_type: NoteType
    note_id: str          # raw w:id value, kept as string (not assumed int)
    position: int          # character offset of the placeholder in extracted_text

@dataclass(frozen=True)
class ExtractionResult:
    extracted_text: str            # plain text with [[FN:n]] / [[EN:n]] tokens inline
    references: list[NoteReference]  # ordered, one per placeholder token
    warnings: list[str]             # e.g. nested-note detected, orphan reference found

def extract_docx_text(docx_path: str) -> ExtractionResult:
    """
    Load a .docx file and return plain text with footnote/endnote
    placeholder tokens, plus a list of the references found.

    Raises:
        FileNotFoundError: if docx_path does not exist
        InvalidDocxError: if the file is not a valid .docx/zip or is
            missing word/document.xml
    """

def validate_extraction(result: ExtractionResult, docx_path: str) -> list[str]:
    """
    Cross-checks the extraction against the source file:
    - placeholder count in extracted_text matches len(result.references)
    - every note_id in result.references exists as a real (non-separator)
      entry in footnotes.xml / endnotes.xml
    - no note_id appears more than once in references with the same type
      unless the source genuinely references it twice (rare but valid —
      same footnote cited in two places)
    Returns a list of human-readable problem descriptions (empty list = clean).
    """
```

`InvalidDocxError` is a custom exception (subclass of `ValueError`), defined
in the same module.

---

## 4. Extraction Algorithm

1. Open `.docx` as a zip archive. Read `word/document.xml`. If missing →
   raise `InvalidDocxError`.
2. Also read `word/footnotes.xml` and `word/endnotes.xml` if present
   (a `.docx` with zero footnotes may lack these files entirely — that's
   valid, not an error).
3. Parse `footnotes.xml`/`endnotes.xml` first to build a set of valid,
   non-separator note IDs:
   - Skip any `<w:footnote>`/`<w:endnote>` element whose `w:type` attribute
     is `separator` or `continuationSeparator`.
   - Record the remaining IDs as "known valid IDs" per type.
4. Walk `document.xml` body paragraph by paragraph (`w:p` elements), and
   within each paragraph, run by run (`w:r` elements) in document order:
   - If a run contains `w:footnoteReference` or `w:endnoteReference`:
     append the corresponding placeholder token to the output text; record
     a `NoteReference` with its character position.
     - If the referenced ID is not in the "known valid IDs" set from step
       3 → treat as an orphan reference: still emit the placeholder (so
       position tracking stays consistent) but add a warning string;
       do not raise — this is a data-quality signal, not a hard failure
       (per George's SDD principle: distinguish code bugs from data
       problems, don't silently swallow, but don't crash on bad input either).
   - Otherwise, append the run's `w:t` text content.
   - Between paragraphs, append `\n\n`.
5. Detect nested notes: while parsing `footnotes.xml`/`endnotes.xml`
   content, if a note's own body contains another `w:footnoteReference`/
   `w:endnoteReference`, add a warning. Do not attempt to extract or
   placeholder-ize content *inside* footnote bodies — footnote text itself
   is never processed (per APP-OVERVIEW §2.6, footnote content is not
   rewritten).
6. Return `ExtractionResult(extracted_text, references, warnings)`.

---

## 5. Edge Cases & Constraints

| Case | Behavior |
|---|---|
| No footnotes/endnotes in document | Zero references, no warnings, normal text extraction |
| Multiple notes in one paragraph | Each gets its own token, in document order |
| Note reference at very start/end of paragraph | Handled identically — position is just a character offset |
| Orphan reference (ID not in footnotes.xml/endnotes.xml) | Placeholder still emitted; warning added; not a raised exception |
| Nested note reference (footnote containing a footnote ref) | Warning added; inner reference is NOT extracted or tokenized (footnote bodies are opaque) |
| Notes inside tables or text boxes in the body | **Out of scope for MVP** — only top-level body paragraphs (`w:body > w:p`) are walked. If a reference exists only inside a table cell, it will be missed; document this as a known MVP limitation, not silently pretend it's handled |
| Duplicate legitimate reference to the same note ID | Valid — emit both placeholders normally, no warning |
| Corrupt/non-docx file passed in | Raise `InvalidDocxError` with a descriptive message (not a bare zip/XML parser exception) |
| Empty paragraphs (no runs) | Contribute only the `\n\n` separator, no text |

---

## 6. Error Handling Requirements

- Never let a raw `zipfile.BadZipFile` or `lxml.etree.XMLSyntaxError`
  propagate to the caller — catch and re-raise as `InvalidDocxError` with
  context (which file, what was expected).
- `validate_extraction` never raises — it returns findings as strings so
  the caller (Langflow flow / tests) decides what to do with them.
- All warnings are descriptive enough to act on: e.g.
  `"Orphan footnote reference: w:id=7 not found in footnotes.xml"`, not
  just `"warning: bad reference"`.

---

## 7. Testing Requirements

Test fixtures needed (create as small synthetic `.docx` files, checked into
`tests/fixtures/`, exempted from `.gitignore`'s `*.docx` rule):

1. `no_notes.docx` — plain paragraphs, no footnotes/endnotes at all
2. `footnotes_only.docx` — 3 paragraphs, footnotes with IDs 2, 3, 4 (skip
   ID 1, which is typically the reserved separator — confirms separator
   exclusion works)
3. `endnotes_only.docx` — same shape, endnotes instead
4. `mixed_notes.docx` — both footnotes and endnotes in the same document
5. `multiple_notes_one_paragraph.docx` — one paragraph with 2+ footnote
   references
6. `note_at_paragraph_boundary.docx` — footnote reference as the very
   first or last run in a paragraph
7. `orphan_reference.docx` — manually crafted so `document.xml` references
   an ID that doesn't exist in `footnotes.xml` (tests warning path, not crash)
8. `nested_footnote.docx` — a footnote body that itself contains a
   `w:footnoteReference` (tests warning path)
9. `not_a_docx.txt` renamed to `.docx`, or a corrupt zip — tests
   `InvalidDocxError` is raised, not a raw exception

Required unit tests (map roughly 1:1 to fixtures above):
```
test_extract_no_notes_returns_zero_references()
test_extract_footnotes_only_correct_count_and_ids()
test_extract_endnotes_only_correct_count_and_ids()
test_extract_mixed_notes_both_types_present()
test_extract_multiple_notes_same_paragraph_ordered_correctly()
test_extract_note_at_paragraph_boundary()
test_extract_orphan_reference_produces_warning_not_exception()
test_extract_nested_footnote_produces_warning()
test_extract_invalid_docx_raises_invalid_docx_error()
test_validate_extraction_clean_case_returns_empty_list()
test_validate_extraction_detects_count_mismatch()
test_separator_footnotes_excluded_from_valid_id_set()
```

---

## 8. What SPEC-006 Depends On From This Spec

- The exact `ExtractionResult` data structure (frozen dataclasses — SPEC-006
  will need `references` to know where each placeholder maps back to a real
  `w:id`, in order to reconstruct proper `w:footnoteReference` runs)
- The placeholder token format (`[[FN:n]]` / `[[EN:n]]`) — SPEC-006's
  reassembly logic parses these tokens back out of the (possibly rewritten)
  text
- The warnings list — SPEC-006's automated QA check (footnote count/position
  match, per APP-OVERVIEW §9) will likely combine its own reassembly-time
  checks with any warnings carried over from extraction

---

## 9. Open Questions for Replanning After This Spec

- Does `\n\n` paragraph demarcation survive LLM rewrite well enough for
  SPEC-006 to work paragraph-by-paragraph, or does reassembly need to
  operate on the flat placeholder-position sequence regardless of paragraph
  structure? (Recommend revisiting after SPEC-004's prompt rules are tested
  on a real document — don't over-design SPEC-006 before that's known.)
- Table/text-box footnotes are out of scope for MVP — worth a decision
  in DECISIONS.md if a real SIDA/TVNAIA draft turns out to use them.


---

## Implementation Status

STATUS: COMPLETE
Completed: 2026-07-23

Files:
- src/ooxml/models.py
- src/ooxml/extract.py
- tests/fixtures/generate_fixtures.py
- tests/test_ooxml_extract.py

Test Results:
- 15/15 tests passing (12 from SPEC-003 §7 + 3 added during validity review)

Deviations from Original Spec:
- Added 3 fixtures/tests beyond the original 9, discovered during Stage 6
  validity review: fragmented_runs.docx, hyperlink_wrapped_footnote.docx,
  tracked_change_footnote.docx
- Changed run-search from iterchildren(w:r) to iter(w:r), and
  footnoteReference lookup from find() to next(iter(), None), to catch
  references wrapped in <w:hyperlink> or <w:ins> (tracked changes)

Known Limitation (see DATA-DECISIONS.md):
- w:del-wrapped footnote references are not filtered out

Next Phase:
- Ready for SPEC-001: Detector Component (must treat [[FN:n]]/[[EN:n]]
  tokens as opaque)