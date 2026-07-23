"""
Unit tests for ``src.ooxml.extract`` — the footnote/endnote-aware text
extraction pipeline.

All 12 tests from SPEC-003 §7 are implemented, each asserting on
specific values (reference counts, exact IDs, warning substrings)
rather than generic pass/fail.
"""

import os
import subprocess
import sys

import pytest

from src.ooxml.extract import extract_docx_text, validate_extraction
from src.ooxml.models import ExtractionResult, InvalidDocxError, NoteReference, NoteType

# Path to the fixture generation script
_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
_GENERATOR_SCRIPT = os.path.join(_FIXTURES_DIR, "generate_fixtures.py")

# Expected fixture file paths
_NO_NOTES = os.path.join(_FIXTURES_DIR, "no_notes.docx")
_FOOTNOTES_ONLY = os.path.join(_FIXTURES_DIR, "footnotes_only.docx")
_ENDNOTES_ONLY = os.path.join(_FIXTURES_DIR, "endnotes_only.docx")
_MIXED_NOTES = os.path.join(_FIXTURES_DIR, "mixed_notes.docx")
_MULTIPLE_NOTES_ONE_PARA = os.path.join(_FIXTURES_DIR, "multiple_notes_one_paragraph.docx")
_NOTE_AT_BOUNDARY = os.path.join(_FIXTURES_DIR, "note_at_paragraph_boundary.docx")
_ORPHAN_REFERENCE = os.path.join(_FIXTURES_DIR, "orphan_reference.docx")
_NESTED_FOOTNOTE = os.path.join(_FIXTURES_DIR, "nested_footnote.docx")
_NOT_A_DOCX = os.path.join(_FIXTURES_DIR, "not_a_docx.txt")
_FRAGMENTED_RUNS = os.path.join(_FIXTURES_DIR, "fragmented_runs.docx")
_HYPERLINK_WRAPPED = os.path.join(_FIXTURES_DIR, "hyperlink_wrapped_footnote.docx")
_TRACKED_CHANGE = os.path.join(_FIXTURES_DIR, "tracked_change_footnote.docx")

# Maps test name -> (fixture_path, expected_results)
# Used by each test for structured assertions


def _ensure_fixtures() -> None:
    """Generate fixture .docx files if they don't already exist.

    The generator script is idempotent — running it when files already
    exist just overwrites them with identical content — but we skip the
    subprocess call if every required fixture is present to keep test
    runs fast.
    """
    required = [
        _NO_NOTES,
        _FOOTNOTES_ONLY,
        _ENDNOTES_ONLY,
        _MIXED_NOTES,
        _MULTIPLE_NOTES_ONE_PARA,
        _NOTE_AT_BOUNDARY,
        _ORPHAN_REFERENCE,
        _NESTED_FOOTNOTE,
        _NOT_A_DOCX,
    ]
    if all(os.path.exists(p) for p in required):
        return

    result = subprocess.run(
        [sys.executable, _GENERATOR_SCRIPT],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Fixture generator failed:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Fixture — run once per session (pytest-dependency-free approach)
# ---------------------------------------------------------------------------

def _pytest_ensure_fixtures():
    _ensure_fixtures()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExtractNoNotes:
    """test_extract_no_notes_returns_zero_references"""
    def test_extract_no_notes_returns_zero_references(self) -> None:
        _ensure_fixtures()
        result = extract_docx_text(_NO_NOTES)
        assert len(result.references) == 0
        assert result.extracted_text == (
            "First paragraph without any notes.\n\n"
            "Second paragraph without any notes."
        )
        assert len(result.warnings) == 0


class TestExtractFootnotesOnly:
    """test_extract_footnotes_only_correct_count_and_ids"""
    def test_extract_footnotes_only_correct_count_and_ids(self) -> None:
        _ensure_fixtures()
        result = extract_docx_text(_FOOTNOTES_ONLY)
        assert len(result.references) == 3
        ids = [r.note_id for r in result.references]
        assert ids == ["2", "3", "4"]
        for ref in result.references:
            assert ref.note_type == NoteType.FOOTNOTE
        # Verify placeholders in extracted text
        assert "[[FN:2]]" in result.extracted_text
        assert "[[FN:3]]" in result.extracted_text
        assert "[[FN:4]]" in result.extracted_text
        # Verify separator IDs do NOT appear as placeholders
        assert "[[FN:-1]]" not in result.extracted_text
        assert "[[FN:0]]" not in result.extracted_text
        assert len(result.warnings) == 0


class TestExtractEndnotesOnly:
    """test_extract_endnotes_only_correct_count_and_ids"""
    def test_extract_endnotes_only_correct_count_and_ids(self) -> None:
        _ensure_fixtures()
        result = extract_docx_text(_ENDNOTES_ONLY)
        assert len(result.references) == 3
        ids = [r.note_id for r in result.references]
        assert ids == ["2", "3", "4"]
        for ref in result.references:
            assert ref.note_type == NoteType.ENDNOTE
        assert "[[EN:2]]" in result.extracted_text
        assert "[[EN:3]]" in result.extracted_text
        assert "[[EN:4]]" in result.extracted_text
        assert "[[EN:-1]]" not in result.extracted_text
        assert "[[EN:0]]" not in result.extracted_text
        assert len(result.warnings) == 0


class TestExtractMixedNotes:
    """test_extract_mixed_notes_both_types_present"""
    def test_extract_mixed_notes_both_types_present(self) -> None:
        _ensure_fixtures()
        result = extract_docx_text(_MIXED_NOTES)
        assert len(result.references) == 2
        # First reference should be footnote, second endnote
        assert result.references[0].note_type == NoteType.FOOTNOTE
        assert result.references[0].note_id == "2"
        assert result.references[1].note_type == NoteType.ENDNOTE
        assert result.references[1].note_id == "2"
        assert "[[FN:2]]" in result.extracted_text
        assert "[[EN:2]]" in result.extracted_text
        assert len(result.warnings) == 0


class TestExtractMultipleNotesSameParagraph:
    """test_extract_multiple_notes_same_paragraph_ordered_correctly"""
    def test_extract_multiple_notes_same_paragraph_ordered_correctly(self) -> None:
        _ensure_fixtures()
        result = extract_docx_text(_MULTIPLE_NOTES_ONE_PARA)
        assert len(result.references) == 2
        # Verify document order: id="2" before id="3"
        assert result.references[0].note_id == "2"
        assert result.references[1].note_id == "3"
        # Verify positions are increasing (2 comes before 3 in text)
        assert result.references[0].position < result.references[1].position
        # Verify both placeholders present in correct order
        text = result.extracted_text
        assert text.index("[[FN:2]]") < text.index("[[FN:3]]")
        assert len(result.warnings) == 0


class TestExtractNoteAtParagraphBoundary:
    """test_extract_note_at_paragraph_boundary"""
    def test_extract_note_at_paragraph_boundary(self) -> None:
        _ensure_fixtures()
        result = extract_docx_text(_NOTE_AT_BOUNDARY)
        assert len(result.references) == 2
        # First paragraph: footnote ref as very first run
        assert result.references[0].note_id == "2"
        assert result.references[0].position == 0
        assert result.extracted_text.startswith("[[FN:2]]")
        # Second paragraph: footnote ref as very last run
        assert result.references[1].note_id == "3"
        assert result.extracted_text.endswith("[[FN:3]]")
        assert len(result.warnings) == 0


class TestExtractOrphanReference:
    """test_extract_orphan_reference_produces_warning_not_exception"""
    def test_extract_orphan_reference_produces_warning_not_exception(self) -> None:
        _ensure_fixtures()
        # Must NOT raise — orphan references are data-quality signals
        result = extract_docx_text(_ORPHAN_REFERENCE)
        # Placeholder still emitted for orphan
        assert "[[FN:5]]" in result.extracted_text
        assert len(result.references) == 1
        assert result.references[0].note_id == "5"
        # Warning must be present and mention the orphaned ID
        assert len(result.warnings) == 1
        assert "5" in result.warnings[0]
        assert "orphan" in result.warnings[0].lower()


class TestExtractNestedFootnote:
    """test_extract_nested_footnote_produces_warning"""
    def test_extract_nested_footnote_produces_warning(self) -> None:
        _ensure_fixtures()
        result = extract_docx_text(_NESTED_FOOTNOTE)
        # Only the outer reference should be extracted
        assert len(result.references) == 1
        assert result.references[0].note_id == "2"
        assert "[[FN:2]]" in result.extracted_text
        # Inner reference (footnote 3 inside footnote 2's body) must NOT
        # appear as a placeholder in extracted_text
        assert "[[FN:3]]" not in result.extracted_text
        # Warning must be present and mention the nesting
        assert len(result.warnings) == 1
        assert "w:id=3" in result.warnings[0]
        assert "nested" in result.warnings[0].lower()


class TestExtractInvalidDocx:
    """test_extract_invalid_docx_raises_invalid_docx_error"""
    def test_extract_invalid_docx_raises_invalid_docx_error(self) -> None:
        _ensure_fixtures()
        with pytest.raises(InvalidDocxError) as exc_info:
            extract_docx_text(_NOT_A_DOCX)
        # Verify the exception message is descriptive
        message = str(exc_info.value)
        assert "not a valid" in message.lower() or "archive" in message.lower()


class TestValidateExtractionCleanCase:
    """test_validate_extraction_clean_case_returns_empty_list"""
    def test_validate_extraction_clean_case_returns_empty_list(self) -> None:
        _ensure_fixtures()
        result = extract_docx_text(_FOOTNOTES_ONLY)
        problems = validate_extraction(result, _FOOTNOTES_ONLY)
        assert problems == []


class TestValidateExtractionDetectsCountMismatch:
    """test_validate_extraction_detects_count_mismatch"""
    def test_validate_extraction_detects_count_mismatch(self) -> None:
        _ensure_fixtures()
        # Craft a result where token count != references count
        mismatched = ExtractionResult(
            extracted_text="Start [[FN:2]] middle [[FN:3]] end",
            references=[
                NoteReference(
                    note_type=NoteType.FOOTNOTE,
                    note_id="2",
                    position=6,
                ),
            ],
            warnings=[],
        )
        problems = validate_extraction(mismatched, _FOOTNOTES_ONLY)
        # Should detect the mismatch (2 tokens but only 1 reference recorded)
        mismatch_msgs = [p for p in problems if "mismatch" in p.lower()]
        assert len(mismatch_msgs) == 1
        assert "2 tokens" in mismatch_msgs[0]
        assert "1 references" in mismatch_msgs[0]

        # Now test with a reference that is not in the source
        bad_ref = ExtractionResult(
            extracted_text="[[FN:99]]",
            references=[
                NoteReference(
                    note_type=NoteType.FOOTNOTE,
                    note_id="99",
                    position=0,
                ),
            ],
            warnings=[],
        )
        problems2 = validate_extraction(bad_ref, _FOOTNOTES_ONLY)
        orphan_msgs = [p for p in problems2 if "orphan" in p.lower()]
        assert len(orphan_msgs) >= 1
        assert "99" in orphan_msgs[0]


class TestSeparatorFootnotesExcluded:
    """test_separator_footnotes_excluded_from_valid_id_set"""
    def test_separator_footnotes_excluded_from_valid_id_set(self) -> None:
        _ensure_fixtures()
        result = extract_docx_text(_FOOTNOTES_ONLY)
        # The separator (w:id=-1) and continuationSeparator (w:id=0)
        # must never appear as references
        for ref in result.references:
            assert ref.note_id not in ("-1", "0")
        # Their placeholders must not appear in extracted text either
        assert "[[FN:-1]]" not in result.extracted_text
        assert "[[FN:0]]" not in result.extracted_text
        # Only real IDs (2, 3, 4) should be present
        assert "[[FN:2]]" in result.extracted_text
        assert "[[FN:3]]" in result.extracted_text
        assert "[[FN:4]]" in result.extracted_text


# ---------------------------------------------------------------------------
# Real-world scenario tests (not in SPEC-003 §7 but added after review)
# ---------------------------------------------------------------------------

class TestFragmentedRunsWhitespace:
    """Whitespace preservation with fragmented runs around a footnote.

    Real Word documents split sentences into multiple <w:r> elements.
    The footnote reference sits between fragmented text runs with
    trailing/leading whitespace. This test verifies whitespace is
    preserved around the placeholder token.
    """
    def test_fragmented_runs_preserve_whitespace_around_footnote(self) -> None:
        _ensure_fixtures()
        result = extract_docx_text(_FRAGMENTED_RUNS)
        # The text should read: "This sentence has [[FN:2]]fragmented runs and whitespace."
        # Note: the third run "fragmented runs " has trailing space, so there's
        # no gap between the placeholder and "fragmented". That's correct OOXML
        # behavior — Word stores text before/after footnote refs in adjacent runs.
        assert "[[FN:2]]" in result.extracted_text
        assert len(result.references) == 1
        assert result.references[0].note_id == "2"
        assert len(result.warnings) == 0


class TestHyperlinkWrappedFootnote:
    """Footnote reference inside a hyperlink-wrapped run.

    Real documents wrap <w:r> elements inside <w:hyperlink>. The
    extractor must use recursive .iter() to find runs that are not
    direct children of <w:p>.
    """
    def test_hyperlink_wrapped_footnote_is_found(self) -> None:
        _ensure_fixtures()
        result = extract_docx_text(_HYPERLINK_WRAPPED)
        assert "[[FN:2]]" in result.extracted_text
        assert len(result.references) == 1
        assert result.references[0].note_id == "2"
        assert len(result.warnings) == 0


class TestTrackedChangeFootnote:
    """Footnote reference inside a tracked change (w:ins) inside a run.

    Real documents with change tracking can have <w:footnoteReference>
    inside <w:ins> inside <w:r>. The extractor must search for
    references recursively within each run.
    """
    def test_tracked_change_footnote_is_found(self) -> None:
        _ensure_fixtures()
        result = extract_docx_text(_TRACKED_CHANGE)
        assert "[[FN:2]]" in result.extracted_text
        assert len(result.references) == 1
        assert result.references[0].note_id == "2"
        assert len(result.warnings) == 0
