
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