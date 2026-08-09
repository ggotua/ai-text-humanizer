"""
Generate minimal valid .docx test fixtures by hand-assembling OOXML parts.

python-docx cannot create footnotes/endnotes natively, so this script builds
the required OOXML parts as strings and zips them, producing small synthetic
.docx files for the test suite.

Usage:
    python tests/fixtures/generate_fixtures.py
"""

import os
import zipfile

# ---------------------------------------------------------------------------
# Module-level OOXML boilerplate constants
# ---------------------------------------------------------------------------

RELS_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\n'
    '</Relationships>\n'
)

CONTENT_TYPES_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
    '  <Default Extension="xml" ContentType="application/xml"/>\n'
    '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
)

CONTENT_TYPES_FOOTNOTE_OVERRIDE = (
    '  <Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>\n'
)

CONTENT_TYPES_ENDNOTE_OVERRIDE = (
    '  <Override PartName="/word/endnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"/>\n'
)

CONTENT_TYPES_TAIL = '</Types>\n'

DOCUMENT_RELS_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
)

DOCUMENT_RELS_FOOTNOTE = (
    '  <Relationship Id="rFootnotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>\n'
)

DOCUMENT_RELS_ENDNOTE = (
    '  <Relationship Id="rEndnotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes" Target="endnotes.xml"/>\n'
)

DOCUMENT_RELS_TAIL = '</Relationships>\n'

DOCUMENT_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
    '  <w:body>\n'
)

DOCUMENT_TAIL = '  </w:body>\n</w:document>\n'

FOOTNOTES_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
)

FOOTNOTES_TAIL = '</w:footnotes>\n'

ENDNOTES_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:endnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
)

ENDNOTES_TAIL = '</w:endnotes>\n'

# ---------------------------------------------------------------------------
# Helpers for building XML fragments
# ---------------------------------------------------------------------------

def _paragraph(*run_xmls: str) -> str:
    """Build a ``<w:p>`` element containing the given run XML fragments."""
    runs = ''.join(run_xmls)
    return f'    <w:p>{runs}</w:p>\n'

def _run(text: str) -> str:
    """Build a ``<w:r><w:t>text</w:t></w:r>`` element (escaped text)."""
    escaped = _xml_escape(text)
    return f'      <w:r><w:t xml:space="preserve">{escaped}</w:t></w:r>\n'

def _footnote_ref(note_id: str) -> str:
    """Build a ``<w:r><w:footnoteReference w:id="..."/></w:r>`` element."""
    return f'      <w:r><w:footnoteReference w:id="{note_id}"/></w:r>\n'

def _endnote_ref(note_id: str) -> str:
    """Build a ``<w:r><w:endnoteReference w:id="..."/></w:r>`` element."""
    return f'      <w:r><w:endnoteReference w:id="{note_id}"/></w:r>\n'

def _xml_escape(text: str) -> str:
    """Escape XML special characters."""
    amp = chr(38) + "amp;"
    lt = chr(38) + "lt;"
    gt = chr(38) + "gt;"
    quot = chr(38) + "quot;"
    return text.replace(chr(38), amp).replace(chr(60), lt).replace(chr(62), gt).replace(chr(34), quot)

def _separator_footnote() -> str:
    """Return the reserved separator footnote (w:id=\"-1\", w:type=\"separator\")."""
    return (
        '  <w:footnote w:id="-1" w:type="separator">\n'
        '    <w:p>\n'
        '      <w:r>\n'
        '        <w:separator/>\n'
        '      </w:r>\n'
        '    </w:p>\n'
        '  </w:footnote>\n'
    )

def _continuation_separator_footnote() -> str:
    """Return the reserved continuation-separator footnote (w:id=\"0\", w:type=\"continuationSeparator\")."""
    return (
        '  <w:footnote w:id="0" w:type="continuationSeparator">\n'
        '    <w:p>\n'
        '      <w:r>\n'
        '        <w:continuationSeparator/>\n'
        '      </w:r>\n'
        '    </w:p>\n'
        '  </w:footnote>\n'
    )

def _separator_endnote() -> str:
    """Return the reserved separator endnote (w:id=\"-1\", w:type=\"separator\")."""
    return (
        '  <w:endnote w:id="-1" w:type="separator">\n'
        '    <w:p>\n'
        '      <w:r>\n'
        '        <w:separator/>\n'
        '      </w:r>\n'
        '    </w:p>\n'
        '  </w:endnote>\n'
    )

def _continuation_separator_endnote() -> str:
    """Return the reserved continuation-separator endnote (w:id=\"0\", w:type=\"continuationSeparator\")."""
    return (
        '  <w:endnote w:id="0" w:type="continuationSeparator">\n'
        '    <w:p>\n'
        '      <w:r>\n'
        '        <w:continuationSeparator/>\n'
        '      </w:r>\n'
        '    </w:p>\n'
        '  </w:endnote>\n'
    )

def _content_footnote(note_id: str, body_xml: str) -> str:
    """Build a ``<w:footnote>`` element with the given ID and body XML."""
    return (
        f'  <w:footnote w:id="{note_id}">\n'
        f'{body_xml}'
        f'  </w:footnote>\n'
    )

def _content_endnote(note_id: str, body_xml: str) -> str:
    """Build a ``<w:endnote>`` element with the given ID and body XML."""
    return (
        f'  <w:endnote w:id="{note_id}">\n'
        f'{body_xml}'
        f'  </w:endnote>\n'
    )

# ---------------------------------------------------------------------------
# Core assembly function
# ---------------------------------------------------------------------------

def build_minimal_docx(
    output_path: str,
    body_xml: str,
    footnotes_xml: str | None = None,
    endnotes_xml: str | None = None,
) -> None:
    """Assemble a valid .docx zip from the given XML fragments plus boilerplate.

    Parameters
    ----------
    output_path:
        Path for the generated .docx file (e.g. ``"tests/fixtures/no_notes.docx"``).
    body_xml:
        Inner content of ``<w:body>`` — one or more ``<w:p>`` elements.
    footnotes_xml:
        Full ``<w:footnotes>`` content (including header/footer constants).
        ``None`` = no footnotes.xml in the archive.
    endnotes_xml:
        Full ``<w:endnotes>`` content (including header/footer constants).
        ``None`` = no endnotes.xml in the archive.
    """
    # Build [Content_Types].xml
    content_types = CONTENT_TYPES_HEAD
    if footnotes_xml is not None:
        content_types += CONTENT_TYPES_FOOTNOTE_OVERRIDE
    if endnotes_xml is not None:
        content_types += CONTENT_TYPES_ENDNOTE_OVERRIDE
    content_types += CONTENT_TYPES_TAIL

    # Build word/_rels/document.xml.rels
    doc_rels = DOCUMENT_RELS_HEAD
    if footnotes_xml is not None:
        doc_rels += DOCUMENT_RELS_FOOTNOTE
    if endnotes_xml is not None:
        doc_rels += DOCUMENT_RELS_ENDNOTE
    doc_rels += DOCUMENT_RELS_TAIL

    # Build word/document.xml
    document_xml = DOCUMENT_HEAD + body_xml + DOCUMENT_TAIL

    # Ensure the output directory exists
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", RELS_RELS)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        zf.writestr("word/document.xml", document_xml)
        if footnotes_xml is not None:
            zf.writestr("word/footnotes.xml", footnotes_xml)
        if endnotes_xml is not None:
            zf.writestr("word/endnotes.xml", endnotes_xml)


# ---------------------------------------------------------------------------
# Fixture generator functions
# ---------------------------------------------------------------------------

FIXTURES_DIR = os.path.join(os.path.dirname(__file__))

def _fixture_path(name: str) -> str:
    return os.path.join(FIXTURES_DIR, name)


def generate_no_notes() -> str:
    """Fixture 1: 2 plain paragraphs, no notes."""
    body = (
        _paragraph(_run("First paragraph without any notes."))
        + _paragraph(_run("Second paragraph without any notes."))
    )
    path = _fixture_path("no_notes.docx")
    build_minimal_docx(path, body)
    return path


def generate_footnotes_only() -> str:
    """Fixture 2: 3 paragraphs, footnotes w:id=\"2\", \"3\", \"4\"."""
    body = (
        _paragraph(_run("Paragraph one. "), _footnote_ref("2"))
        + _paragraph(_run("Paragraph two. "), _footnote_ref("3"))
        + _paragraph(_run("Paragraph three. "), _footnote_ref("4"))
    )
    footnotes = (
        FOOTNOTES_HEAD
        + _separator_footnote()
        + _continuation_separator_footnote()
        + _content_footnote("2", _paragraph(_run("First footnote text.")))
        + _content_footnote("3", _paragraph(_run("Second footnote text.")))
        + _content_footnote("4", _paragraph(_run("Third footnote text.")))
        + FOOTNOTES_TAIL
    )
    path = _fixture_path("footnotes_only.docx")
    build_minimal_docx(path, body, footnotes_xml=footnotes)
    return path


def generate_endnotes_only() -> str:
    """Fixture 3: 3 paragraphs, endnotes w:id=\"2\", \"3\", \"4\"."""
    body = (
        _paragraph(_run("Paragraph one. "), _endnote_ref("2"))
        + _paragraph(_run("Paragraph two. "), _endnote_ref("3"))
        + _paragraph(_run("Paragraph three. "), _endnote_ref("4"))
    )
    endnotes = (
        ENDNOTES_HEAD
        + _separator_endnote()
        + _continuation_separator_endnote()
        + _content_endnote("2", _paragraph(_run("First endnote text.")))
        + _content_endnote("3", _paragraph(_run("Second endnote text.")))
        + _content_endnote("4", _paragraph(_run("Third endnote text.")))
        + ENDNOTES_TAIL
    )
    path = _fixture_path("endnotes_only.docx")
    build_minimal_docx(path, body, endnotes_xml=endnotes)
    return path


def generate_mixed_notes() -> str:
    """Fixture 4: one footnote (w:id=\"2\") and one endnote (w:id=\"2\") in different paragraphs.

    Note both can validly be id=\"2\" because footnote IDs and endnote IDs
    are independent namespaces.
    """
    body = (
        _paragraph(_run("A paragraph with a footnote. "), _footnote_ref("2"))
        + _paragraph(_run("A paragraph with an endnote. "), _endnote_ref("2"))
    )
    footnotes = (
        FOOTNOTES_HEAD
        + _separator_footnote()
        + _continuation_separator_footnote()
        + _content_footnote("2", _paragraph(_run("Mixed footnote text.")))
        + FOOTNOTES_TAIL
    )
    endnotes = (
        ENDNOTES_HEAD
        + _separator_endnote()
        + _continuation_separator_endnote()
        + _content_endnote("2", _paragraph(_run("Mixed endnote text.")))
        + ENDNOTES_TAIL
    )
    path = _fixture_path("mixed_notes.docx")
    build_minimal_docx(path, body, footnotes_xml=footnotes, endnotes_xml=endnotes)
    return path


def generate_multiple_notes_one_paragraph() -> str:
    """Fixture 5: single paragraph containing two footnote references (w:id=\"2\", w:id=\"3\")."""
    body = _paragraph(
        _run("This paragraph has "),
        _footnote_ref("2"),
        _run(" two footnote "),
        _footnote_ref("3"),
        _run("references."),
    )
    footnotes = (
        FOOTNOTES_HEAD
        + _separator_footnote()
        + _continuation_separator_footnote()
        + _content_footnote("2", _paragraph(_run("First multiple footnote.")))
        + _content_footnote("3", _paragraph(_run("Second multiple footnote.")))
        + FOOTNOTES_TAIL
    )
    path = _fixture_path("multiple_notes_one_paragraph.docx")
    build_minimal_docx(path, body, footnotes_xml=footnotes)
    return path


def generate_note_at_paragraph_boundary() -> str:
    """Fixture 6: one paragraph where the footnote reference run is the very first run,
    another paragraph where it's the very last run.
    """
    body = (
        _paragraph(
            _footnote_ref("2"),
            _run("This footnote reference is at the start of the paragraph."),
        )
        + _paragraph(
            _run("This footnote reference is at the end of the paragraph. "),
            _footnote_ref("3"),
        )
    )
    footnotes = (
        FOOTNOTES_HEAD
        + _separator_footnote()
        + _continuation_separator_footnote()
        + _content_footnote("2", _paragraph(_run("Boundary footnote start.")))
        + _content_footnote("3", _paragraph(_run("Boundary footnote end.")))
        + FOOTNOTES_TAIL
    )
    path = _fixture_path("note_at_paragraph_boundary.docx")
    build_minimal_docx(path, body, footnotes_xml=footnotes)
    return path


def generate_orphan_reference() -> str:
    """Fixture 7: document.xml references w:id=\"5\" but footnotes.xml only defines
    w:id=\"-1\", \"0\", \"2\" — 5 is deliberately orphaned.
    """
    body = _paragraph(
        _run("This paragraph references a footnote that does not exist. "),
        _footnote_ref("5"),
    )
    footnotes = (
        FOOTNOTES_HEAD
        + _separator_footnote()
        + _continuation_separator_footnote()
        + _content_footnote("2", _paragraph(_run("Only this footnote exists.")))
        + FOOTNOTES_TAIL
    )
    path = _fixture_path("orphan_reference.docx")
    build_minimal_docx(path, body, footnotes_xml=footnotes)
    return path


def generate_nested_footnote() -> str:
    """Fixture 8: footnotes.xml's footnote body (w:id=\"2\") itself contains a
    ``w:footnoteReference`` to w:id=\"3\".
    """
    body = _paragraph(
        _run("A paragraph with a footnote that has a nested reference. "),
        _footnote_ref("2"),
    )
    # Footnote 2's body contains a reference to footnote 3
    footnote_2_body = _paragraph(
        _run("This footnote "),
        _footnote_ref("3"),
        _run("contains a nested footnote reference."),
    )
    footnotes = (
        FOOTNOTES_HEAD
        + _separator_footnote()
        + _continuation_separator_footnote()
        + _content_footnote("2", footnote_2_body)
        + _content_footnote("3", _paragraph(_run("Nested footnote text.")))
        + FOOTNOTES_TAIL
    )
    path = _fixture_path("nested_footnote.docx")
    build_minimal_docx(path, body, footnotes_xml=footnotes)
    return path


def generate_not_a_docx() -> str:
    """Fixture 9: a plain text file (not a .docx) — for InvalidDocxError tests."""
    path = _fixture_path("not_a_docx.txt")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("this is not a valid docx file")
    return path


def generate_fragmented_runs() -> str:
    """Fixture 10: real-world run fragmentation with whitespace next to footnote.

    Word splits sentences into multiple <w:r> elements. Some runs have
    leading/trailing whitespace. The footnote reference sits between
    fragmented text runs to verify whitespace is preserved around it.
    """
    body = _paragraph(
        # First run: "This " (trailing space)
        _run("This "),
        # Second run: "sentence has " (trailing space)
        _run("sentence has "),
        # Footnote reference
        _footnote_ref("2"),
        # Third run: "fragmented runs " (trailing space)
        _run("fragmented runs "),
        # Fourth run: "and whitespace." (no trailing space)
        _run("and whitespace."),
    )
    footnotes = (
        FOOTNOTES_HEAD
        + _separator_footnote()
        + _continuation_separator_footnote()
        + _content_footnote("2", _paragraph(_run("A footnote in fragmented text.")))
        + FOOTNOTES_TAIL
    )
    path = _fixture_path("fragmented_runs.docx")
    build_minimal_docx(path, body, footnotes_xml=footnotes)
    return path


def generate_hyperlink_wrapped_footnote() -> str:
    """Fixture 11: footnote reference inside a hyperlink-wrapped run.

    Real Word documents can place <w:r> elements inside <w:hyperlink>
    wrappers. The extractor must find runs recursively within paragraphs
    (not just as direct children). The hyperlink itself is cosmetic;
    the key property is that the <w:r> lives under <w:hyperlink> not
    directly under <w:p>.
    """
    body = _paragraph(
        _run("Text before the hyperlinked footnote. "),
        # The w:r containing the footnote ref is inside a w:hyperlink
        f'      <w:hyperlink r:id="rId5">\n'
        f'        <w:r><w:footnoteReference w:id="2"/></w:r>\n'
        f'      </w:hyperlink>\n',
        _run(" Text after."),
    )
    footnotes = (
        FOOTNOTES_HEAD
        + _separator_footnote()
        + _continuation_separator_footnote()
        + _content_footnote("2", _paragraph(_run("Hyperlinked footnote text.")))
        + FOOTNOTES_TAIL
    )
    path = _fixture_path("hyperlink_wrapped_footnote.docx")
    build_minimal_docx(path, body, footnotes_xml=footnotes)
    return path


def generate_tracked_change_footnote() -> str:
    """Fixture 12: footnote reference wrapped inside a tracked change (w:ins).

    Real documents with change tracking can have <w:footnoteReference>
    inside <w:ins> inside <w:r>. The extractor must search for
    references recursively within each run.
    """
    body = _paragraph(
        _run("Text before "),
        # The w:footnoteReference is inside w:ins, inside w:r
        (
            '      <w:r>\n'
            '        <w:ins w:id="1">\n'
            '          <w:footnoteReference w:id="2"/>\n'
            '        </w:ins>\n'
            '      </w:r>\n'
        ),
        _run(" tracked change footnote."),
    )
    footnotes = (
        FOOTNOTES_HEAD
        + _separator_footnote()
        + _continuation_separator_footnote()
        + _content_footnote("2", _paragraph(_run("Tracked change footnote text.")))
        + FOOTNOTES_TAIL
    )
    path = _fixture_path("tracked_change_footnote.docx")
    build_minimal_docx(path, body, footnotes_xml=footnotes)
    return path


def generate_realistic_ru_prose() -> str:
    """Fixture 13: realistic RU prose with an obvious cliché and a footnote.

    Unlike the structural fixtures above (built for extraction testing),
    this one uses natural Russian sentences containing an obvious cliché
    ("играет важную роль") plus a footnote reference, so the full
    extract -> humanize -> reassemble pipeline (SPEC-006 §6's integration
    test) has realistic input to work with.
    """
    body = (
        _paragraph(
            _run("Удалённая работа играет важную роль в современном мире. "),
            _footnote_ref("2"),
            _run(" Многие компании переходят на гибкий график."),
        )
        + _paragraph(
            _run("Это позволяет сотрудникам лучше совмещать работу и личную жизнь.")
        )
        + _paragraph(
            _run("В итоге растёт эффективность труда и качество жизни.")
        )
    )
    footnotes = (
        FOOTNOTES_HEAD
        + _separator_footnote()
        + _continuation_separator_footnote()
        + _content_footnote("2", _paragraph(_run("Исследование 2024 года.")))
        + FOOTNOTES_TAIL
    )
    path = _fixture_path("realistic_ru_prose.docx")
    build_minimal_docx(path, body, footnotes_xml=footnotes)
    return path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Map of fixture name -> generator function
    generators = [
        ("no_notes.docx", generate_no_notes),
        ("footnotes_only.docx", generate_footnotes_only),
        ("endnotes_only.docx", generate_endnotes_only),
        ("mixed_notes.docx", generate_mixed_notes),
        ("multiple_notes_one_paragraph.docx", generate_multiple_notes_one_paragraph),
        ("note_at_paragraph_boundary.docx", generate_note_at_paragraph_boundary),
        ("orphan_reference.docx", generate_orphan_reference),
        ("nested_footnote.docx", generate_nested_footnote),
        ("not_a_docx.txt", generate_not_a_docx),
        ("fragmented_runs.docx", generate_fragmented_runs),
        ("hyperlink_wrapped_footnote.docx", generate_hyperlink_wrapped_footnote),
        ("tracked_change_footnote.docx", generate_tracked_change_footnote),
        ("realistic_ru_prose.docx", generate_realistic_ru_prose),
    ]

    fixture_dir = FIXTURES_DIR
    os.makedirs(fixture_dir, exist_ok=True)

    for name, gen_fn in generators:
        path = gen_fn()
        print(f"Created: {path}")