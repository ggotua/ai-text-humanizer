# PROMPT-001: Detector Component — Implementation Prompts (Cline / DeepSeek)

Reference: docs/specs/SPEC-001-detector-component.md (read this first, in full —
especially sections 4a and 4b on placeholder-token and lemma-matching handling,
they are the parts most likely to be implemented sloppily).

Execute these 7 prompts in order. Run tests after each step before moving to
the next. This component feeds every downstream prompt (SPEC-002), so a
sloppy metric here propagates silently into the rewrite loop.

---

## STEP 1: Models & Config Loader

```
@workspace
Reference: docs/specs/SPEC-001-detector-component.md, sections 3 and 4a/4b
(Interface Definition, Placeholder Token Handling, Lemma-Based Matching).

IMPLEMENTER RULES — follow without exception:
1. Return the COMPLETE file — no "# ... rest unchanged" or partial output.
2. Functional style: pure functions, explicit inputs/outputs. Dataclasses
   are the only exception (plain data containers, no methods beyond
   __post_init__ validation if truly needed).
3. For any constrained iteration: use `for` loops with a fixed limit,
   never `while`.
4. Do not assume imports or variables not shown in this prompt.
5. Implement only what is asked. No extra features.

Create file: src/detector/models.py

Requirements:
- Implement exactly the enum and dataclasses from SPEC-001 section 3:
  Severity, RuleMatch, RhythmStats, DiversityStats, DetectorReport,
  DetectorConfig — using the field names and types shown there exactly,
  including the RU/EN split fields on DetectorConfig
  (cliche_blacklist_lemmas, cliche_blacklist_literal, etc.)
- Define PLACEHOLDER_TOKEN_PATTERN as shown in section 3.
- Define DetectorError as a subclass of RuntimeError, per section 6 —
  constructor takes a message and an optional `offending_text: str = ""`
  keyword argument, included in the exception's string representation.
- Add type hints and docstrings on every class, explaining purpose in
  1-2 sentences. The DetectorConfig docstring must explain WHY it has
  both _lemmas and _literal fields (RU vs EN matching strategy differ —
  reference SPEC-001's rationale, don't just restate the field names).

Create file: src/detector/config_loader.py

Requirements:
- Implement `load_config(config_dir: str, language: str) -> DetectorConfig`
  exactly per SPEC-001 section 3's docstring:
  - language="ru": read config_dir/cliches_ru.txt and config_dir/hedges_ru.txt,
    one phrase per line, strip blank lines. Lemmatize each line via natasha
    into a tuple of lemmas (split on whitespace, lemmatize each word token),
    store in cliche_blacklist_lemmas / hedge_blacklist_lemmas. Literal
    fields are empty frozensets.
  - language="en": read config_dir/cliches_en.txt and config_dir/hedges_en.txt,
    one phrase per line, lowercase and store as literal frozensets. Lemma
    fields are empty tuples.
  - Raise FileNotFoundError with a message naming the exact missing file
    path if a required file doesn't exist — do not fall back to an empty
    blacklist silently.
  - meta_opening_patterns / meta_closing_patterns, and the numeric
    thresholds (max_cliche_matches, max_hedge_matches, min_stdev_threshold,
    min_distinct_2, min_distinct_3, max_parallelism_matches) are read from
    a single config_dir/thresholds.json file (you decide reasonable
    starting default values and document them in a comment — this is
    tunable later, not load-bearing correctness right now).
  - If any regex in meta_opening_patterns/meta_closing_patterns fails to
    compile, raise a clear error at load time (wrap the re.error with
    context saying which pattern failed), not later when first used.
- Implement `strip_placeholder_tokens(text: str) -> tuple[str, list[tuple[int, str]]]`
  exactly per SPEC-001 section 3's docstring — use PLACEHOLDER_TOKEN_PATTERN
  from models.py, do not redefine the regex here.

### Acceptance Check
Run:
`python -c "from src.detector.models import DetectorConfig, Severity; print('ok')"`
Confirm no import errors. Manual load test comes in Step 7 once fixture
config files exist.
```

---

## STEP 2: Blacklist/Hedge Matching (RU lemma-based + EN literal)

```
@workspace
Reference: docs/specs/SPEC-001-detector-component.md, section 3
(function signatures for detect_blacklist_phrases_ru and
detect_blacklist_phrases_en) and section 4a (Lemma-Based Matching
Algorithm — read this closely, it specifies the exact sliding-window
approach and what RuleMatch.matched_text/position must contain).
Also read src/detector/models.py from Step 1 — import from there,
do not redefine RuleMatch/Severity.

IMPLEMENTER RULES — same as Step 1.

Create file: src/detector/blacklist.py

Requirements:
- Implement `detect_blacklist_phrases_ru(text, blacklist_lemmas, rule_name, severity) -> list[RuleMatch]`
  following SPEC-001 section 4a exactly:
  1. Use natasha to segment and lemmatize `text`, producing an ordered
     list of (surface_form, lemma, char_start, char_end) for real word
     tokens only.
  2. Exclude placeholder tokens (identified via PLACEHOLDER_TOKEN_PATTERN
     from models.py) from this list entirely before matching.
  3. For each entry in blacklist_lemmas (a tuple of N lemmas), slide a
     window of size N over the lemma sequence. On exact match, build a
     RuleMatch with matched_text = original surface forms spanning the
     match (char_start of first token to char_end of last token) and
     position = char_start of the first matched token.
  4. Return ALL matches found, not just the first per entry.
- Implement `detect_blacklist_phrases_en(text, blacklist_literal, rule_name, severity) -> list[RuleMatch]`:
  case-insensitive literal substring search, one RuleMatch per occurrence,
  matched_text = the original-case substring found, position = its char
  offset.
- Neither function should match text inside a placeholder token — for
  the EN function this is naturally true given the token format contains
  no blacklist-like words, but include the check anyway per SPEC-001's
  instruction to verify explicitly rather than assume.

### Acceptance Check
Manual smoke test (once config exists from Step 1's fixtures, or inline
with a hardcoded example): confirm that lemmatized text containing
"играют важную роль" matches a blacklist entry lemmatized from
"играет важную роль" — this is the core value of the lemma approach,
verify it actually works before moving on.
```

---

## STEP 3: Meta-Commentary Detection

```
@workspace
Reference: docs/specs/SPEC-001-detector-component.md, section 2 (item 3)
and section 3 (detect_meta_commentary signature).

IMPLEMENTER RULES — same as Step 1.

Create file: src/detector/meta_commentary.py

Requirements:
- Implement `detect_meta_commentary(text, opening_patterns, closing_patterns) -> list[RuleMatch]`
- Split text into paragraphs (split on \n\n, consistent with SPEC-003's
  paragraph-boundary convention).
- Check ONLY the first paragraph against opening_patterns (regex match,
  case-insensitive) and ONLY the last paragraph against closing_patterns.
  Do not check any other paragraph — mid-text matches of these patterns
  are not meta-commentary and must not be flagged (per SPEC-001 section 7
  test_meta_commentary_not_flagged_mid_text).
- rule_name should be "meta_commentary_opening" or "meta_commentary_closing"
  depending on which matched, so SPEC-002 can distinguish them in feedback.
- severity: Severity.MEDIUM for both (document this choice in a comment;
  it's a starting assumption, tunable later).

### Acceptance Check
Test with a 3-paragraph text where paragraph 2 (middle) contains the
literal string "давайте разберём" — confirm it is NOT flagged, since only
paragraph 1 is checked for openings.
```

---

## STEP 4: Rhythm Stats

```
@workspace
Reference: docs/specs/SPEC-001-detector-component.md, section 2 (item 4),
section 3 (compute_rhythm_stats signature), and section 4b (placeholder
token handling — READ THIS CAREFULLY, this is the step most likely to
have a subtle bug per the spec's own warning).

IMPLEMENTER RULES — same as Step 1.

Create file: src/detector/rhythm.py

Requirements:
- Implement `compute_rhythm_stats(text, min_stdev_threshold) -> RhythmStats`
- Use `razdel.sentenize(text)` to segment into sentences — segmentation
  happens on the ORIGINAL text, with placeholder tokens still present
  (per section 4b: token removal must not shift sentence boundaries).
- For each sentence, compute word count using `razdel.tokenize`, but
  FIRST strip placeholder tokens from that sentence's text (using
  strip_placeholder_tokens from config_loader.py) before tokenizing —
  a placeholder token must never be counted as a word.
- Compute mean and population standard deviation of the per-sentence
  word counts (use Python's `statistics` module — statistics.pstdev,
  not sample stdev, since we have the full population of sentences in
  this document, not a sample).
- monotony_flag = True if stdev < min_stdev_threshold.
- Handle zero-sentence text: return RhythmStats(sentence_count=0,
  mean_length_words=0.0, stdev_length_words=0.0, lengths=[],
  monotony_flag=False) — do not raise or produce NaN via statistics
  module on an empty list (statistics.pstdev on empty raises
  StatisticsError — catch this case explicitly beforehand, do not rely
  on a bare try/except swallowing it).

### Acceptance Check
Test manually: a sentence like "Смотри [[FN:3]] на результат." should
produce a word count of 3 (Смотри, на, результат), not 4 — confirm the
placeholder is excluded from the count.
```

---

## STEP 5: Diversity Stats

```
@workspace
Reference: docs/specs/SPEC-001-detector-component.md, section 2 (item 5)
and section 3 (compute_diversity_stats signature), section 4b.

IMPLEMENTER RULES — same as Step 1.

Create file: src/detector/diversity.py

Requirements:
- Implement `compute_diversity_stats(text, min_distinct_2, min_distinct_3, max_parallelism_matches) -> DiversityStats`
- Strip placeholder tokens from text FIRST (strip_placeholder_tokens),
  then lemmatize the remaining text via natasha into an ordered token
  list — placeholder tokens must never appear in the bigram/trigram
  stream (per section 4b, they'd artificially inflate "distinct" counts).
- Compute distinct-2 = (unique bigrams of lemmas) / (total bigrams),
  distinct-3 similarly for trigrams. Return 0.0 (not NaN, not a
  ZeroDivisionError) if there are fewer than 2 (or 3) tokens total.
- Implement literal triple-parallelism detection separately, operating
  on the ORIGINAL text (not lemmatized, not placeholder-stripped — this
  check is about surface punctuation patterns): regex match for
  "X, Y and Z" / "X, Y и Z" style constructions (comma-separated list of
  3 items joined by "and"/"и" before the last one). Return these as
  RuleMatch objects in DiversityStats.parallelism_matches, rule_name =
  "triple_parallelism", severity = Severity.LOW.

### Acceptance Check
Test manually with text containing "быстро, точно и эффективно" — confirm
it's caught by the parallelism regex. Test that a placeholder token
appearing twice (e.g. [[FN:2]] and [[FN:5]]) does NOT count as 2 distinct
"words" inflating distinct_2/distinct_3.
```

---

## STEP 6: Report Builder

```
@workspace
Reference: docs/specs/SPEC-001-detector-component.md, section 3
(build_detector_report signature) and section 2 (all 5 metrics).
Read all previous files created in Steps 1-5 — import from them,
do not reimplement any metric logic in this file.

IMPLEMENTER RULES — same as Step 1.

Create file: src/detector/report.py

Requirements:
- Implement `build_detector_report(text: str, config: DetectorConfig) -> DetectorReport`
- Dispatch blacklist/hedge detection based on config.language:
  if "ru", call detect_blacklist_phrases_ru with the lemma fields; if
  "en", call detect_blacklist_phrases_en with the literal fields.
- Call detect_meta_commentary, compute_rhythm_stats, compute_diversity_stats.
- Determine `passed` and `failed_rules`:
  - "cliche_blacklist" fails if len(cliche_matches) > config.max_cliche_matches
  - "hedge_blacklist" fails if len(hedge_matches) > config.max_hedge_matches
  - "rhythm_monotony" fails if rhythm.monotony_flag is True
  - "lexical_diversity" fails if diversity.distinct_2 < config.min_distinct_2
    or diversity.distinct_3 < config.min_distinct_3
  - "parallelism" fails if len(diversity.parallelism_matches) > config.max_parallelism_matches
  - passed = True only if failed_rules is empty
- Note: meta_commentary matches are informational in this report (always
  included in the report) but do NOT contribute to failed_rules/passed in
  this spec — SPEC-002 decides how to weight them in the rewrite prompt.
  (If this assumption seems wrong once you see real output, flag it —
  don't silently change the threshold logic without discussion.)

### Acceptance Check
Construct a short text with 2 known cliché matches and a threshold of
max_cliche_matches=1 — confirm passed=False and failed_rules contains
"cliche_blacklist".
```

---

## STEP 7: Unit Tests + Test Config Fixtures

```
@workspace
Reference: docs/specs/SPEC-001-detector-component.md, section 7 (exact
list of required test names and fixture requirements). Read all files
from src/detector/ created in Steps 1-6.

IMPLEMENTER RULES — same as Step 1.

Create test config fixtures:
- tests/fixtures/config/cliches_ru.txt — 5-10 entries, hand-picked,
  include at least one multi-word entry like "играет важную роль" so
  the inflection test has something to check against
- tests/fixtures/config/hedges_ru.txt — 5-10 entries
- tests/fixtures/config/cliches_en.txt — 5-10 entries
- tests/fixtures/config/hedges_en.txt — 5-10 entries
- tests/fixtures/config/thresholds.json — reasonable small-number
  thresholds suitable for deterministic testing

Create file: tests/test_detector.py

Requirements:
- Implement all test functions listed in SPEC-001 section 7, using
  pytest, against the fixture config directory above.
- test_cliche_blacklist_match_ru_inflected_form specifically: use a
  blacklist entry like "играет важную роль" (from the fixture file) and
  assert that text containing "играют важную роль" (different
  conjugation) still produces a match — this is the single most
  important test in this entire spec, don't write a weak version of it.
- test_cliche_blacklist_no_match_when_word_order_differs: confirm the
  documented limitation (section 4a) — text with scrambled word order
  does NOT match, and this is asserted as expected behavior, not
  treated as a bug.
- Each test should assert specific values (exact counts, exact matched
  text, exact position where reasonable) — not just "no exception raised."

### Acceptance Check
Run: `pytest tests/test_detector.py -v`
All tests must pass. If the inflection test fails, this is very likely a
real bug in the lemma-matching logic (Step 2) — do not weaken the test's
assertions to make it pass. Bring the failure back to Claude with full
output.
```

---

## After All 7 Steps Complete

```powershell
pytest tests/test_detector.py -v
git add src/detector tests/test_detector.py tests/fixtures/config
git commit -m "feat: Implement Detector Component — 5 programmatic metrics [SPEC-001]"
git push
git status
```

Then bring results back to Claude for Stage 6 (Validity/Safety review) before
marking SPEC-001 complete in its Implementation Status section — same
discipline as SPEC-003. Given how much of this spec is genuinely new logic
(lemma matching, placeholder-aware word counting), expect this review to
find at least one real issue.
