# PROMPT-003: OOXML Footnote/Endnote Extraction — Cursor Implementation Prompts

Reference: docs/specs/SPEC-003-ooxml-footnote-extraction.md (read this first, in full)

Execute these 5 prompts in order, in Cursor Composer (Ctrl+I) with `@workspace`
enabled. Run tests after each step before moving to the next — this spec is
high-risk, don't stack unverified steps.

---

## PROMPT 1: Fixture Generator Script

Open Cursor Composer, paste:

```
@workspace
Reference: docs/specs/SPEC-003-ooxml-footnote-extraction.md, section 7
(Testing Requirements — fixture list).

IMPLEMENTER RULES — follow without exception:
1. Return the COMPLETE file — no "# ... rest unchanged" or partial output.
2. Functional style: pure functions with explicit inputs/outputs. Avoid
   classes with hidden state.
3. For any constrained generation/looping: use `for` loops with a fixed
   iteration limit, never `while`.
4. Do not assume imports or variables not shown in this prompt.
5. Implement only what is asked. No extra features.

Create file: tests/fixtures/generate_fixtures.py

Purpose: python-docx cannot create footnotes/endnotes natively. This script
builds minimal valid .docx files by hand-assembling the required OOXML parts
as strings and zipping them together — no python-docx dependency for this
script specifically.

A minimal valid .docx is a zip archive containing at least:
- [Content_Types].xml
- _rels/.rels
- word/document.xml
- word/_rels/document.xml.rels
- word/footnotes.xml (only if the fixture has footnotes)
- word/endnotes.xml (only if the fixture has endnotes)

Requirements:
- Write a function `build_minimal_docx(output_path: str, body_xml: str,
  footnotes_xml: str | None, endnotes_xml: str | None) -> None` that
  assembles a valid .docx zip from the given XML fragments plus the
  required boilerplate parts (write the boilerplate as module-level
  string constants — Content_Types.xml, _rels/.rels, document.xml.rels —
  using minimal-but-valid OOXML namespaces).
- Write one function per fixture, each calling `build_minimal_docx` with
  hand-crafted `w:p` / `w:footnoteReference` / `w:endnoteReference` XML,
  producing exactly these 9 files in tests/fixtures/:
    1. no_notes.docx — 2 plain paragraphs, no notes
    2. footnotes_only.docx — 3 paragraphs, footnote references with
       w:id="2", "3", "4" (footnotes.xml must also include the reserved
       separator note at w:id="-1" with w:type="separator" and
       continuationSeparator at w:id="0" — real Word documents always
       have these; the fixture must too, to properly test that they're
       excluded)
    3. endnotes_only.docx — same shape, endnotes instead, same separator
       convention in endnotes.xml
    4. mixed_notes.docx — one footnote (w:id="2") and one endnote
       (w:id="2") in different paragraphs — note both can validly be id=2
       since footnote IDs and endnote IDs are independent namespaces
    5. multiple_notes_one_paragraph.docx — single paragraph containing
       two footnote references (w:id="2" and w:id="3")
    6. note_at_paragraph_boundary.docx — one paragraph where the footnote
       reference run is the very first run, another paragraph where it's
       the very last run
    7. orphan_reference.docx — document.xml references w:id="5" but
       footnotes.xml only defines w:id="-1", "0", "2" (5 is missing —
       deliberately orphaned)
    8. nested_footnote.docx — footnotes.xml's footnote body (w:id="2")
       itself contains a `w:footnoteReference` to w:id="3"
    9. not_a_docx.txt — NOT a docx at all, just a plain text file with
       the content "this is not a valid docx file" (for the InvalidDocxError
       test — note this one is a .txt, not renamed to .docx, since the
       test will pass an explicitly wrong path/extension deliberately)

- Add a `if __name__ == "__main__":` block that calls all 9 generator
  functions and prints a confirmation line per file created.

### Acceptance Check
Run: `python tests/fixtures/generate_fixtures.py`
Confirm all 9 files appear in tests/fixtures/ and that `footnotes_only.docx`
opens without a "file is corrupt" warning if you open it manually in Word
(spot check at least this one file manually before proceeding).
```

---

## PROMPT 2: Data Structures & Exceptions

```
@workspace
Reference: docs/specs/SPEC-003-ooxml-footnote-extraction.md, section 3
(Interface Definition).

IMPLEMENTER RULES — same as Prompt 1.

Create file: src/ooxml/models.py

Requirements:
- Implement exactly the dataclasses and enum shown in SPEC-003 section 3:
  NoteType (Enum), NoteReference (frozen dataclass), ExtractionResult
  (frozen dataclass).
- Implement InvalidDocxError as a subclass of ValueError, with a
  constructor that accepts a message string.
- Add type hints and docstrings for every class explaining its purpose
  in one or two sentences — reference SPEC-003 section 2 (Placeholder
  Token Contract) in the ExtractionResult docstring so future readers
  know why the format looks the way it does.

### Acceptance Check
Run: `python -c "from src.ooxml.models import NoteType, NoteReference, ExtractionResult, InvalidDocxError; print('ok')"`
Confirm it prints "ok" with no import errors.
```

---

## PROMPT 3: `extract_docx_text` Implementation

```
@workspace
Reference: docs/specs/SPEC-003-ooxml-footnote-extraction.md, sections 3
and 4 (Interface Definition, Extraction Algorithm). Also read
src/ooxml/models.py (created in the previous step) — use those exact
classes, do not redefine them.

IMPLEMENTER RULES — same as Prompt 1, plus:
6. Never let zipfile.BadZipFile or lxml.etree.XMLSyntaxError propagate
   uncaught — always re-raise as InvalidDocxError per SPEC-003 section 6.

Create file: src/ooxml/extract.py

Requirements:
- Implement `extract_docx_text(docx_path: str) -> ExtractionResult`
  exactly per the algorithm in SPEC-003 section 4, steps 1–6.
- Use `zipfile` (standard library) to open the .docx as a zip archive.
- Use `lxml.etree` for XML parsing (not the standard library's xml.etree,
  which is less robust for namespace handling).
- Correctly handle the OOXML namespace:
  `w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"`
- Implement the separator/continuationSeparator exclusion logic from
  section 4 step 3 exactly — check the `w:type` attribute on each
  `w:footnote`/`w:endnote` element.
- Implement the nested-note detection from section 4 step 5.
- Handle the edge cases from SPEC-003 section 5's table explicitly —
  do not skip the empty-paragraph or duplicate-legitimate-reference cases.
- Import NoteType, NoteReference, ExtractionResult, InvalidDocxError from
  src/ooxml/models — do not redefine them in this file.

### Acceptance Check
Run this manually against the fixtures once generated:
`python -c "from src.ooxml.extract import extract_docx_text; r = extract_docx_text('tests/fixtures/footnotes_only.docx'); print(r.extracted_text); print(r.references)"`
Confirm the printed text contains `[[FN:2]]`, `[[FN:3]]`, `[[FN:4]]` and
no separator-related tokens.
```

---

## PROMPT 4: `validate_extraction` Implementation

```
@workspace
Reference: docs/specs/SPEC-003-ooxml-footnote-extraction.md, sections 3
and 6 (Interface Definition, Error Handling Requirements). Also read
src/ooxml/extract.py and src/ooxml/models.py — use the same data
structures and the same parsing approach for re-reading footnotes.xml/
endnotes.xml (don't duplicate parsing logic if it can reasonably be
shared — but do not refactor extract.py in this step, only add
validate_extraction as a new function in the same file or a new module,
your choice, as long as imports stay consistent).

IMPLEMENTER RULES — same as Prompt 1, plus:
6. This function must never raise — it always returns a list (possibly
   empty) of human-readable problem strings, per SPEC-003 section 6.

Add to (or create alongside) src/ooxml/extract.py:

Requirements:
- Implement `validate_extraction(result: ExtractionResult, docx_path: str) -> list[str]`
  exactly per SPEC-003 section 3's docstring and section 6.
- Checks to implement:
  1. Placeholder count in result.extracted_text (count of `[[FN:` and
     `[[EN:` substrings) matches len(result.references)
  2. Every note_id in result.references exists as a real, non-separator
     entry in footnotes.xml/endnotes.xml (re-parse the docx to check —
     this is intentionally independent of whatever extract_docx_text
     already validated, as a genuine cross-check)
  3. Flag (but do not fail on) any note_id appearing more than once with
     the same type unless it's plausible as a legitimate duplicate
     citation — per SPEC-003 section 5's "Duplicate legitimate reference"
     row, this is NOT an error case, so don't add a warning for it at all;
     only check #1 and #2 need to produce warning strings
- Each returned string should be specific enough to act on, e.g.:
  "Placeholder count mismatch: 4 tokens in text, 3 references recorded"
  "Orphan footnote reference: w:id=7 not found in footnotes.xml"

### Acceptance Check
Run against orphan_reference.docx fixture — confirm validate_extraction
returns a non-empty list containing a message about w:id=5. Run against
footnotes_only.docx — confirm it returns an empty list.
```

---

## PROMPT 5: Unit Tests

```
@workspace
Reference: docs/specs/SPEC-003-ooxml-footnote-extraction.md, section 7
(the exact list of 12 required test names). Also read
tests/fixtures/generate_fixtures.py, src/ooxml/models.py, and
src/ooxml/extract.py.

IMPLEMENTER RULES — same as Prompt 1.

Create file: tests/test_ooxml_extract.py

Requirements:
- Implement all 12 test functions named exactly as listed in SPEC-003
  section 7.
- Use pytest. Add a module-level fixture or setup step that runs
  tests/fixtures/generate_fixtures.py once if the fixture files don't
  already exist (check with os.path.exists — don't regenerate every
  test run unnecessarily; regenerating is idempotent but slow).
- Each test should assert on specific values (exact reference counts,
  exact note_ids present, exact warning substrings) — not just "no
  exception raised." Vague assertions defeat the purpose of this test
  suite given how risky this component is.
- test_extract_invalid_docx_raises_invalid_docx_error should test against
  tests/fixtures/not_a_docx.txt.

### Acceptance Check
Run: `pytest tests/test_ooxml_extract.py -v`
All 12 tests must pass. If any fail, do not patch the test to make it
pass — bring the failure back to Claude with the full pytest output,
per the project's error-handling discipline (this is a spec/code
mismatch or a real bug, not something to paper over).
```

---

## After All 5 Prompts Complete

Run the full suite once more:
```powershell
pytest tests/test_ooxml_extract.py -v
```

Then commit:
```powershell
git add src/ooxml tests/fixtures tests/test_ooxml_extract.py
git commit -m "feat: Implement OOXML footnote/endnote extraction [SPEC-003]"
git push
```

Verify the push landed:
```powershell
git status
```

Then bring the results back to Claude for Stage 6 (Validity/Safety review —
"find 3 ways this could mislead or break") before marking SPEC-003 complete
in its Implementation Status section.
