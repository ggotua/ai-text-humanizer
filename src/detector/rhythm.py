"""
Sentence-length rhythm / burstiness computation (SPEC-001 §2 item 4).

Segments text into sentences via ``razdel.sentenize`` on the **original**
text (placeholder tokens still present), then strips placeholder tokens
from each sentence before counting words via ``razdel.tokenize``.
"""

import statistics

from src.detector.models import RhythmStats
from src.detector.config_loader import strip_placeholder_tokens


def compute_rhythm_stats(
    text: str,
    min_stdev_threshold: float,
) -> RhythmStats:
    """Compute sentence-length rhythm statistics.

    Segmentation (``razdel.sentenize``) runs on the **original** text with
    placeholder tokens still present — removing them first would shift
    sentence boundaries (per SPEC-001 §4b).  Word counts are computed on
    each sentence **after** stripping placeholder tokens, so that
    ``[[FN:n]]`` / ``[[EN:n]]`` tokens are never counted as words.

    Population standard deviation (``statistics.pstdev``) is used because
    the sentences in this document are the full population, not a sample.

    Parameters
    ----------
    text:
        Input text that may contain ``[[FN:n]]`` / ``[[EN:n]]`` tokens.
    min_stdev_threshold:
        If the standard deviation of sentence lengths falls below this
        value, ``monotony_flag`` is set to ``True``.

    Returns
    -------
    A ``RhythmStats`` instance.  For empty or zero-sentence text returns
    a zeroed-out record (no exception).
    """
    from razdel import sentenize, tokenize

    # --- 1. Segment into sentences on the ORIGINAL text ---
    sentences = list(sentenize(text))

    # Filter out empty/whitespace-only sentences (razdel may return a
    # sentinel empty sentence for blank input).
    sentences = [s for s in sentences if s.text.strip()]

    if not sentences:
        return RhythmStats(
            sentence_count=0,
            mean_length_words=0.0,
            stdev_length_words=0.0,
            lengths=[],
            monotony_flag=False,
        )

    # --- 2. For each sentence, strip placeholders then count words ---
    lengths: list[int] = []
    for sent in sentences:
        # sent.text is the original sentence substring (with placeholders)
        cleaned, _ = strip_placeholder_tokens(sent.text)
        # strip_placeholder_tokens replaces tokens with "" which can leave
        # double spaces (e.g. "word [[FN:3]] word" -> "word  word").
        # Normalise whitespace so razdel.tokenize doesn't see empty tokens.
        cleaned = " ".join(cleaned.split())
        # Tokenize the cleaned sentence — keep only word tokens
        # (filter out standalone punctuation like ".", ",", "!" etc.).
        tokens = [
            t.text
            for t in tokenize(cleaned)
            if t.text.isalpha() or any(c.isalpha() for c in t.text)
        ]
        lengths.append(len(tokens))

    # --- 3. Compute statistics ---
    sentence_count = len(lengths)
    mean_length_words = sum(lengths) / sentence_count

    if sentence_count > 1:
        stdev_length_words = statistics.pstdev(lengths)
    else:
        # pstdev of a single value is 0.0; handle explicitly for clarity
        stdev_length_words = 0.0

    monotony_flag = stdev_length_words < min_stdev_threshold

    return RhythmStats(
        sentence_count=sentence_count,
        mean_length_words=mean_length_words,
        stdev_length_words=stdev_length_words,
        lengths=lengths,
        monotony_flag=monotony_flag,
    )