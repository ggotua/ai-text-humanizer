# SPEC-001: Detector Component (Programmatic Metrics)

Feature:      Programmatic detection of AI-writing markers in plain text
Priority:     P1 (foundation — SPEC-002's prompt feedback loop depends on this)
Status:       Planning
Dependencies: None (pure Python, no Ollama/Langflow required to test)
Related Docs: APP-OVERVIEW.md §2.2, §2.6 (placeholder token contract),
              SPEC-002 (consumes this component's report; also owns
              title-echo detection, which is explicitly NOT part of this spec)

---

## 1. Overview

Computes 5 programmatic metrics on a text draft and returns a structured
report of which rules failed, where, and by how much severity. This report
is the concrete feedback fed into the next LLM rewrite pass (SPEC-002/004).

**Explicitly excluded from this spec:** title-echo detection. Per the
2026-07-23 architecture decision (APP-OVERVIEW §2.2), title-echo is an
LLM-as-judge call, not a deterministic metric — it belongs to SPEC-002.

**Updated 2026-07-23 (blacklist content discussion):** RU blacklist/hedge
matching uses **lemma-based matching** via `natasha`, not literal substring
match. Reason: Russian is highly inflected — a literal entry "играет важную
роль" would miss "играет важной роли" (case), "играют важную роль" (number),
"сыграла важную роль" (tense/aspect). Lemmatizing both the blacklist entries
and the input text before matching catches all inflected forms from one
human-readable entry. EN blacklist/hedge matching stays literal substring —
English morphology doesn't need this (e.g. "crucial" doesn't inflect).

Config files remain human-readable, natural-phrasing text (e.g.
`играет важную роль` as written) — lemmatization happens automatically at
`load_config` time, not by hand-authoring pre-lemmatized entries.

**Placeholder-token awareness is mandatory throughout:** the input text may
contain `[[FN:n]]` / `[[EN:n]]` tokens (per SPEC-003's extraction contract).
Every metric in this spec must treat these tokens as opaque — never counted
as a "word" for lexical diversity, never matched against blacklists, never
split mid-token by sentence segmentation, never flagged as a cliché.

**Acceptance criteria:**
- All 5 metrics compute correctly on RU and EN text
- Placeholder tokens never distort any metric's output
- Report structure is stable and documented enough for SPEC-002 to consume
  without guessing field names
- No metric requires Ollama, Langflow, or any network call

---

## 2. The 5 Metrics

1. **Cliché/buzzword blacklist match** — configurable phrase list (RU + EN
   separate files), case-insensitive substring/regex match
2. **Hedge/weak-attribution phrase match** — same mechanism as #1, separate
   config file and separate report category (different severity weighting)
3. **Meta-commentary / template-conclusion detection** — pattern-based
   check for boilerplate openings ("давайте разберём", "let's break down")
   and closings ("в заключение", "in conclusion") — checked only at
   document start/end, not mid-text
4. **Sentence-length rhythm/burstiness** — mean and standard deviation of
   sentence length (in words), using `razdel.sentenize` for segmentation
5. **N-gram repetition / lexical diversity** — combines:
   a. literal "X, Y and Z" / "X, Y и Z" triple-parallelism regex match
   b. distinct-2 / distinct-3 score using `natasha`-lemmatized tokens

---

## 3. Interface Definition

Functional style — pure functions, explicit inputs/outputs, no classes
except plain data containers (frozen dataclasses).

```python
from dataclasses import dataclass, field
from enum import Enum

class Severity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

@dataclass(frozen=True)
class RuleMatch:
    rule_name: str          # e.g. "cliche_blacklist", "hedge_phrase"
    matched_text: str        # the exact phrase found
    position: int            # character offset in the input text
    severity: Severity

@dataclass(frozen=True)
class RhythmStats:
    sentence_count: int
    mean_length_words: float
    stdev_length_words: float
    lengths: list[int]        # word count per sentence, in order
    monotony_flag: bool        # True if stdev is below the configured threshold

@dataclass(frozen=True)
class DiversityStats:
    distinct_2: float          # unique bigrams / total bigrams
    distinct_3: float
    parallelism_matches: list[RuleMatch]  # literal "X, Y and Z" hits

@dataclass(frozen=True)
class DetectorReport:
    cliche_matches: list[RuleMatch]
    hedge_matches: list[RuleMatch]
    meta_commentary_matches: list[RuleMatch]
    rhythm: RhythmStats
    diversity: DiversityStats
    passed: bool                # True if all rules are within configured thresholds
    failed_rules: list[str]     # names of rules that failed threshold, empty if passed

@dataclass(frozen=True)
class DetectorConfig:
    language: str                          # "ru" or "en" — determines matching strategy
    cliche_blacklist_lemmas: tuple[tuple[str, ...], ...]  # RU only: each entry is a lemma sequence, e.g. ("играть", "важный", "роль")
    hedge_blacklist_lemmas: tuple[tuple[str, ...], ...]   # RU only, same format
    cliche_blacklist_literal: frozenset[str]  # EN only: literal phrases, case-insensitive
    hedge_blacklist_literal: frozenset[str]   # EN only
    meta_opening_patterns: tuple[str, ...]   # regex patterns
    meta_closing_patterns: tuple[str, ...]
    max_cliche_matches: int
    max_hedge_matches: int
    min_stdev_threshold: float    # below this, rhythm is flagged as monotonous
    min_distinct_2: float
    min_distinct_3: float
    max_parallelism_matches: int

PLACEHOLDER_TOKEN_PATTERN = r"\[\[(?:FN|EN):[^\]]+\]\]"  # matches [[FN:3]], [[EN:12]], etc.

def load_config(config_dir: str, language: str) -> DetectorConfig:
    """
    Loads blacklists and thresholds for the given language ("ru" or "en")
    from config_dir. Config files are plain human-readable phrases, one
    per line (e.g. "играет важную роль"). For language="ru", each line is
    lemmatized via natasha at load time into a lemma-tuple and stored in
    cliche_blacklist_lemmas/hedge_blacklist_lemmas — literal fields are left
    empty. For language="en", lines are stored as-is (lowercased) in the
    literal fields — lemma fields are left empty. Raises FileNotFoundError
    with a clear message if a required config file is missing — does not
    silently fall back to an empty blacklist.
    """

def strip_placeholder_tokens(text: str) -> tuple[str, list[tuple[int, str]]]:
    """
    Returns (text_with_tokens_removed, removed_token_positions) where
    removed_token_positions is a list of (original_position, token_string)
    for every [[FN:n]]/[[EN:n]] token found. Used internally by metrics
    that must not let placeholder tokens distort word counts or matches.
    Does NOT mutate positions of surrounding text in a way that breaks
    RuleMatch.position reporting — see section 4 for how this is reconciled.
    """

def detect_blacklist_phrases_ru(text: str, blacklist_lemmas: tuple[tuple[str, ...], ...], rule_name: str, severity: Severity) -> list[RuleMatch]:
    """
    Lemma-based matching for Russian. Tokenizes and lemmatizes `text` via
    natasha into an ordered sequence of (surface_form, lemma, char_start,
    char_end). For each blacklist entry (a lemma tuple), slides a window
    of matching length over the text's lemma sequence looking for an exact
    lemma-sequence match. On match, RuleMatch.matched_text is the ORIGINAL
    surface form found in the text (not the lemma), and RuleMatch.position
    is the char_start of the first matched token — so the report is
    human-readable, not a bag of lemmas. Placeholder tokens are excluded
    from the lemma sequence before matching (see section 4).
    """

def detect_blacklist_phrases_en(text: str, blacklist_literal: frozenset[str], rule_name: str, severity: Severity) -> list[RuleMatch]:
    """Case-insensitive literal substring match for English — no lemmatization needed. Skips matches inside placeholder tokens (impossible by construction, but tested anyway)."""

def detect_meta_commentary(text: str, opening_patterns: tuple[str, ...], closing_patterns: tuple[str, ...]) -> list[RuleMatch]:
    """Checks only the first and last paragraph of text against the given regex patterns."""

def compute_rhythm_stats(text: str, min_stdev_threshold: float) -> RhythmStats:
    """Segments text into sentences via razdel.sentenize, computes word-count stats per sentence. Placeholder tokens count as zero extra words (see section 4)."""

def compute_diversity_stats(text: str, min_distinct_2: float, min_distinct_3: float, max_parallelism_matches: int) -> DiversityStats:
    """Lemmatizes tokens via natasha, computes distinct-2/distinct-3, and regex-matches literal triple-parallelism patterns."""

def build_detector_report(text: str, config: DetectorConfig) -> DetectorReport:
    """Runs all 5 metrics and assembles the final report, including passed/failed_rules based on config thresholds."""
```

---

## 4a. Lemma-Based Matching Algorithm (RU blacklist/hedges)

1. Run `natasha`'s segmenter + morph tagger over the input text (with
   placeholder tokens still present in the raw text at this stage).
2. Build an ordered list of `(surface_form, lemma, char_start, char_end)`
   for every real word token. Placeholder tokens (`[[FN:n]]`/`[[EN:n]]`)
   are identified via `PLACEHOLDER_TOKEN_PATTERN` and **excluded** from
   this list entirely — they contribute no lemma and don't break a
   multi-word blacklist match spanning around them, since they simply
   aren't part of the sequence being matched against.
3. For each blacklist entry (a tuple of N lemmas), slide a window of size
   N over the token list's lemma sequence. On an exact match at position
   `i..i+N`, construct a `RuleMatch` with:
   - `matched_text` = the original surface forms from `char_start` of
     token `i` to `char_end` of token `i+N-1` (so the report shows what
     was actually written, e.g. "играют важную роль", not the lemma form)
   - `position` = `char_start` of token `i`
4. A single blacklist entry may match multiple times in one document —
   return all matches, not just the first.

**Known limitation, acceptable for MVP:** word order must match exactly.
"важную играет роль" (unnatural but grammatically loose word order) would
NOT match an entry lemmatized as `("играть", "важный", "роль")`. This is a
simplification — full dependency-parse-based matching (order-independent)
is deferred to v2 if it turns out to matter in practice.

---

## 4b. Placeholder Token Handling — The Critical Detail

This is the part most likely to be implemented sloppily, so it gets its own
section rather than being buried in each function's docstring.

- **Blacklist/hedge/meta-commentary matching:** placeholder tokens contain
  no natural-language words, so literal substring matching against them is
  harmless by construction — no special-casing needed, but a test must
  confirm a blacklist phrase is never accidentally matched against text
  *inside* a token (impossible given the token format, but verify explicitly
  rather than assuming).
- **Sentence segmentation (`razdel`):** a placeholder token like `[[FN:3]]`
  must not be treated as sentence-ending punctuation or split mid-token.
  Test against a sentence containing a token in the middle, at the start,
  and at the end.
- **Word-count for rhythm stats:** a placeholder token must NOT be counted
  as a word. If `razdel.tokenize` naively counts `[[FN:3]]` as one or more
  tokens, the sentence's word count will be inflated. Strip tokens (via
  `strip_placeholder_tokens`) before counting words, but segment sentences
  on the *original* text (with tokens still present) — token removal for
  counting purposes must not shift sentence boundaries.
- **Lexical diversity / n-grams:** placeholder tokens must be excluded from
  the token stream entirely before computing bigrams/trigrams — otherwise
  `[[FN:3]]` and `[[FN:7]]` would count as two "distinct" tokens that have
  nothing to do with actual vocabulary diversity, artificially inflating
  the distinct-2/distinct-3 score.

---

## 5. Edge Cases & Constraints

| Case | Behavior |
|---|---|
| Empty string input | Return a report with all-zero counts, `rhythm.sentence_count == 0`, `passed == True` (nothing to fail on) — not an exception |
| Text that is only placeholder tokens, no real words | `diversity` stats should reflect zero real vocabulary (not divide-by-zero — return 0.0, not NaN or a crash) |
| Text in a language other than the configured one (e.g. English text run against RU config) | Not auto-detected in MVP — the caller is responsible for passing the correct `language` to `load_config`. Document this as an MVP limitation, not a silent failure mode |
| Blacklist file has duplicate entries | Deduplicated on load (config uses `frozenset`/lemma-tuple dedup) |
| RU blacklist phrase appears in an inflected form (different case/number/tense than the config file's literal wording) | Must still match, via lemma comparison — this is the entire point of 4a; test explicitly, don't assume |
| RU blacklist entry's word order doesn't match text's word order | Does NOT match (documented limitation, section 4a) |
| Sentence with zero words after placeholder stripping (e.g. a sentence that was only a footnote reference) | Counted as a 0-length sentence in rhythm stats, not excluded — but flag this in a warning-equivalent field if it happens more than once, since it likely indicates a segmentation problem upstream |
| Regex patterns in config that are invalid regex | `load_config` raises a clear `re.error`-wrapping exception at load time, not at first use — fail fast |

---

## 6. Error Handling Requirements

- `load_config` never returns a partially-loaded config — if any required
  file is missing or malformed, raise before returning anything.
- `build_detector_report` never raises on malformed *input text* — text is
  user data, not a code-level precondition; it should degrade gracefully
  (see edge cases table) rather than crash the pipeline mid-iteration.
- Any internal exception from `razdel`/`natasha` (e.g. on unexpected
  Unicode edge cases) must be caught and surfaced as a `DetectorError`
  (subclass of `RuntimeError`) with the offending text snippet included,
  not a raw library traceback.

---

## 7. Testing Requirements

Config fixtures needed: `tests/fixtures/config/cliches_ru.txt`,
`hedges_ru.txt`, `cliches_en.txt`, `hedges_en.txt` — small hand-picked
lists (5–10 entries each) for deterministic testing, separate from
whatever production blacklist ends up being hundreds of entries long.

Required unit tests:
```
test_empty_text_returns_zero_report_not_exception()
test_placeholder_only_text_returns_zero_diversity_not_nan()
test_cliche_blacklist_match_ru_literal_form()
test_cliche_blacklist_match_ru_inflected_form()
test_cliche_blacklist_no_match_when_word_order_differs()
test_cliche_blacklist_match_en()
test_hedge_blacklist_match_ru_inflected_form()
test_hedge_blacklist_match_en()
test_meta_commentary_detected_only_at_start_and_end()
test_meta_commentary_not_flagged_mid_text()
test_rhythm_stats_correct_sentence_count()
test_rhythm_stats_placeholder_token_not_counted_as_word()
test_sentence_segmentation_handles_token_at_start_middle_end()
test_diversity_stats_excludes_placeholder_tokens_from_ngrams()
test_diversity_stats_lemmatizes_before_counting_distinct()
test_triple_parallelism_pattern_detected_ru()
test_triple_parallelism_pattern_detected_en()
test_load_config_missing_file_raises_clear_error()
test_load_config_invalid_regex_raises_at_load_time_not_later()
test_load_config_ru_lemmatizes_blacklist_entries_at_load_time()
test_build_detector_report_passed_true_when_all_thresholds_met()
test_build_detector_report_failed_rules_lists_correct_names()
```

---

## 8. What SPEC-002 Depends On From This Spec

- The exact `DetectorReport` structure — SPEC-002's prompt template needs
  to turn `failed_rules`, `cliche_matches`, etc. into concrete rewrite
  instructions (per the data-analysis-SDD principle: specific feedback,
  not generic "be more human" prompting)
- The placeholder token pattern constant (`PLACEHOLDER_TOKEN_PATTERN`) —
  SPEC-002's own title-echo LLM-judge prompt must also be told not to
  treat these tokens as content when judging a subheading/paragraph pair
