# PROMPT-006: .docx Reassembly & Footnote QA Check — Implementation Prompts

Reference: docs/specs/SPEC-006-docx-reassembly.md (read in full first —
especially section 1's "copy zip, replace only document.xml" architecture
and section 3's exact algorithm). Also read src/ooxml/extract.py and
src/ooxml/models.py (SPEC-003) — this spec reuses ExtractionResult,
NoteReference, PLACEHOLDER_TOKEN_PATTERN, InvalidDocxError from there.

Execute these 6 steps in order, testing after each. This is the most
XML-heavy spec in the project — expect Step 2 to be the hardest; if
Cline stalls on it, split further (e.g. "build one paragraph's runs" as
its own function before "build the whole body").

---

## STEP 1: Models and Text-Splitting Helper

```
@workspace
Reference: docs/specs/SPEC-006-docx-reassembly.md, section 2.

IMPLEMENTER RULES — follow without exception:
1. Return the COMPLETE file — no partial output.
2. Functional style: pure functions, explicit inputs/outputs.
3. For any constrained iteration: `for` loops with a fixed limit, never `while`.
4. Do not assume imports/variables not shown here.
5. Implement only what is asked.

Create file: src/ooxml/reassemble.py

Requirements for this step — implement ONLY these, not build_document_xml
or reassemble_docx yet:

- ReassemblyResult frozen dataclass exactly per SPEC-006 section 2.
- A helper function `xml_escape(text: str) -> str` that escapes `&`, `<`,
  `>` (in that order — & must be escaped first, or you'll double-escape
  the entities you just inserted) for safe inclusion in XML text content.
- A helper function `split_paragraph_into_segments(paragraph_text: str) -> list[tuple[str, str | None]]`
  that splits a paragraph's text using PLACEHOLDER_TOKEN_PATTERN
  (import from src.ooxml.models, do not redefine), returning an ordered
  list of (text_or_token, token_type_or_None) tuples — for a plain-text
  segment, the tuple is (text, None); for a token match, the tuple is
  (full_token_string, "FN" or "EN"). Include this even for empty
  plain-text segments between two adjacent tokens (don't skip them) —
  section 3 step 4b of the spec requires handling this.

### Acceptance Check
```
python -c "
from src.ooxml.reassemble import split_paragraph_into_segments, xml_escape
segments = split_paragraph_into_segments('Текст [[FN:3]] и [[EN:1]] тут.')
for s in segments:
    print(s)
print(xml_escape('A & B < C'))
"
```
Should show alternating text/token tuples and print `A &amp; B &lt; C`.
```

---

## STEP 2: build_document_xml

```
@workspace
Reference: docs/specs/SPEC-006-docx-reassembly.md, section 3 steps 1-5
(the full algorithm for constructing the new document.xml). Read
src/ooxml/reassemble.py from Step 1 — add to it, reuse
split_paragraph_into_segments and xml_escape, don't reimplement them.
Also read src/ooxml/models.py for NoteReference's exact fields.

IMPLEMENTER RULES — same as Step 1.

Add to src/ooxml/reassemble.py (return the complete file):

`build_document_xml(final_text: str, references: tuple[NoteReference, ...], original_document_xml: str) -> str`

Implement per SPEC-006 section 3:
1. Parse `original_document_xml` (use lxml.etree, consistent with
   SPEC-003's approach) to extract:
   a. The opening `<w:document ...>` tag with ALL its namespace
      declarations, verbatim — do not hand-write namespace declarations,
      copy them from the parsed original.
   b. The `<w:sectPr>` element that is a direct child of `<w:body>`
      (if present) — serialize it back to an XML string to reuse.
2. Split `final_text` on `\n\n` into paragraphs.
3. For each paragraph, use `split_paragraph_into_segments` to get its
   (text_or_token, type) tuples. For each tuple:
   - If type is None: build `<w:r><w:t xml:space="preserve">{xml_escape(text)}</w:t></w:r>`
   - If type is "FN" or "EN": parse the numeric/string id out of the
     token (format is `[[FN:3]]` or `[[EN:7]]` — extract the part after
     the colon and before the closing `]]`). Build
     `<w:r><w:footnoteReference w:id="{id}"/></w:r>` or
     `<w:r><w:endnoteReference w:id="{id}"/></w:r>` accordingly.
     (Do not look up the id against `references` in this function — per
     SPEC-006 section 3 step 4c, that lookup/orphan-handling happens at
     a higher level; this function just needs to emit a syntactically
     correct reference run for any token it sees.)
   Wrap all the runs for one paragraph in `<w:p>{runs}</w:p>`.
4. Concatenate all `<w:p>` elements, then append the preserved `<w:sectPr>`
   XML string from step 1b (empty string if none was found in the original).
5. Build the final document by wrapping the body content
   (`<w:body>{paragraphs}{sectpr}</w:body>`) inside the original
   `<w:document ...>` opening tag from step 1a and its matching
   `</w:document>` closing tag. Return this as a complete XML string
   (including the `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`
   declaration line, matching what a real Word document.xml starts with).

### Acceptance Check
Manually construct a minimal `original_document_xml` string (a
`<w:document>` wrapper with a `<w:sectPr>` inside `<w:body>`) and a
`final_text` with 2 paragraphs, one containing a `[[FN:2]]` token.
Call build_document_xml and print the result — visually confirm: correct
namespace declarations copied through, 2 `<w:p>` elements, one
`<w:footnoteReference w:id="2"/>` in the right paragraph, `<w:sectPr>`
present at the end before `</w:body>`.
```

---

## STEP 3: reassemble_docx (zip orchestration)

```
@workspace
Reference: docs/specs/SPEC-006-docx-reassembly.md, section 3 steps 6-8
and section 5 (Error Handling — atomic write pattern). Read
src/ooxml/reassemble.py from Steps 1-2 — use build_document_xml, don't
reimplement its logic. Also read src/ooxml/extract.py for
InvalidDocxError and the zip-opening pattern used there.

IMPLEMENTER RULES — same as Step 1.

Add to src/ooxml/reassemble.py (return the complete file):

`reassemble_docx(original_docx_path: str, extraction_result: ExtractionResult, final_text: str, output_docx_path: str) -> ReassemblyResult`

Requirements:
- Open original_docx_path as a zip (raise InvalidDocxError on failure,
  same pattern as SPEC-003's extract_docx_text).
- Read word/document.xml raw content from it.
- Call build_document_xml(final_text, extraction_result.references, raw_document_xml)
  to get the new XML.
- Write output_docx_path using the ATOMIC WRITE PATTERN per SPEC-006
  section 5: write to a temporary path first (e.g.
  output_docx_path + ".tmp"), and only rename/move it to output_docx_path
  on full success. Use Python's `zipfile.ZipFile` in write mode: copy
  every entry from the original zip EXCEPT "word/document.xml" using
  `.writestr()` with the original entry's raw bytes, then write the new
  XML as "word/document.xml".
- After writing, call run_footnote_qa_check (implemented in Step 4 — for
  THIS step, just call it, even though it doesn't exist yet; Step 4 will
  add it to this same file) and use its result to populate
  ReassemblyResult's qa_passed/qa_warnings fields.
- original_reference_count = len(extraction_result.references)
- output_reference_count: count placeholder tokens actually present in
  final_text (reuse a token-counting approach consistent with earlier
  specs — e.g. len(PLACEHOLDER_TOKEN_PATTERN.findall(final_text)) type logic)

Note: this step references run_footnote_qa_check before it's implemented
— that's expected, implement Step 3 and Step 4 together if needed, or
add a placeholder stub for run_footnote_qa_check that Step 4 will replace.

### Acceptance Check
Do not run this in isolation if run_footnote_qa_check isn't implemented
yet — proceed to Step 4 and test both together.
```

---

## STEP 4: run_footnote_qa_check

```
@workspace
Reference: docs/specs/SPEC-006-docx-reassembly.md, section 2 (this
function's docstring) and section 3 step 7. Read src/ooxml/extract.py —
reuse extract_docx_text, don't reimplement extraction logic.

IMPLEMENTER RULES — same as Step 1.

Add to src/ooxml/reassemble.py (return the complete file, including a
real implementation replacing any Step 3 placeholder):

`run_footnote_qa_check(extraction_result: ExtractionResult, output_docx_path: str) -> tuple[bool, list[str]]`

Implement per SPEC-006 section 2's docstring:
1. Call extract_docx_text(output_docx_path) to get the output document's
   own ExtractionResult.
2. Compare total reference counts (original extraction_result.references
   vs output's) — if they differ, add a warning string naming the
   discrepancy.
3. For every (type, id) in extraction_result.references, confirm at
   least one matching (type, id) exists in the output's references — if
   any are missing, add a warning naming which one.
4. Since build_document_xml only puts reference runs inside <w:p>
   elements (never dangling), point 3 of SPEC-006 section 2's docstring
   ("every reference resolves inside some <w:p>") is implicitly satisfied
   by construction — but add a comment noting this rather than silently
   assuming it without any check at all; if extract_docx_text's own
   re-parse succeeds without warnings about orphans, that's your
   independent confirmation.
5. Return (len(warnings) == 0, warnings).

### Acceptance Check
`pytest tests/test_reassemble.py -v` — will fail until Step 5 creates
this file, so for now, manually test:
Using one of SPEC-003's existing fixtures (e.g.
tests/fixtures/footnotes_only.docx), call extract_docx_text on it,
then reassemble_docx with final_text set to the SAME extracted_text
unchanged (a no-op round trip), write to a temp output path, and confirm
ReassemblyResult.qa_passed is True.
```

---

## STEP 5: Unit Tests

```
@workspace
Reference: docs/specs/SPEC-006-docx-reassembly.md, section 6's first
test list (everything except the integration test). Read
src/ooxml/reassemble.py from Steps 1-4 and
tests/fixtures/generate_fixtures.py (SPEC-003) for existing fixtures to
reuse.

IMPLEMENTER RULES — same as Step 1.

Create file: tests/test_reassemble.py

Requirements:
- Implement all test functions from SPEC-006 section 6's list.
- Reuse SPEC-003's existing fixtures (footnotes_only.docx,
  endnotes_only.docx, mixed_notes.docx, multiple_notes_one_paragraph.docx,
  no_notes.docx) rather than creating new ones where they already cover
  the needed scenario.
- For test_reassemble_docx_preserves_other_zip_contents_byte_identical:
  open both input and output as zip archives, iterate all entries except
  "word/document.xml", compare raw bytes with assertEqual/assert ==.
- For test_reassemble_docx_xml_escapes_special_characters_in_text: use
  input text containing literal `&`, `<`, `>` characters, confirm the
  output document.xml has them properly escaped (and confirm the
  document still opens successfully — e.g. via python-docx or by
  re-parsing with lxml without error).
- Each test asserts specific values, not just "no exception raised."

### Acceptance Check
Run: `pytest tests/test_reassemble.py -v`
All tests must pass.
```

---

## STEP 6: Full Round-Trip Integration Test

```
@workspace
Reference: docs/specs/SPEC-006-docx-reassembly.md, section 6's
integration test requirement. Read tests/test_iteration_loop.py's
existing integration test pattern (skip-on-unreachable) and
src/pipeline/iteration_loop.py, src/pipeline/single_pass.py — this test
wires together SPEC-003 + SPEC-005 + this spec for the first time.

IMPLEMENTER RULES — same as Step 1.

Add to tests/test_reassemble.py:

Requirements:
- Implement test_full_pipeline_docx_to_docx_round_trip() marked
  @pytest.mark.integration.
- If the existing SPEC-003 fixtures don't have realistic prose (they
  were built for structural extraction testing, likely short/synthetic
  sentences), create ONE new fixture with 2-3 short paragraphs of
  realistic RU text containing an obvious cliché AND a footnote
  reference in one of the paragraphs — add a generator function for it
  in tests/fixtures/generate_fixtures.py if a suitable fixture doesn't
  already exist, following the same hand-built-XML pattern as the
  existing fixture functions there.
- Pipeline: extract_docx_text(fixture_path) → run_iteration_loop(
  extracted.extracted_text, "ru", make_ollama_call_fn(), load_config("config", "ru"),
  max_iterations=1) → reassemble_docx(fixture_path, extracted, iteration_result.final_text, output_path)
- Wrap in try/except, pytest.skip() with clear message on any failure —
  same pattern as prior integration tests.
- Assert (only if no skip): result.qa_passed is True, and the output
  file at output_path can be opened successfully via python-docx's
  Document() loader without raising (independent validity confirmation
  beyond our own zip-writing code).

### Acceptance Check
With Ollama running and mistral pulled:
`pytest tests/test_reassemble.py -v -m integration`
Expect this to take roughly as long as SPEC-004's single-pass test
(~1-2 minutes, since max_iterations=1 keeps it to one grammar+style cycle).
```

---

## After All 6 Steps Complete

```powershell
pytest tests/test_reassemble.py -v
git add src/ooxml/reassemble.py tests/test_reassemble.py tests/fixtures/
git commit -m "feat: Implement docx reassembly and footnote QA check [SPEC-006]"
git push
git status
```

**Before considering this done — open the actual output .docx file in Word**
(not just via python-docx's loader) and visually confirm the footnote
appears correctly and is readable. This is the one point in the entire
project where a human eyeball check on the real target application
matters more than any automated test — automated tests confirm the XML
is well-formed and the counts match, but only opening it in Word confirms
it actually looks right to a human reader.

Then bring results back to Claude for Stage 6 (validity review) — given
this spec's complexity and that it's the final integration point for the
project's core value proposition, expect a thorough review before marking
SPEC-006 complete.
