"""
Lexical diversity / n-gram computation (SPEC-001 §2 item 5).

Computes distinct-2 / distinct-3 scores on lemmatised tokens (placeholder
tokens excluded) and detects literal triple-parallelism patterns on the
original text.
"""

import re

from src.detector.models import DiversityStats, RuleMatch, Severity


# Regex for triple-parallelism: "X, Y and Z" / "X, Y и Z"
# Matches three items separated by commas, with "and"/"и" before the last.
_PARALLELISM_RE = re.compile(
    r"(?i)\b([^,]+),\s*([^,]+?)\s+(?:and|и)\s+([^,]+?)\b"
)


def _lemmatize_tokens(text: str) -> list[str]:
    """Lemmatize *text* via natasha, returning an ordered list of lemmas.

    Placeholder tokens must already be stripped from *text* before calling
    this function — they would otherwise appear as separate "words" and
    inflate distinct-2/distinct-3 scores.
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

    doc = Doc(text)
    doc.segment(seg)
    doc.tag_morph(morph_tagger)

    lemmas: list[str] = []
    for token in doc.tokens:
        token.lemmatize(morph_vocab)
        lemmas.append(token.lemma)

    return lemmas


def _distinct_ngrams(tokens: list[str], n: int) -> float:
    """Compute the distinct-n ratio: unique n-grams / total n-grams.

    Returns 0.0 if there are fewer than *n* tokens (no n-grams possible).
    """
    if len(tokens) < n:
        return 0.0

    total = len(tokens) - n + 1
    unique = len({tuple(tokens[i : i + n]) for i in range(total)})
    return unique / total


def _detect_parallelism(text: str) -> list[RuleMatch]:
    """Detect literal triple-parallelism patterns in *text*.

    Uses a regex to find constructions like "X, Y and Z" or "X, Y и Z".
    Operates on the **original** text (not lemmatized, not placeholder-
    stripped) because this check is about surface punctuation patterns.

    Returns one RuleMatch per regex match found.
    """
    matches: list[RuleMatch] = []
    for m in _PARALLELISM_RE.finditer(text):
        matches.append(
            RuleMatch(
                rule_name="triple_parallelism",
                matched_text=m.group(),
                position=m.start(),
                severity=Severity.LOW,
            )
        )
    return matches


def compute_diversity_stats(
    text: str,
    min_distinct_2: float,
    min_distinct_3: float,
    max_parallelism_matches: int,
) -> DiversityStats:
    """Compute lexical diversity statistics.

    1. Strips placeholder tokens from *text* (so they never appear in the
       n-gram stream and inflate distinct counts).
    2. Lemmatizes the remaining text via natasha into an ordered token list.
    3. Computes distinct-2 and distinct-3 ratios.
    4. Detects triple-parallelism patterns on the **original** text.

    Parameters
    ----------
    text:
        Input text that may contain ``[[FN:n]]`` / ``[[EN:n]]`` tokens.
    min_distinct_2:
        Minimum acceptable distinct-2 ratio (informational; not enforced
        here — enforcement happens in ``build_detector_report``).
    min_distinct_3:
        Minimum acceptable distinct-3 ratio (informational).
    max_parallelism_matches:
        Maximum allowed parallelism matches (informational).

    Returns
    -------
    A ``DiversityStats`` instance.
    """
    from src.detector.config_loader import strip_placeholder_tokens

    # --- 1. Strip placeholders before lemmatization ---
    cleaned, _ = strip_placeholder_tokens(text)
    cleaned = " ".join(cleaned.split())  # normalise whitespace

    # --- 2. Lemmatize ---
    if cleaned.strip():
        lemmas = _lemmatize_tokens(cleaned)
    else:
        lemmas = []

    # --- 3. Distinct-n ratios ---
    distinct_2 = _distinct_ngrams(lemmas, 2)
    distinct_3 = _distinct_ngrams(lemmas, 3)

    # --- 4. Triple-parallelism on original text ---
    parallelism_matches = _detect_parallelism(text)

    return DiversityStats(
        distinct_2=distinct_2,
        distinct_3=distinct_3,
        parallelism_matches=parallelism_matches,
    )