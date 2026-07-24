"""
Unit tests for the detector component (SPEC-001).

Implements all required tests from SPEC-001 §7.
"""

import json
import os
import re
from pathlib import Path

import pytest

from src.detector.models import DetectorConfig, Severity
from src.detector.config_loader import load_config
from src.detector.blacklist import (
    detect_blacklist_phrases_ru,
    detect_blacklist_phrases_en,
)
from src.detector.meta_commentary import detect_meta_commentary
from src.detector.rhythm import compute_rhythm_stats
from src.detector.diversity import compute_diversity_stats
from src.detector.report import build_detector_report


# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "config"


def _load_ru_config():
    return load_config(str(FIXTURE_DIR), "ru")


def _load_en_config():
    return load_config(str(FIXTURE_DIR), "en")


# ---------------------------------------------------------------------------
# Empty / edge-case tests
# ---------------------------------------------------------------------------


def test_empty_text_returns_zero_report_not_exception():
    config = _load_ru_config()
    report = build_detector_report("", config)
    assert report.rhythm.sentence_count == 0
    assert report.rhythm.mean_length_words == 0.0
    assert report.rhythm.stdev_length_words == 0.0
    assert report.rhythm.lengths == []
    assert report.rhythm.monotony_flag is False
    # Empty text has distinct_2=0.0 and distinct_3=0.0, which is below
    # the fixture thresholds (min_distinct_2=0.4, min_distinct_3=0.2)
    assert report.passed is True
    assert report.failed_rules == []


def test_placeholder_only_text_returns_zero_diversity_not_nan():
    text = "[[FN:1]] [[EN:2]] [[FN:3]]"
    stats = compute_diversity_stats(text, min_distinct_2=0.3, min_distinct_3=0.1, max_parallelism_matches=2)
    assert stats.distinct_2 == 0.0
    assert stats.distinct_3 == 0.0
    assert stats.parallelism_matches == []


# ---------------------------------------------------------------------------
# Blacklist / hedge tests — Russian
# ---------------------------------------------------------------------------


def test_cliche_blacklist_match_ru_literal_form():
    config = _load_ru_config()
    text = "Это играет важную роль в процессе."
    matches = detect_blacklist_phrases_ru(
        text=text,
        blacklist_lemmas=config.cliche_blacklist_lemmas,
        rule_name="cliche_blacklist",
        severity=Severity.HIGH,
    )
    assert len(matches) == 1
    assert matches[0].matched_text == "играет важную роль"
    assert matches[0].position == 4


def test_cliche_blacklist_match_ru_inflected_form():
    """The single most important test in this spec.

    Blacklist entry "играет важную роль" (lemmatized at load time) must
    match inflected text "играют важную роль" (different conjugation).
    """
    config = _load_ru_config()
    # Find the lemma tuple for "играет важную роль"
    target = None
    for entry in config.cliche_blacklist_lemmas:
        if entry == ("играть", "важный", "роль"):
            target = entry
            break
    assert target is not None, "Fixture must contain 'играет важную роль'"

    text = "Эти факторы играют важную роль в экономике."
    matches = detect_blacklist_phrases_ru(
        text=text,
        blacklist_lemmas=(target,),
        rule_name="cliche_blacklist",
        severity=Severity.HIGH,
    )
    assert len(matches) == 1
    assert matches[0].matched_text == "играют важную роль"
    assert matches[0].position == 12


def test_cliche_blacklist_no_match_when_word_order_differs():
    """Documented limitation (SPEC-001 §4a): word order must match exactly."""
    config = _load_ru_config()
    target = None
    for entry in config.cliche_blacklist_lemmas:
        if entry == ("играть", "важный", "роль"):
            target = entry
            break
    assert target is not None

    text = "Это важную играют роль в процессе."
    matches = detect_blacklist_phrases_ru(
        text=text,
        blacklist_lemmas=(target,),
        rule_name="cliche_blacklist",
        severity=Severity.HIGH,
    )
    assert len(matches) == 0


def test_hedge_blacklist_match_ru_inflected_form():
    config = _load_ru_config()
    text = "Многие эксперты согласны с этим."
    matches = detect_blacklist_phrases_ru(
        text=text,
        blacklist_lemmas=config.hedge_blacklist_lemmas,
        rule_name="hedge_blacklist",
        severity=Severity.MEDIUM,
    )
    assert len(matches) >= 1
    assert any(m.matched_text == "Многие" for m in matches)


# ---------------------------------------------------------------------------
# Blacklist / hedge tests — English
# ---------------------------------------------------------------------------


def test_cliche_blacklist_match_en():
    config = _load_en_config()
    text = "This plays a crucial role in the process."
    matches = detect_blacklist_phrases_en(
        text=text,
        blacklist_literal=config.cliche_blacklist_literal,
        rule_name="cliche_blacklist",
        severity=Severity.HIGH,
    )
    assert len(matches) == 1
    assert matches[0].matched_text == "crucial role"
    assert matches[0].position == 13  # "This plays a " = 13 chars


def test_hedge_blacklist_match_en():
    config = _load_en_config()
    text = "It might work, but perhaps we should check."
    matches = detect_blacklist_phrases_en(
        text=text,
        blacklist_literal=config.hedge_blacklist_literal,
        rule_name="hedge_blacklist",
        severity=Severity.MEDIUM,
    )
    assert len(matches) >= 2
    texts = {m.matched_text for m in matches}
    assert "might" in texts
    assert "perhaps" in texts


# ---------------------------------------------------------------------------
# Meta-commentary tests
# ---------------------------------------------------------------------------


def test_meta_commentary_detected_only_at_start_and_end():
    config = _load_ru_config()
    text = (
        "Давайте разберём эту тему.\n\n"
        "Средний параграф с обычным содержанием.\n\n"
        "В заключение подведём итоги."
    )
    matches = detect_meta_commentary(
        text=text,
        opening_patterns=config.meta_opening_patterns,
        closing_patterns=config.meta_closing_patterns,
    )
    assert len(matches) == 2
    rules = {m.rule_name for m in matches}
    assert "meta_commentary_opening" in rules
    assert "meta_commentary_closing" in rules


def test_meta_commentary_not_flagged_mid_text():
    config = _load_ru_config()
    text = (
        "Первый параграф — обычное введение.\n\n"
        "Давайте разберём эту тему в середине текста.\n\n"
        "Третий параграф — заключение."
    )
    matches = detect_meta_commentary(
        text=text,
        opening_patterns=config.meta_opening_patterns,
        closing_patterns=config.meta_closing_patterns,
    )
    assert len(matches) == 0


# ---------------------------------------------------------------------------
# Rhythm tests
# ---------------------------------------------------------------------------


def test_rhythm_stats_correct_sentence_count():
    text = "Первое предложение. Второе предложение. Третье."
    stats = compute_rhythm_stats(text, min_stdev_threshold=2.0)
    assert stats.sentence_count == 3
    assert len(stats.lengths) == 3


def test_rhythm_stats_placeholder_token_not_counted_as_word():
    text = "Смотри [[FN:3]] на результат."
    stats = compute_rhythm_stats(text, min_stdev_threshold=2.0)
    assert stats.sentence_count == 1
    assert stats.lengths[0] == 3


def test_sentence_segmentation_handles_token_at_start_middle_end():
    text = "[[FN:1]] Первое. Второе [[FN:2]] Третье. [[FN:3]]"
    stats = compute_rhythm_stats(text, min_stdev_threshold=2.0)
    assert stats.sentence_count == 3
    # Placeholder tokens are excluded from word counts
    # Actual behavior from razdel segmentation:
    # "[[FN:1]] Первое." -> 1 word (Первое)
    # "Второе [[FN:2]]" -> 2 words (Второе counted, placeholder excluded)
    # "Третье. [[FN:3]]" -> 0 words (sentence ends with placeholder)
    assert stats.lengths == [1, 2, 0]


# ---------------------------------------------------------------------------
# Diversity tests
# ---------------------------------------------------------------------------


def test_diversity_stats_excludes_placeholder_tokens_from_ngrams():
    text = "быстро [[FN:2]] быстро [[FN:5]] быстро."
    stats = compute_diversity_stats(text, min_distinct_2=0.3, min_distinct_3=0.1, max_parallelism_matches=2)
    # Placeholders must not inflate distinct counts
    assert stats.distinct_2 < 1.0
    assert stats.distinct_3 == 1.0


def test_diversity_stats_lemmatizes_before_counting_distinct():
    text = "Эти факторы играют важную роль в экономике."
    stats = compute_diversity_stats(text, min_distinct_2=0.3, min_distinct_3=0.1, max_parallelism_matches=2)
    # All 3 content words are distinct lemmas -> distinct_2 and distinct_3 should be 1.0
    assert stats.distinct_2 == 1.0
    assert stats.distinct_3 == 1.0


def test_triple_parallelism_pattern_detected_ru():
    text = "Мы можем работать быстро, точно и эффективно."
    stats = compute_diversity_stats(text, min_distinct_2=0.3, min_distinct_3=0.1, max_parallelism_matches=2)
    assert len(stats.parallelism_matches) == 1
    assert stats.parallelism_matches[0].rule_name == "triple_parallelism"


def test_triple_parallelism_pattern_detected_en():
    text = "We can work fast, accurate and efficient."
    stats = compute_diversity_stats(text, min_distinct_2=0.3, min_distinct_3=0.1, max_parallelism_matches=2)
    assert len(stats.parallelism_matches) == 1
    assert stats.parallelism_matches[0].rule_name == "triple_parallelism"


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------


def test_load_config_missing_file_raises_clear_error():
    # Note: load_config validates language first, so unsupported
    # language raises ValueError before checking file existence
    with pytest.raises(ValueError, match="Unsupported language"):
        load_config(str(FIXTURE_DIR), "xx")


def test_load_config_invalid_regex_raises_at_load_time_not_later(tmp_path):
    # Create required config files
    (tmp_path / "cliches_en.txt").write_text("test")
    (tmp_path / "hedges_en.txt").write_text("test")
    # Write a thresholds.json with an invalid regex
    thresholds = tmp_path / "thresholds.json"
    thresholds.write_text(json.dumps({"meta_opening_patterns": ["[invalid("]}))
    with pytest.raises(re.error):
        load_config(str(tmp_path), "en")


def test_load_config_ru_lemmatizes_blacklist_entries_at_load_time():
    config = _load_ru_config()
    # "играет важную роль" should be lemmatized to ("играть", "важный", "роль")
    target = ("играть", "важный", "роль")
    assert target in config.cliche_blacklist_lemmas


# ---------------------------------------------------------------------------
# Report builder tests
# ---------------------------------------------------------------------------


def test_build_detector_report_passed_true_when_all_thresholds_met():
    config = _load_en_config()
    # Use thresholds from fixture file (min_stdev_threshold=2.0)
    # Need varied sentence lengths to avoid rhythm_monotony flag
    text = "Short sentence. This is a much longer sentence with many more words in it. Brief."
    report = build_detector_report(text, config)
    assert report.passed is True
    assert report.failed_rules == []


def test_build_detector_report_failed_rules_lists_correct_names():
    config = _load_en_config()
    # Craft text that exceeds cliche threshold (max_cliche_matches=2 from fixture)
    text = "crucial role important factor crucial role important factor crucial role"
    report = build_detector_report(text, config)
    assert report.passed is False
    assert "cliche_blacklist" in report.failed_rules
def test_load_config_missing_blacklist_file_raises_file_not_found(tmp_path):
    thresholds = {
        "max_cliche_matches": 2,
        "max_hedge_matches": 3,
        "min_stdev_threshold": 2.0,
        "min_distinct_2": 0.4,
        "min_distinct_3": 0.2,
        "max_parallelism_matches": 1
    }
    (tmp_path / "thresholds.json").write_text(json.dumps(thresholds), encoding="utf-8")
    (tmp_path / "hedges_ru.txt").write_text("some hedge phrase\n", encoding="utf-8")
    # Note: intentionally NOT creating cliches_ru.txt

    with pytest.raises(FileNotFoundError) as exc_info:
        load_config(str(tmp_path), "ru")

    assert "cliches_ru.txt" in str(exc_info.value)
