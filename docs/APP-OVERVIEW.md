# AI-Text Humanizer (Langflow + Local Ollama) — Application Overview

**Version:** 1.0
**Date:** 2026-07-23
**Status:** Planning

---

## 1. Executive Summary

A local, privacy-preserving text-humanization pipeline built in Langflow, running
on Ollama with Mistral 7B. Users upload a `.txt` or `.docx` file containing
AI-generated or stiff/formulaic text. The pipeline iteratively detects and
corrects stylistic AI-markers (clichés, hedges, monotonous rhythm, N-gram
repetition, title-echo, robotic paragraph structure) until the text passes a
programmatic quality threshold, then returns the final humanized text.

**Target Users:** George himself (own drafts: SIDA reports, TVNAIA/media
analysis writeups, articles) and, secondarily, freelance clients who need
editorial polish on AI-assisted drafts.

**Platform:** Local desktop app (Langflow UI in browser, all processing local)
**Tech Stack:** Langflow, Ollama (Mistral 7B), Python (detector components),
Windows/local filesystem

**Explicitly out of scope:** anything designed to defeat authorship/provenance
verification (edit-history spoofing, keystroke simulation, watermark evasion
intended to misattribute authorship). This tool improves writing style only;
it does not fabricate a false authorship trail. See Section 3.

---

## 2. Core Features (MVP)

### 2.1 Input
- Upload `.txt` file
- Upload `.docx` file — body text extracted for processing; footnote/endnote
  **structure is preserved** (see 2.6). Formatting (fonts, styles) is not
  preserved in MVP — only footnote/endnote anchoring is a hard requirement.

### 2.6 Footnote/Endnote Preservation (added 2026-07-23)

**Reason:** Input documents (SIDA/TVNAIA reports, articles) routinely carry
footnotes/endnotes with citations. The humanizer must not detach a footnote
from its anchor point or drop/duplicate any note.

**Scope decision:** Footnote/endnote **text content is NOT rewritten** —
only body text goes through the detector/rewrite loop. Footnotes are treated
as opaque payloads that get re-attached to the correct anchor position in the
output document.

**Approach:**
- On load: parse the `.docx` OOXML structure directly (not a plain-text
  extraction). Identify each `w:footnoteReference` / `w:endnoteReference` in
  the body, record its position (paragraph/run index) and its relationship
  ID linking to the corresponding entry in `footnotes.xml` / `endnotes.xml`.
- Replace each reference in the text sent to the LLM with a stable inline
  placeholder token (e.g. `[[FN:3]]`), so the rewrite pass sees the anchor
  point as an immovable marker rather than losing it.
- Instruct the rewrite prompt explicitly: never delete, duplicate, or reorder
  `[[FN:n]]` tokens; they may move within a sentence during rewrite but must
  all still be present exactly once each afterward.
- On output: reassemble the `.docx` — rewritten body text with placeholders
  replaced back with real footnote/endnote references, footnotes.xml/endnotes.xml
  copied through unmodified.

**Out of scope for MVP:** footnote *text* humanization, renumbering logic
beyond what Word does automatically, nested footnotes-in-footnotes edge cases.

### 2.2 Detector Layer

**Updated 2026-07-23** after reviewing which categories genuinely need a
library vs. plain regex/stdlib. Five of six categories are pure Python
(no LLM call); title-echo detection is the one exception — deferred to
an LLM-as-judge call, not a deterministic metric.

**Pure Python (no LLM), using stdlib + `razdel`/`natasha` for sentence
segmentation and lemmatization** (chosen over hand-rolled regex splitting —
sentence boundary detection on abbreviations, decimals, and quotes is a
solved problem, not worth re-solving):
- Cliché/buzzword blacklist match count (configurable list, regex/string match)
- Hedge/weak-attribution phrase count ("эксперты считают", "потенциально", etc.)
- Meta-commentary / template-conclusion detection (opening/closing boilerplate,
  pattern-based)
- Sentence-length distribution (mean, std dev) → rhythm/burstiness score
  (requires correct sentence segmentation — `razdel` handles Russian
  abbreviations/decimals/quotes that naive `re.split` would mishandle)
- N-gram repetition / parallelism (literal "X, Y and Z" pattern via regex
  for MVP; deeper syntactic parallelism via `natasha` POS-tagging deferred
  to v2 — explicitly a simplification, not full syntactic analysis)
- Lexical diversity (distinct-2 / distinct-3), using lemmatized tokens
  from `natasha` so inflected forms of the same word aren't counted as
  distinct vocabulary

**LLM-as-judge (via Ollama/Mistral call, not a Python metric):**
- Title-echo detection: a subheading and the sentence(s) immediately
  following it are passed to Mistral with a direct question — "does this
  restate the heading rather than add data/example/narrative?" Chosen over
  lexical-overlap (misses paraphrase) or sentence-embeddings (adds a
  second heavy ML dependency) because Mistral is already loaded locally —
  simpler architecture, at the cost of one extra LLM call per subheading.

Output: a structured report combining both — programmatic metrics from the
5 stdlib-based checks, plus the LLM's title-echo judgments — fed back into
the next rewrite pass as concrete feedback.

### 2.3 Rewrite Layer (Ollama / Mistral 7B)
Two-stage temperature approach:
1. **Stage 1 (temp ≈ 0.3):** grammar, logical flow, factual consistency pass.
2. **Stage 2 (temp ≈ 0.7–0.85, tuned empirically for Russian):** stylistic
   rewrite pass using the detector's report as explicit, concrete instructions
   (not generic "be more human" prompting).

### 2.4 Iteration Loop
- Detector → Rewrite → Detector → ... until metrics pass configured thresholds
  OR a fixed max-iteration limit is reached (`for`-loop with hard cap, never
  unbounded — per George's existing SDD convention on constrained generation).
- If max iterations reached without passing: return best-scoring version with
  a warning flag, not a silent failure.

### 2.5 Output
- Final humanized text (plain text / downloadable `.txt`)
- Iteration count and final detector report shown to user (not a full diff view
  in MVP — deferred to v2)

---

## 3. System Architecture

```
Browser (Langflow UI)
      ↓
Langflow Flow (visual pipeline)
   ├── File Loader (.txt / .docx → plain text)
   ├── Detector Component (Python, custom)
   ├── Prompt Template Component (blacklist + rules + detector feedback)
   ├── Ollama Component (Mistral 7B, temp param)
   └── Loop Controller (max N iterations, threshold check)
      ↓
Output (final text + report)
```

All processing local — no data leaves the machine. This matters given the
SIDA/TVNAIA content may include sensitive or embargoed material.

---

## 4. Data Model (High-Level)

No persistent database in MVP. State lives within a single Langflow run:

- `raw_text`: string (from upload)
- `current_draft`: string (mutates each iteration)
- `detector_report`: structured object (list of {rule, severity, location, suggestion})
- `iteration_count`: int
- `thresholds`: config object (per-rule pass/fail cutoffs)

If persistence is wanted later (v2): SQLite table logging each run's iteration
history for review/tuning of thresholds over time.

---

## 5. User Workflows

### First-Time Use
1. Open Langflow flow in browser
2. Upload `.txt` or `.docx` draft
3. Run flow
4. Review final text + report (how many iterations, what was fixed)

### Regular Usage (George's own drafts)
1. Drop in SIDA/TVNAIA/article draft
2. Run
3. Spot-check output, manually adjust anything the detector missed
4. Use final text in downstream deliverable

### Freelance/Client Usage (later)
1. Same flow, potentially with client-specific blacklist config
2. Export final text for delivery

---

## 6. Technical Requirements

Performance:
- Single-document runs (not batch) in MVP
- Reasonable iteration time on local hardware (Mistral 7B via Ollama) —
  exact latency budget TBD once first version is running (depends on
  George's GPU/CPU)
- Title-echo LLM-as-judge adds one Mistral call per subheading per
  iteration — for documents with many subheadings this could meaningfully
  add to total run time; worth measuring once SPEC-001 is running against
  a real draft, and batching all subheading checks into a single call if
  it turns out to be slow

Language:
- Primary: Russian
- Secondary: English (blacklist rules should support both from the start,
  since detector logic is language-agnostic where possible — sentence-length
  stats, N-gram detection — and language-specific where necessary — hedge
  phrase lists, cliché lists)

Dependencies (added 2026-07-23):
- `razdel` — sentence/token segmentation (Russian-focused; correctly
  handles abbreviations, decimals, quotes that naive regex splitting
  would mishandle)
- `natasha` — lemmatization for lexical-diversity counting; POS-tagging
  reserved for v2 syntactic-parallelism detection, not used in MVP

Constraints:
- Fully local — no cloud API calls
- No watermark-evasion or authorship-spoofing logic (see Section 3)

---

## 7. Development Phases

**Reordered 2026-07-23** after adding footnote/endnote preservation (2.6).
Rationale: OOXML/footnote handling is the highest-complexity, highest-risk
component. If the `[[FN:n]]` placeholder contract were designed late, a
mistake there would force rework of the detector and prompt templates that
were built assuming plain text. So the token contract is fixed early and the
risky OOXML component is built and validated as an isolated track, in
parallel with the core `.txt` pipeline — not bolted on at the end.

**Phase 1 — Foundation (SPEC-001, SPEC-002):**
- Detector component — pure Python, works on plain text, must treat
  `[[FN:n]]` placeholder tokens as opaque/untouchable (skip them in all
  metrics, never flag or strip them)
- Prompt templates & blacklist config (RU+EN) — includes explicit rule:
  never delete/duplicate/reorder-losing `[[FN:n]]` tokens

**Phase 2 — OOXML Track (SPEC-003, isolated, can start in parallel with Phase 1):**
- `.docx` → plain-text-with-placeholders extraction
- Footnote/endnote reference mapping (paragraph/run position + relationship ID)
- This component is tested standalone against sample `.docx` files —
  independent of whether the LLM pipeline exists yet

**Phase 3 — Core Pipeline on `.txt` (SPEC-004, SPEC-005):**
- Langflow flow: file loader → prompt template → Ollama component,
  proven end-to-end on `.txt` input first (no footnote complexity yet)
- Iteration loop with threshold checks and max-iteration cap

**Phase 4 — `.docx` Integration (SPEC-006, depends on Phase 2 + Phase 3):**
- Wire the OOXML track into the proven `.txt` pipeline
- Output reassembly: rewritten body + placeholders swapped back for real
  footnote/endnote references, footnotes.xml/endnotes.xml passed through
  unmodified
- Automated QA check: footnote/endnote count and anchor position match
  between input and output

**Phase 5 — Polish:**
- Report/output formatting
- Threshold tuning on real SIDA/TVNAIA drafts

---

## 8. Future Versions

**Version 2.0:**
- Diff-view (before/after, rule-by-rule)
- Persistent run history (SQLite) for threshold tuning over time
- Client-specific config profiles (different blacklists per client/project)
- Support for Vikhr/Saiga-Mistral as an alternate model, selectable in UI

**Version 3.0:**
- Batch processing (multiple documents)
- Deploy to a small local web UI beyond Langflow's own interface, if needed
  for client-facing use

---

## 9. Success Criteria

MVP is complete when:
- User can upload `.txt` or `.docx` and get back a rewritten text
- Detector catches all 6 target marker categories (clichés, hedges,
  meta-commentary, title-echo, rhythm monotony, N-gram repetition)
- Iteration loop terminates correctly (either passes thresholds or hits
  max-iteration cap gracefully — no infinite loop)
- On a real SIDA/TVNAIA draft, output is noticeably less "AI-flavored" by
  George's own read, without losing factual accuracy
- For `.docx` input containing footnotes/endnotes: automated check confirms
  footnote/endnote **count matches** between input and output, and each
  anchor lands at a position consistent with the original (not dropped,
  duplicated, or detached into the wrong sentence)

---

## 10. Related Documents

- SPEC-001: Detector Component (metrics + rule engine, placeholder-token-aware) — Low/Medium
- SPEC-002: Prompt Templates & Blacklist Config (RU + EN, token-preservation rule) — Low
- SPEC-003: OOXML Parsing — Footnote/Endnote Placeholder Extraction (`.docx` → text-with-placeholders) — **High**
- SPEC-004: Langflow Flow Assembly (file loader, Ollama component, wiring — `.txt` path) — Medium
- SPEC-005: Iteration Loop & Threshold Controller — Medium
- SPEC-006: `.docx` Reassembly & Footnote QA Check (integrates SPEC-003 into the proven pipeline; automated count/position check) — **High**

Each spec will have a corresponding PROMPT-NNN.md for Cursor (@workspace,
SDD-V2 convention).
