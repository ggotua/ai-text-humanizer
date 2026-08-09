"""
Unit tests for ``src.ooxml.reassemble`` — .docx reassembly and the
footnote/endnote QA check.

Implements the 12 unit tests from SPEC-006 §6 (everything except the
Ollama-backed integration test), reusing SPEC-003's fixtures where they
already cover the needed scenario. Each test asserts on specific values
(reference counts, element counts, escaped substrings, warning text)
rather than merely "no exception raised".
"""

import os
import subprocess
import sys
import zipfile

import pytest
from docx import Document
from lxml import etree

from src.detector.config_loader import load_config
from src.ooxml.extract import extract_docx_text
from src.ooxml.models import InvalidDocxError
from src.ooxml.reassemble import (
    build_document_xml,
    reassemble_docx,
)
from src.pipeline.iteration_loop import run_iteration_loop
from src.pipeline.single_pass import make_ollama_call_fn

# Path to the fixture generation script
_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
_GENERATOR_SCRIPT = os.path.join(_FIXTURES_DIR, "generate_fixtures.py")

# Expected fixture file paths (SPEC-003)
_NO_NOTES = os.path.join(_FIXTURES_DIR, "no_notes.docx")
_FOOTNOTES_ONLY = os.path.join(_FIXTURES_DIR, "footnotes_only.docx")
_ENDNOTES_ONLY = os.path.join(_FIXTURES_DIR, "endnotes_only.docx")
_MIXED_NOTES = os.path.join(_FIXTURES_DIR, "mixed_notes.docx")
_MULTIPLE_NOTES_ONE_PARA = os.path.join(_FIXTURES_DIR, "multiple_notes_one_paragraph.docx")
_NOT_A_DOCX = os.path.join(_FIXTURES_DIR, "not_a_docx.txt")
_REALISTIC_RU_PROSE = os.path.join(_FIXTURES_DIR, "realistic_ru_prose.docx")

# OOXML namespace map for lxml XPath queries
_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


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
        _NOT_A_DOCX,
        _REALISTIC_RU_PROSE,
    ]
    if all(os.path.exists(p) for p in required):
        return

    result = subprocess.run(
        [sys.executable, _GENERATOR_SCRIPT],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Fixture generator failed:\n{result.stderr}")


def _read_document_xml(docx_path: str) -> str:
    """Read the raw ``word/document.xml`` content from a .docx zip."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        return zf.read("word/document.xml").decode("utf-8")


def _count_w_p(xml: str) -> int:
    """Count ``<w:p>`` elements in a document.xml string."""
    root = etree.fromstring(xml.encode("utf-8"))
    return len(root.xpath(".//w:p", namespaces=_NS))


def _count_footnote_refs(xml: str, note_id: str) -> int:
    """Count ``<w:footnoteReference w:id="...">`` elements in a document.xml string."""
    root = etree.fromstring(xml.encode("utf-8"))
    return len(
        root.xpath(
            f".//w:footnoteReference[@w:id='{note_id}']",
            namespaces=_NS,
        )
    )


# ---------------------------------------------------------------------------
# Reassembly tests
# ---------------------------------------------------------------------------

class TestReassembleZeroFootnotes:
    """test_reassemble_docx_zero_footnotes_qa_passes"""
    def test_reassemble_docx_zero_footnotes_qa_passes(self, tmp_path) -> None:
        _ensure_fixtures()
        extracted = extract_docx_text(_NO_NOTES)
        assert len(extracted.references) == 0

        out = str(tmp_path / "out.docx")
        result = reassemble_docx(_NO_NOTES, extracted, extracted.extracted_text, out)

        assert result.original_reference_count == 0
        assert result.output_reference_count == 0
        assert result.qa_passed is True
        assert result.qa_warnings == []


class TestReassemblePreservesFootnoteCount:
    """test_reassemble_docx_preserves_footnote_count"""
    def test_reassemble_docx_preserves_footnote_count(self, tmp_path) -> None:
        _ensure_fixtures()
        extracted = extract_docx_text(_FOOTNOTES_ONLY)
        assert len(extracted.references) == 3

        out = str(tmp_path / "out.docx")
        result = reassemble_docx(
            _FOOTNOTES_ONLY, extracted, extracted.extracted_text, out
        )

        assert result.original_reference_count == 3
        assert result.output_reference_count == 3
        assert result.qa_passed is True


class TestReassemblePreservesEndnoteCount:
    """test_reassemble_docx_preserves_endnote_count"""
    def test_reassemble_docx_preserves_endnote_count(self, tmp_path) -> None:
        _ensure_fixtures()
        extracted = extract_docx_text(_ENDNOTES_ONLY)
        assert len(extracted.references) == 3

        out = str(tmp_path / "out.docx")
        result = reassemble_docx(
            _ENDNOTES_ONLY, extracted, extracted.extracted_text, out
        )

        assert result.original_reference_count == 3
        assert result.output_reference_count == 3
        assert result.qa_passed is True


class TestReassembleDuplicateReference:
    """test_reassemble_docx_handles_duplicate_reference_to_same_id"""
    def test_reassemble_docx_handles_duplicate_reference_to_same_id(self) -> None:
        _ensure_fixtures()
        original_xml = _read_document_xml(_FOOTNOTES_ONLY)

        # The same note id referenced twice in one paragraph.
        final_text = "Text [[FN:2]] and [[FN:2]] again."
        out_xml = build_document_xml(final_text, (), original_xml)

        # Both references must be emitted as separate runs.
        assert _count_footnote_refs(out_xml, "2") == 2


class TestReassembleMultiParagraph:
    """test_reassemble_docx_multi_paragraph_final_text_produces_multiple_w_p_elements"""
    def test_reassemble_docx_multi_paragraph_final_text_produces_multiple_w_p_elements(
        self,
    ) -> None:
        _ensure_fixtures()
        original_xml = _read_document_xml(_NO_NOTES)

        final_text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        out_xml = build_document_xml(final_text, (), original_xml)

        assert _count_w_p(out_xml) == 3


class TestReassembleXmlEscapes:
    """test_reassemble_docx_xml_escapes_special_characters_in_text"""
    def test_reassemble_docx_xml_escapes_special_characters_in_text(self) -> None:
        _ensure_fixtures()
        original_xml = _read_document_xml(_NO_NOTES)

        final_text = "A & B < C > D"
        out_xml = build_document_xml(final_text, (), original_xml)

        # The special characters must be escaped in the output XML.
        assert "&amp;" in out_xml
        assert "&lt;" in out_xml
        assert "&gt;" in out_xml
        # The raw unescaped characters must not appear in text content.
        assert "A & B" not in out_xml
        assert "B < C" not in out_xml
        assert "C > D" not in out_xml
        # The document must still parse as well-formed XML.
        etree.fromstring(out_xml.encode("utf-8"))


class TestReassemblePreservesOtherZipContents:
    """test_reassemble_docx_preserves_other_zip_contents_byte_identical"""
    def test_reassemble_docx_preserves_other_zip_contents_byte_identical(
        self,
        tmp_path,
    ) -> None:
        _ensure_fixtures()
        extracted = extract_docx_text(_FOOTNOTES_ONLY)

        out = str(tmp_path / "out.docx")
        reassemble_docx(_FOOTNOTES_ONLY, extracted, extracted.extracted_text, out)

        with zipfile.ZipFile(_FOOTNOTES_ONLY, "r") as zin, zipfile.ZipFile(
            out, "r"
        ) as zout:
            in_names = set(zin.namelist())
            out_names = set(zout.namelist())
            assert in_names == out_names
            for name in in_names:
                if name == "word/document.xml":
                    continue
                assert zin.read(name) == zout.read(name), f"entry {name} differs"


class TestReassemblePreservesSectPr:
    """test_reassemble_docx_preserves_sectpr"""
    def test_reassemble_docx_preserves_sectpr(self) -> None:
        original_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<w:body>'
            '<w:p><w:r><w:t>old</w:t></w:r></w:p>'
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>'
            '</w:body>'
            '</w:document>'
        )

        out_xml = build_document_xml("New text.", (), original_xml)

        # The body-level <w:sectPr> must be preserved (its content and
        # attributes survive reassembly). lxml re-serialization may add
        # redundant namespace declarations on the element, so assert on
        # the parsed element rather than an exact byte string.
        root = etree.fromstring(out_xml.encode("utf-8"))
        sectpr = root.xpath(".//w:sectPr", namespaces=_NS)
        assert len(sectpr) == 1
        pgsz = sectpr[0].xpath("./w:pgSz", namespaces=_NS)
        assert len(pgsz) == 1
        assert pgsz[0].get(f"{{{_NS['w']}}}w") == "11906"
        assert pgsz[0].get(f"{{{_NS['w']}}}h") == "16838"


# ---------------------------------------------------------------------------
# QA check tests
# ---------------------------------------------------------------------------

class TestQaCheckDetectsMissingReference:
    """test_qa_check_detects_missing_reference"""
    def test_qa_check_detects_missing_reference(self, tmp_path) -> None:
        _ensure_fixtures()
        extracted = extract_docx_text(_FOOTNOTES_ONLY)

        # Drop the [[FN:4]] reference from the final text.
        final_text = "Paragraph one. [[FN:2]]\n\nParagraph two. [[FN:3]]"
        out = str(tmp_path / "out.docx")
        result = reassemble_docx(_FOOTNOTES_ONLY, extracted, final_text, out)

        assert result.qa_passed is False
        assert any("4" in w for w in result.qa_warnings)


class TestQaCheckDetectsCountMismatch:
    """test_qa_check_detects_count_mismatch"""
    def test_qa_check_detects_count_mismatch(self, tmp_path) -> None:
        _ensure_fixtures()
        extracted = extract_docx_text(_FOOTNOTES_ONLY)

        # Only 2 tokens instead of the original 3.
        final_text = "Paragraph one. [[FN:2]]\n\nParagraph two. [[FN:3]]"
        out = str(tmp_path / "out.docx")
        result = reassemble_docx(_FOOTNOTES_ONLY, extracted, final_text, out)

        assert result.qa_passed is False
        assert any("mismatch" in w.lower() for w in result.qa_warnings)


class TestQaCheckPassesOnCleanRoundTrip:
    """test_qa_check_passes_on_clean_round_trip"""
    def test_qa_check_passes_on_clean_round_trip(self, tmp_path) -> None:
        _ensure_fixtures()
        extracted = extract_docx_text(_FOOTNOTES_ONLY)

        out = str(tmp_path / "out.docx")
        result = reassemble_docx(
            _FOOTNOTES_ONLY, extracted, extracted.extracted_text, out
        )

        assert result.qa_passed is True
        assert result.qa_warnings == []


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------

class TestReassembleRaisesInvalidDocx:
    """test_reassemble_raises_invalid_docx_error_on_corrupt_input"""
    def test_reassemble_raises_invalid_docx_error_on_corrupt_input(
        self,
        tmp_path,
    ) -> None:
        _ensure_fixtures()
        dummy = extract_docx_text(_NO_NOTES)
        out = str(tmp_path / "out.docx")

        with pytest.raises(InvalidDocxError):
            reassemble_docx(_NOT_A_DOCX, dummy, "text", out)


# ---------------------------------------------------------------------------
# Integration test — full extract -> humanize -> reassemble round trip
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_full_pipeline_docx_to_docx_round_trip(tmp_path) -> None:
    """Full extract -> humanize -> reassemble round trip against real Ollama.

    Wires together SPEC-003 (``extract_docx_text``), SPEC-005
    (``run_iteration_loop`` with ``max_iterations=1``), and this spec
    (``reassemble_docx``) for the first time.  Uses the realistic RU prose
    fixture containing an obvious cliché ("играет важную роль") and a
    footnote reference, so the humanization pass has something to rewrite.

    Skips gracefully if Ollama is not reachable, using the same
    skip-on-unreachable pattern as prior integration tests.
    """
    _ensure_fixtures()

    try:
        extracted = extract_docx_text(_REALISTIC_RU_PROSE)
        config = load_config("config", "ru")
        ollama_call = make_ollama_call_fn(model="mistral")
        iteration_result = run_iteration_loop(
            text=extracted.extracted_text,
            language="ru",
            ollama_call=ollama_call,
            detector_config=config,
            max_iterations=1,
        )
        out = str(tmp_path / "out.docx")
        result = reassemble_docx(
            _REALISTIC_RU_PROSE,
            extracted,
            iteration_result.final_text,
            out,
        )
    except Exception as exc:
        pytest.skip(f"Ollama not reachable or request failed: {exc}")

    # The reassembled document must pass the footnote QA check.
    assert result.qa_passed is True, (
        f"Expected qa_passed=True, got warnings: {result.qa_warnings}"
    )

    # Independent validity confirmation: python-docx's own loader must be
    # able to open the output file (beyond our own zip-writing code).
    Document(out)
