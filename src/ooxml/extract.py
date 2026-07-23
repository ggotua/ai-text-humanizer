"""
OOXML footnote/endnote-aware text extraction from ``.docx`` files.

Implements the algorithm described in SPEC-003 §4: opens a ``.docx`` as a
zip archive, reads the body XML and optional footnotes/endnotes parts,
replaces every note reference with a placeholder token (``[[FN:{id}]]`` /
``[[EN:{id}]]``), and returns the result as an ``ExtractionResult``.
"""

import zipfile
from pathlib import Path

from lxml import etree

from src.ooxml.models import (
    ExtractionResult,
    InvalidDocxError,
    NoteReference,
    NoteType,
)

# OOXML namespace — all w: prefixed elements live here
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _read_xml_part(zf: zipfile.ZipFile, path: str) -> str | None:
    """Read a text XML part from the zip, returning ``None`` if absent."""
    try:
        return zf.read(path).decode("utf-8")
    except KeyError:
        return None


def _parse_notes(
    xml_text: str | None,
    note_type: NoteType,
) -> tuple[set[str], list[str]]:
    """Parse footnotes or endnotes XML into a set of valid (non-separator) IDs.

    Parameters
    ----------
    xml_text:
        Raw XML content of ``footnotes.xml`` or ``endnotes.xml``, or
        ``None`` if the file is absent.
    note_type:
        ``NoteType.FOOTNOTE`` or ``NoteType.ENDNOTE`` — used only for
        warning message phrasing.

    Returns
    -------
    ``(valid_ids, warnings)`` where ``valid_ids`` contains every
    ``w:id`` whose ``w:type`` is *not* ``separator`` or
    ``continuationSeparator``, and ``warnings`` lists any nested-note
    findings detected.
    """
    valid_ids: set[str] = set()
    warnings: list[str] = []

    if xml_text is None:
        return valid_ids, warnings

    root = etree.fromstring(xml_text.encode("utf-8"))

    tag_note = f"{_W}footnote" if note_type == NoteType.FOOTNOTE else f"{_W}endnote"
    type_attr = f"{_W}type"

    for note_el in root.iterchildren(tag_note):
        note_id: str | None = note_el.get(f"{_W}id")
        if note_id is None:
            continue

        note_type_attr: str | None = note_el.get(type_attr)
        if note_type_attr in ("separator", "continuationSeparator"):
            # Skip separator / continuation separator notes
            continue

        valid_ids.add(note_id)

        # Step 5: detect nested note references inside this note's body
        for child in note_el.iter():
            if child.tag == f"{_W}footnoteReference":
                nested_id = child.get(f"{_W}id", "?")
                warnings.append(
                    f"Nested footnote reference: footnote/endnote w:id={note_id} "
                    f"contains a w:footnoteReference to w:id={nested_id}"
                )
            elif child.tag == f"{_W}endnoteReference":
                nested_id = child.get(f"{_W}id", "?")
                warnings.append(
                    f"Nested endnote reference: footnote/endnote w:id={note_id} "
                    f"contains a w:endnoteReference to w:id={nested_id}"
                )

    return valid_ids, warnings


def extract_docx_text(docx_path: str) -> ExtractionResult:
    """Extract plain text from a ``.docx`` file, replacing footnote/endnote
    references with placeholder tokens.

    Parameters
    ----------
    docx_path:
        Path to a ``.docx`` file on disk.

    Returns
    -------
    ``ExtractionResult`` containing the extracted text with
    ``[[FN:{id}]]`` / ``[[EN:{id}]]`` tokens inline, a list of
    ``NoteReference`` objects (one per placeholder), and any warnings
    encountered during extraction.

    Raises
    ------
    FileNotFoundError:
        If ``docx_path`` does not exist.
    InvalidDocxError:
        If the file is not a valid zip archive, is missing
        ``word/document.xml``, or contains malformed XML.
    """
    path = Path(docx_path)
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {docx_path}")

    try:
        zf = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        raise InvalidDocxError(
            f"File is not a valid zip archive (not a .docx): {docx_path}"
        ) from exc

    try:
        # Step 1: read word/document.xml
        document_xml = _read_xml_part(zf, "word/document.xml")
        if document_xml is None:
            raise InvalidDocxError(
                f"Missing word/document.xml in archive: {docx_path}"
            )

        # Step 2: read footnotes and endnotes if present
        footnotes_xml = _read_xml_part(zf, "word/footnotes.xml")
        endnotes_xml = _read_xml_part(zf, "word/endnotes.xml")

        # Step 3: parse notes to build valid ID sets and collect warnings
        try:
            valid_footnote_ids, fn_warnings = _parse_notes(
                footnotes_xml, NoteType.FOOTNOTE
            )
            valid_endnote_ids, en_warnings = _parse_notes(
                endnotes_xml, NoteType.ENDNOTE
            )
        except etree.XMLSyntaxError as exc:
            raise InvalidDocxError(
                f"Malformed XML in {docx_path}: {exc}"
            ) from exc

        warnings: list[str] = fn_warnings + en_warnings

        # Step 4: walk document body paragraphs
        try:
            doc_root = etree.fromstring(document_xml.encode("utf-8"))
        except etree.XMLSyntaxError as exc:
            raise InvalidDocxError(
                f"Malformed XML in word/document.xml of {docx_path}: {exc}"
            ) from exc

        body = doc_root.find(f"{_W}body")
        if body is None:
            raise InvalidDocxError(
                f"Missing <w:body> in word/document.xml of {docx_path}"
            )

        text_parts: list[str] = []
        references: list[NoteReference] = []

        for paragraph_idx, p_el in enumerate(body.iterchildren(f"{_W}p")):
            # Step 4 — between paragraphs, append \n\n (except before the first)
            if paragraph_idx > 0:
                text_parts.append("\n\n")

            # Use recursive .iter() to find runs even when they are nested
            # inside hyperlinks, tracked changes (w:ins/w:del), or other
            # wrapper elements that real Word documents produce.
            runs = list(p_el.iter(f"{_W}r"))
            if not runs:
                # Empty paragraph — still contribute the boundary via the
                # \n\n we already added (or will have nothing if it's the
                # very first paragraph)
                continue

            for r_el in runs:
                # Check for footnote reference — use recursive .iter() to
                # find references even inside tracked changes (w:ins/w:del)
                # or hyperlinks that wrap the reference marker inside a run.
                fn_ref = next(r_el.iter(f"{_W}footnoteReference"), None)
                if fn_ref is not None:
                    note_id: str = fn_ref.get(f"{_W}id", "")
                    position = len("".join(text_parts))
                    placeholder = f"[[FN:{note_id}]]"
                    text_parts.append(placeholder)
                    references.append(
                        NoteReference(
                            note_type=NoteType.FOOTNOTE,
                            note_id=note_id,
                            position=position,
                        )
                    )
                    if note_id not in valid_footnote_ids:
                        warnings.append(
                            f"Orphan footnote reference: w:id={note_id} "
                            f"not found in footnotes.xml"
                        )
                    continue

                # Check for endnote reference — use recursive .iter() to
                # find references inside tracked changes or hyperlinks.
                en_ref = next(r_el.iter(f"{_W}endnoteReference"), None)
                if en_ref is not None:
                    note_id = en_ref.get(f"{_W}id", "")
                    position = len("".join(text_parts))
                    placeholder = f"[[EN:{note_id}]]"
                    text_parts.append(placeholder)
                    references.append(
                        NoteReference(
                            note_type=NoteType.ENDNOTE,
                            note_id=note_id,
                            position=position,
                        )
                    )
                    if note_id not in valid_endnote_ids:
                        warnings.append(
                            f"Orphan endnote reference: w:id={note_id} "
                            f"not found in endnotes.xml"
                        )
                    continue

                # Otherwise, extract text from w:t elements in this run
                for t_el in r_el.iterchildren(f"{_W}t"):
                    if t_el.text:
                        text_parts.append(t_el.text)

        extracted_text = "".join(text_parts)

        return ExtractionResult(
            extracted_text=extracted_text,
            references=references,
            warnings=warnings,
        )

    finally:
        zf.close()


def validate_extraction(result: ExtractionResult, docx_path: str) -> list[str]:
    """Cross-check an extraction result against the source ``.docx`` file.

    This function re-reads the docx independently (it does not trust or
    assume anything about what ``extract_docx_text`` already validated),
    and returns human-readable problem descriptions.

    Parameters
    ----------
    result:
        The ``ExtractionResult`` returned by ``extract_docx_text``.
    docx_path:
        Path to the same ``.docx`` file that produced *result*.

    Returns
    -------
    A list of problem descriptions (empty list = no problems found).
    Never raises — problems are returned as strings.
    """
    problems: list[str] = []

    # --- Check 1: placeholder count vs references count ---
    fn_count = result.extracted_text.count("[[FN:")
    en_count = result.extracted_text.count("[[EN:")
    total_tokens = fn_count + en_count

    if total_tokens != len(result.references):
        problems.append(
            f"Placeholder count mismatch: {total_tokens} tokens in text "
            f"({fn_count} FN, {en_count} EN), "
            f"{len(result.references)} references recorded"
        )
        # If counts are already mismatched, further checks may be unreliable;
        # still run them, but return early if a basic structural problem
        # prevents reading the docx.

    # --- Check 2: every note_id in references exists in the source ---
    try:
        with zipfile.ZipFile(Path(docx_path), "r") as zf:
            footnotes_xml = _read_xml_part(zf, "word/footnotes.xml")
            endnotes_xml = _read_xml_part(zf, "word/endnotes.xml")
    except (zipfile.BadZipFile, FileNotFoundError):
        problems.append(f"Cannot open file for cross-check: {docx_path}")
        return problems

    try:
        valid_footnote_ids, _ = _parse_notes(footnotes_xml, NoteType.FOOTNOTE)
        valid_endnote_ids, _ = _parse_notes(endnotes_xml, NoteType.ENDNOTE)
    except Exception:
        problems.append(f"Failed to parse XML parts in {docx_path} during cross-check")
        return problems

    for ref in result.references:
        if ref.note_type == NoteType.FOOTNOTE:
            if ref.note_id not in valid_footnote_ids:
                problems.append(
                    f"Orphan footnote reference: w:id={ref.note_id} "
                    f"not found in footnotes.xml"
                )
        else:
            if ref.note_id not in valid_endnote_ids:
                problems.append(
                    f"Orphan endnote reference: w:id={ref.note_id} "
                    f"not found in endnotes.xml"
                )

    return problems
