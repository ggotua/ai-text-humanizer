"""
OOXML document reassembly and footnote/endnote QA check.

Implements SPEC-006: takes a humanized text (with placeholder tokens
intact) and the original ``.docx`` extraction metadata, and produces a
real ``.docx`` with footnotes/endnotes correctly re-attached, plus an
automated QA check.
"""

import os
import re
import zipfile

from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from src.detector.models import PLACEHOLDER_TOKEN_PATTERN
from src.ooxml.extract import extract_docx_text
from src.ooxml.models import (
    ExtractionResult,
    InvalidDocxError,
    NoteReference,
    NoteType,
)

# Compile the shared placeholder pattern once at module level.
_PLACEHOLDER_RE = re.compile(PLACEHOLDER_TOKEN_PATTERN)

# OOXML namespace — all w: prefixed elements live here
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Standard XML declaration line a real Word document.xml starts with.
_XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'

# The single zip entry that reassembly replaces; everything else is copied
# through byte-for-byte unchanged.
_DOCUMENT_XML_ENTRY = "word/document.xml"


@dataclass(frozen=True)
class ReassemblyResult:
    """The outcome of reassembling a ``.docx`` from humanized text.

    Attributes
    ----------
    output_docx_path:
        Path where the reassembled ``.docx`` was written.
    original_reference_count:
        Number of footnote/endnote references in the original document.
    output_reference_count:
        Number of footnote/endnote references in the output document.
    qa_passed:
        Whether the automated QA check passed.
    qa_warnings:
        Human-readable QA problem descriptions (empty iff ``qa_passed``).
    """
    output_docx_path: str
    original_reference_count: int
    output_reference_count: int
    qa_passed: bool
    qa_warnings: list[str]


def xml_escape(text: str) -> str:
    """Escape ``&``, ``<``, ``>`` for safe inclusion in XML text content.

    ``&`` must be escaped first so the entities inserted for ``<`` and
    ``>`` are not themselves double-escaped.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def split_paragraph_into_segments(
    paragraph_text: str,
) -> list[tuple[str, str | None]]:
    """Split a paragraph's text into alternating plain-text and token segments.

    Uses ``PLACEHOLDER_TOKEN_PATTERN`` to find ``[[FN:n]]`` / ``[[EN:n]]``
    tokens. Returns an ordered list of ``(text_or_token, type_or_None)``
    tuples:

    - For a plain-text segment: ``(text, None)``.
    - For a token match: ``(full_token_string, "FN" or "EN")``.

    Empty plain-text segments between two adjacent tokens are preserved
    (not skipped), per SPEC-006 section 3 step 4b.
    """
    segments: list[tuple[str, str | None]] = []
    cursor = 0

    for match in _PLACEHOLDER_RE.finditer(paragraph_text):
        start, end = match.span()

        # Plain-text segment before this token (may be empty).
        segments.append((paragraph_text[cursor:start], None))

        token = match.group(0)
        token_type = "FN" if token.startswith("[[FN:") else "EN"
        segments.append((token, token_type))

        cursor = end

    # Trailing plain-text segment after the last token (may be empty).
    segments.append((paragraph_text[cursor:], None))

    return segments


def _extract_document_wrapper_tags(original_document_xml: str) -> tuple[str, str]:
    """Return the verbatim opening and closing ``<w:document>`` tags.

    The opening tag includes every namespace declaration copied from the
    original document, so Word's required namespaces are never
    hand-reconstructed (SPEC-006 section 3 step 5).
    """
    root = etree.fromstring(original_document_xml.encode("utf-8"))
    root_str = etree.tostring(root, encoding="unicode")

    first_gt = root_str.find(">")
    opening_tag = root_str[: first_gt + 1]

    last_close = root_str.rfind("</")
    closing_tag = root_str[last_close:]

    return opening_tag, closing_tag


def _extract_body_sectpr(original_document_xml: str) -> str:
    """Return the body-level ``<w:sectPr>`` serialized to a string, or ``""``.

    Only the ``<w:sectPr>`` that is a direct child of ``<w:body>`` is
    preserved (SPEC-006 section 3 step 2 — multi-section documents are an
    explicit MVP limitation).
    """
    root = etree.fromstring(original_document_xml.encode("utf-8"))
    body = root.find(f"{_W}body")
    if body is None:
        return ""
    sectpr = body.find(f"{_W}sectPr")
    if sectpr is None:
        return ""
    return etree.tostring(sectpr, encoding="unicode")


def _build_paragraph_xml(paragraph_text: str) -> str:
    """Build a single ``<w:p>`` element from a paragraph's text.

    Plain-text segments become ``<w:r><w:t xml:space="preserve">`` runs
    (XML-escaped); ``[[FN:n]]`` / ``[[EN:n]]`` tokens become footnote /
    endnote reference runs. The note id is parsed from the token string
    only — no lookup against ``references`` happens here (that
    orphan-handling lives at a higher level, per SPEC-006 section 3 step 4c).
    """
    runs: list[str] = []

    for text_or_token, token_type in split_paragraph_into_segments(paragraph_text):
        if token_type is None:
            runs.append(
                f'<w:r><w:t xml:space="preserve">'
                f"{xml_escape(text_or_token)}"
                f"</w:t></w:r>"
            )
        else:
            # Token format is [[FN:3]] or [[EN:7]] — extract the part after
            # the colon and before the closing ]].
            note_id = text_or_token[len("[[") : -len("]]")].split(":", 1)[1]
            if token_type == "FN":
                runs.append(f'<w:r><w:footnoteReference w:id="{note_id}"/></w:r>')
            else:
                runs.append(f'<w:r><w:endnoteReference w:id="{note_id}"/></w:r>')

    return f"<w:p>{''.join(runs)}</w:p>"


def build_document_xml(
    final_text: str,
    references: tuple[NoteReference, ...],
    original_document_xml: str,
) -> str:
    """Construct the new ``word/document.xml`` content.

    Splits ``final_text`` into paragraphs on ``\\n\\n``, builds a
    ``<w:p>`` element per paragraph (via ``split_paragraph_into_segments``
    and ``xml_escape``), and wraps the resulting body content inside the
    original document's ``<w:document>`` opening tag (namespaces copied
    verbatim) and closing tag, with the original body-level ``<w:sectPr>``
    appended as the last element of the body.

    ``references`` is accepted for signature compatibility with SPEC-006
    section 2 but is intentionally not used here — note-id lookup and
    orphan handling happen at a higher level (section 3 step 4c).
    """
    opening_tag, closing_tag = _extract_document_wrapper_tags(original_document_xml)
    sectpr = _extract_body_sectpr(original_document_xml)

    paragraphs = final_text.split("\n\n")
    paragraphs_xml = "".join(_build_paragraph_xml(p) for p in paragraphs)

    body_content = f"<w:body>{paragraphs_xml}{sectpr}</w:body>"

    return f"{_XML_DECLARATION}{opening_tag}{body_content}{closing_tag}"


def run_footnote_qa_check(
    extraction_result: ExtractionResult,
    output_docx_path: str,
) -> tuple[bool, list[str]]:
    """Re-extract the output ``.docx`` and compare it against the original.

    Checks:
    1. Total reference count (original vs output) must match exactly.
    2. Every ``(note_type, note_id)`` present in ``extraction_result`` is
       also present in the output's references (order doesn't matter).
    3. Every reference in the output resolves inside some ``<w:p>`` — this
       is implicitly guaranteed by construction (``build_document_xml``
       only ever places reference runs inside ``<w:p>`` elements), and is
       independently confirmed here by ``extract_docx_text`` re-parsing the
       output without orphan warnings.

    Returns ``(passed, warnings)`` — ``warnings`` is empty iff ``passed``.
    """
    warnings: list[str] = []

    output_result = extract_docx_text(output_docx_path)

    # Check 1: total reference count must match exactly.
    original_count = len(extraction_result.references)
    output_count = len(output_result.references)
    if original_count != output_count:
        warnings.append(
            f"Reference count mismatch: original has {original_count}, "
            f"output has {output_count}"
        )

    # Check 2: every (type, id) in the original must exist in the output.
    original_pairs = {
        (ref.note_type, ref.note_id) for ref in extraction_result.references
    }
    output_pairs = {
        (ref.note_type, ref.note_id) for ref in output_result.references
    }
    for note_type, note_id in sorted(original_pairs, key=lambda p: (p[0].value, p[1])):
        if (note_type, note_id) not in output_pairs:
            warnings.append(
                f"Missing reference in output: {note_type.value} w:id={note_id}"
            )

    # Check 3: every reference resolves inside some <w:p>. build_document_xml
    # only ever emits reference runs inside <w:p> elements (never dangling
    # directly under <w:body>), so this is satisfied by construction. As an
    # independent confirmation, extract_docx_text re-parses the output; if it
    # reported orphan warnings, those would surface here as well.
    warnings.extend(output_result.warnings)

    return (len(warnings) == 0, warnings)


def reassemble_docx(
    original_docx_path: str,
    extraction_result: ExtractionResult,
    final_text: str,
    output_docx_path: str,
) -> ReassemblyResult:
    """Produce a ``.docx`` at ``output_docx_path`` from humanized text.

    Copies the original ``.docx`` zip contents through unchanged EXCEPT
    ``word/document.xml``, which is rebuilt from ``final_text`` via
    ``build_document_xml``. Runs the QA check automatically before
    returning.

    Raises ``InvalidDocxError`` if ``original_docx_path`` can't be opened
    as a valid ``.docx``. Does not raise on QA check failures — those are
    reported via ``ReassemblyResult.qa_passed`` / ``qa_warnings``.
    """
    path = Path(original_docx_path)
    if not path.exists():
        raise InvalidDocxError(f"Document not found: {original_docx_path}")

    try:
        zf = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        raise InvalidDocxError(
            f"File is not a valid zip archive (not a .docx): {original_docx_path}"
        ) from exc

    try:
        try:
            raw_document_xml = zf.read(_DOCUMENT_XML_ENTRY).decode("utf-8")
        except KeyError as exc:
            raise InvalidDocxError(
                f"Missing word/document.xml in archive: {original_docx_path}"
            ) from exc

        new_document_xml = build_document_xml(
            final_text,
            tuple(extraction_result.references),
            raw_document_xml,
        )

        # Atomic write pattern (SPEC-006 section 5): write to a temporary
        # path first, then rename into place only on full success.
        temp_path = f"{output_docx_path}.tmp"
        try:
            with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as out_zf:
                for item in zf.infolist():
                    if item.filename == _DOCUMENT_XML_ENTRY:
                        continue
                    out_zf.writestr(item, zf.read(item.filename))
                out_zf.writestr(_DOCUMENT_XML_ENTRY, new_document_xml)
            os.replace(temp_path, output_docx_path)
        except Exception:
            # Do not leave a corrupt partial zip at the temp path.
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise
    finally:
        zf.close()

    qa_passed, qa_warnings = run_footnote_qa_check(
        extraction_result, output_docx_path
    )

    return ReassemblyResult(
        output_docx_path=output_docx_path,
        original_reference_count=len(extraction_result.references),
        output_reference_count=len(re.findall(PLACEHOLDER_TOKEN_PATTERN, final_text)),
        qa_passed=qa_passed,
        qa_warnings=qa_warnings,
    )
