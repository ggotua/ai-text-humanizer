
## 2026-07-23: w:del-wrapped footnote references not filtered
Decision:   extract_docx_text does not check for w:del ancestry when
            finding footnoteReference nodes — a footnote reference inside
            a tracked deletion will still produce a placeholder token.
Reason:     Real documents in scope (SIDA/TVNAIA drafts) are not expected
            to carry unaccepted tracked changes from other authors.
Risk:       If a .docx with unresolved tracked deletions is fed in, output
            may reference a footnote the visible text no longer contains.
Mitigation if it becomes a problem: check ancestor-or-self::w:del before
            emitting the placeholder.