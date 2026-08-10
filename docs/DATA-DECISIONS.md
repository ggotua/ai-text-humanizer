
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
## 2026-08-05: PYTHONPATH required for Langflow custom component to find src/
Decision:   Langflow must be launched with $env:PYTHONPATH set to the
            project root (e.g. "D:\ai engineering\text humanizer")
            before running `uv run langflow run --components-path ...`
Reason:     A stray `src` namespace package exists in
            venv\Lib\site-packages, which Python's import system merges
            with the project's own src/ directory as a namespace package.
            Without PYTHONPATH pointing at the project root explicitly,
            Langflow's custom component loader (running via uv's entry
            point, not the terminal's cwd) cannot find src.pipeline,
            src.detector, etc.
Risk:       Anyone launching Langflow without setting PYTHONPATH first
            will see "ModuleNotFoundError: No module named src.pipeline"
            when the custom component tries to load.
Mitigation: Document the required launch sequence; consider investigating
            and removing the stray src/ package in site-packages later
            (not urgent — PYTHONPATH workaround is stable).
## 2026-08-XX: Mistral translated RU document to EN during humanization
Decision:   Added explicit anti-translation instruction to prompts
            (SPEC-002) plus automated language-integrity check in the
            iteration loop (SPEC-005b), mirroring the existing
            placeholder-token integrity pattern.
Reason:     Found via manual Word inspection of SPEC-006's round-trip
            output — the ENTIRE document was translated to English
            despite language="ru" passed throughout. No automated test
            caught this because none checked output language, only
            structure/tokens. Confirms the value of the mandatory
            human-eyeball check in SPEC-006's PROMPT — this is exactly
            the kind of failure automated tests miss.
Risk:       Heuristic script-detection check (Cyrillic vs Latin ratio)
            is coarse — won't catch subtler issues like code-switching
            mid-sentence, only full document-level drift.
## 2026-08-10: Discovered second citation format not covered by SPEC-003/006
Decision:   Real SIDA/TVNAIA drafts use TWO different citation mechanisms
            depending on the document: (1) native Word footnotes
            (w:footnoteReference — what SPEC-003/005/006 currently
            protect), and (2) manually-typed Unicode superscript digit
            characters (¹²³...) inline in body text, with a consolidated
            bibliography at the document's end, NO footnotes.xml/
            endnotes.xml present at all.
Reason:     Found via real-document testing (Belarus_Chapter_Draft.docx,
            5000 words, 117 superscript citation markers, zero native
            footnoteReference elements). This is not an edge case for
            this document — it is the ONLY citation mechanism present,
            at a density of roughly 1 marker per 320 words.
Risk:       The entire placeholder-token integrity architecture
            (SPEC-003 extraction, SPEC-005 multiset checks, SPEC-006
            reassembly) provides ZERO protection for superscript-digit
            citations — they are currently treated as ordinary text
            characters, with no guarantee against loss/duplication/
            reordering during LLM rewriting.
Next step:  SPEC-007 needed — extend the placeholder-token system to
            also detect and protect Unicode superscript-digit citation
            markers (⁰¹²³⁴⁵⁶⁷⁸⁹), parallel to but independent from
            SPEC-003's OOXML footnote extraction. Both citation formats
            must be supported since real documents use either one
            depending on the source.