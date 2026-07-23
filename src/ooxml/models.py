"""
Data models for OOXML footnote/endnote extraction.

Defines the core types used by the extraction pipeline: the note-type
enumeration, the per-reference data record, the top-level extraction
result, and the custom exception for invalid documents.
"""

from dataclasses import dataclass
from enum import Enum


class NoteType(Enum):
    """Distinguishes footnotes from endnotes in a NoteReference.

    The ``.value`` attribute yields the placeholder prefix used in the
    extracted text (``"FN"`` or ``"EN"``), matching the token contract
    defined in SPEC-003 §2.
    """
    FOOTNOTE = "FN"
    ENDNOTE = "EN"


@dataclass(frozen=True)
class NoteReference:
    """A single footnote or endnote reference found in the document body.

    Attributes
    ----------
    note_type:
        Whether this is a footnote or an endnote.
    note_id:
        The raw ``w:id`` attribute value from the source XML, kept as a
        string (not assumed to be numeric) to preserve the exact value
        for reassembly (SPEC-006).
    position:
        Character offset of the placeholder token (e.g. ``[[FN:3]]``)
        within ``ExtractionResult.extracted_text``.
    """
    note_type: NoteType
    note_id: str
    position: int


@dataclass(frozen=True)
class ExtractionResult:
    """The output of extracting plain text from a ``.docx`` file.

    ``extracted_text`` contains the document body text with every
    footnote/endnote reference replaced by a placeholder token of the
    form ``[[FN:{id}]]`` or ``[[EN:{id}]]`` (see SPEC-003 §2 —
    Placeholder Token Contract).  ``references`` lists every note
    reference in document order, one entry per placeholder token.
    ``warnings`` captures data-quality signals (orphan references,
    nested notes) that are not hard errors.

    Attributes
    ----------
    extracted_text:
        Plain text with ``[[FN:n]]`` / ``[[EN:n]]`` tokens inline.
    references:
        Ordered list of note references, one per placeholder token.
    warnings:
        Human-readable problem descriptions (empty list = clean).
    """
    extracted_text: str
    references: list[NoteReference]
    warnings: list[str]


class InvalidDocxError(ValueError):
    """Raised when a file cannot be processed as a valid ``.docx`` document.

    This covers cases such as:
    - The file is not a valid zip archive.
    - The archive is missing ``word/document.xml``.
    - The XML content is malformed.

    The exception message should describe *what* went wrong and *which*
    file was being processed, so callers can log or display actionable
    information.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)