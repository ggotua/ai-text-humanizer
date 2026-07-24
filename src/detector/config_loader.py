"""
Configuration loading for the detector component (SPEC-001).

Provides ``load_config`` which reads blacklist files and thresholds from
a config directory, and ``strip_placeholder_tokens`` which removes
placeholder tokens from text before word-count or n-gram metrics.
"""

import json
import os
import re
from pathlib import Path

from src.detector.models import (
    DetectorConfig,
    PLACEHOLDER_TOKEN_PATTERN,
)

# ---------------------------------------------------------------------------
# Default thresholds — written here as a reference; overridden by
# thresholds.json in the config directory.  These are reasonable starting
# values that can be tuned later without changing the code.
# ---------------------------------------------------------------------------
_DEFAULT_THRESHOLDS = {
    "max_cliche_matches": 3,
    "max_hedge_matches": 5,
    "min_stdev_threshold": 3.0,
    "min_distinct_2": 0.3,
    "min_distinct_3": 0.1,
    "max_parallelism_matches": 2,
    "meta_opening_patterns": [
        r"(?i)^(давайте\s+разбер[её]м|let'?s\s+break\s+down)",
        r"(?i)^(в\s+этой\s+статье|in\s+this\s+article)",
    ],
    "meta_closing_patterns": [
        r"(?i)^(в\s+заключение|in\s+conclusion)",
        r"(?i)^(итак,\s*мы|so,?\s*(we|let))",
    ],
}

# Compile placeholder pattern once at module level
_PLACEHOLDER_RE = re.compile(PLACEHOLDER_TOKEN_PATTERN)


def _read_lines(filepath: str) -> list[str]:
    """Read non-empty, stripped lines from a text file."""
    lines: list[str] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
    return lines


def _lemmatize_phrase(phrase: str) -> tuple[str, ...]:
    """Lemmatize a Russian phrase into a tuple of lemmas using natasha.

    Imports natasha lazily so that EN-only deployments don't need the
    ~200 MB natasha model installed.
    """
    from natasha import (
        MorphVocab,
        NewsEmbedding,
        NewsMorphTagger,
        Segmenter,
        Doc,
    )

    emb = NewsEmbedding()
    morph_vocab = MorphVocab()
    seg = Segmenter()
    morph_tagger = NewsMorphTagger(emb)

    doc = Doc(phrase)
    doc.segment(seg)
    doc.tag_morph(morph_tagger)

    lemmas: list[str] = []
    for token in doc.tokens:
        token.lemmatize(morph_vocab)
        lemmas.append(token.lemma)
    return tuple(lemmas)


def load_config(config_dir: str, language: str) -> DetectorConfig:
    """Load blacklists and thresholds for the given language.

    Config file layout (all paths relative to *config_dir*):

    * ``cliches_{lang}.txt`` — one cliché phrase per line.
    * ``hedges_{lang}.txt`` — one hedge phrase per line.
    * ``thresholds.json`` — JSON object with the numeric threshold fields
      and regex pattern lists (see ``_DEFAULT_THRESHOLDS`` for the schema).

    For ``language="ru"`` each phrase line is lemmatised via ``natasha``
    into a lemma tuple.  For ``language="en"`` each line is lowercased and
    stored as a literal string in a frozenset.

    Parameters
    ----------
    config_dir:
        Path to the directory containing config files.
    language:
        ``"ru"`` or ``"en"``.

    Returns
    -------
    A fully-populated ``DetectorConfig``.

    Raises
    ------
    FileNotFoundError:
        If any required file is missing.  The message names the exact
        missing path.
    DetectorError (wrapping re.error):
        If any pattern in ``meta_opening_patterns`` or
        ``meta_closing_patterns`` is not valid regex.
    """
    if language not in ("ru", "en"):
        raise ValueError(
            f"Unsupported language: {language!r}. Must be 'ru' or 'en'."
        )

    base = Path(config_dir)

    # --- 1. Read cliché and hedge files ---
    cliches_path = base / f"cliches_{language}.txt"
    hedges_path = base / f"hedges_{language}.txt"

    if not cliches_path.exists():
        raise FileNotFoundError(
            f"Required config file not found: {cliches_path}"
        )
    if not hedges_path.exists():
        raise FileNotFoundError(
            f"Required config file not found: {hedges_path}"
        )

    cliche_lines = _read_lines(str(cliches_path))
    hedge_lines = _read_lines(str(hedges_path))

    # --- 2. Load thresholds.json ---
    thresholds_path = base / "thresholds.json"
    if thresholds_path.exists():
        with open(str(thresholds_path), "r", encoding="utf-8") as f:
            overrides = json.load(f)
    else:
        overrides = {}

    max_cliche = overrides.get(
        "max_cliche_matches", _DEFAULT_THRESHOLDS["max_cliche_matches"]
    )
    max_hedge = overrides.get(
        "max_hedge_matches", _DEFAULT_THRESHOLDS["max_hedge_matches"]
    )
    min_stdev = overrides.get(
        "min_stdev_threshold", _DEFAULT_THRESHOLDS["min_stdev_threshold"]
    )
    min_distinct_2 = overrides.get(
        "min_distinct_2", _DEFAULT_THRESHOLDS["min_distinct_2"]
    )
    min_distinct_3 = overrides.get(
        "min_distinct_3", _DEFAULT_THRESHOLDS["min_distinct_3"]
    )
    max_parallelism = overrides.get(
        "max_parallelism_matches", _DEFAULT_THRESHOLDS["max_parallelism_matches"]
    )
    meta_opening_raw = overrides.get(
        "meta_opening_patterns", _DEFAULT_THRESHOLDS["meta_opening_patterns"]
    )
    meta_closing_raw = overrides.get(
        "meta_closing_patterns", _DEFAULT_THRESHOLDS["meta_closing_patterns"]
    )

    # --- 3. Validate and compile regex patterns ---
    meta_opening_compiled: list[str] = []
    for i, pattern in enumerate(meta_opening_raw):
        try:
            re.compile(pattern)
            meta_opening_compiled.append(pattern)
        except re.error as exc:
            raise re.error(
                f"Invalid regex in meta_opening_patterns[{i}]: "
                f"{pattern!r} — {exc}"
            )

    meta_closing_compiled: list[str] = []
    for i, pattern in enumerate(meta_closing_raw):
        try:
            re.compile(pattern)
            meta_closing_compiled.append(pattern)
        except re.error as exc:
            raise re.error(
                f"Invalid regex in meta_closing_patterns[{i}]: "
                f"{pattern!r} — {exc}"
            )

    # --- 4. Build language-specific fields ---
    if language == "ru":
        cliche_lemmas: list[tuple[str, ...]] = []
        for phrase in cliche_lines:
            cliche_lemmas.append(_lemmatize_phrase(phrase))
        # Deduplicate by converting to dict keys preserving order
        seen: set[tuple[str, ...]] = set()
        deduped_cliche: list[tuple[str, ...]] = []
        for t in cliche_lemmas:
            if t not in seen:
                seen.add(t)
                deduped_cliche.append(t)

        hedge_lemmas: list[tuple[str, ...]] = []
        for phrase in hedge_lines:
            hedge_lemmas.append(_lemmatize_phrase(phrase))
        seen_hedge: set[tuple[str, ...]] = set()
        deduped_hedge: list[tuple[str, ...]] = []
        for t in hedge_lemmas:
            if t not in seen_hedge:
                seen_hedge.add(t)
                deduped_hedge.append(t)

        return DetectorConfig(
            language="ru",
            cliche_blacklist_lemmas=tuple(deduped_cliche),
            hedge_blacklist_lemmas=tuple(deduped_hedge),
            cliche_blacklist_literal=frozenset(),
            hedge_blacklist_literal=frozenset(),
            meta_opening_patterns=tuple(meta_opening_compiled),
            meta_closing_patterns=tuple(meta_closing_compiled),
            max_cliche_matches=max_cliche,
            max_hedge_matches=max_hedge,
            min_stdev_threshold=min_stdev,
            min_distinct_2=min_distinct_2,
            min_distinct_3=min_distinct_3,
            max_parallelism_matches=max_parallelism,
        )

    elif language == "en":
        cliche_literal = frozenset(line.lower() for line in cliche_lines)
        hedge_literal = frozenset(line.lower() for line in hedge_lines)

        return DetectorConfig(
            language="en",
            cliche_blacklist_lemmas=(),
            hedge_blacklist_lemmas=(),
            cliche_blacklist_literal=cliche_literal,
            hedge_blacklist_literal=hedge_literal,
            meta_opening_patterns=tuple(meta_opening_compiled),
            meta_closing_patterns=tuple(meta_closing_compiled),
            max_cliche_matches=max_cliche,
            max_hedge_matches=max_hedge,
            min_stdev_threshold=min_stdev,
            min_distinct_2=min_distinct_2,
            min_distinct_3=min_distinct_3,
            max_parallelism_matches=max_parallelism,
        )

    else:
        raise ValueError(
            f"Unsupported language: {language!r}. Expected 'ru' or 'en'."
        )


def strip_placeholder_tokens(text: str) -> tuple[str, list[tuple[int, str]]]:
    """Remove placeholder tokens from *text*, recording their positions.

    Returns ``(cleaned_text, removed)`` where *cleaned_text* is the input
    with all ``[[FN:n]]`` / ``[[EN:n]]`` tokens removed, and *removed* is
    a list of ``(original_position, token_string)`` tuples in the order
    the tokens appeared.

    The positions in *removed* refer to the **original** input text, not
    the cleaned text.  This allows downstream functions (e.g. rhythm word
    counting) to exclude tokens without needing to re-parse the original.

    Parameters
    ----------
    text:
        Input text that may contain placeholder tokens.

    Returns
    -------
    ``(cleaned_text, removed_token_positions)``
    """
    removed: list[tuple[int, str]] = []
    cleaned = _PLACEHOLDER_RE.sub("", text)
    for match in _PLACEHOLDER_RE.finditer(text):
        removed.append((match.start(), match.group()))
    return cleaned, removed