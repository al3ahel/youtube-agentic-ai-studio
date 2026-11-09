"""Narrated shot ownership is independent of subtitle display chunking."""
from copy import deepcopy

import pytest

from agents.narration_scope import scene_source


def test_mail_and_steel_sentences_stay_in_their_own_shots_across_a_caption_cut():
    section = {"id": "construction", "start": 0, "duration": 4,
               "narration": "Mail uses linked rings. Steel plate deflects blows."}
    timings = [("Mail", .1, .3), ("uses", .4, .6), ("linked", .8, 1.1), ("rings", 1.8, 2.1),
               ("Steel", 2, 2.3), ("plate", 2.4, 2.6), ("deflects", 2.8, 3.2), ("blows", 3.3, 3.7)]
    words = [{"word": word, "start": start, "end": end, "section_id": "construction"}
             for word, start, end in timings]
    captions = [{"start": 0, "end": 1.4, "text": "Mail uses linked"},
                {"start": 1.4, "end": 2.7, "text": "rings Steel plate"},
                {"start": 2.7, "end": 4, "text": "deflects blows"}]
    before = deepcopy((section, words, captions))
    mail = scene_source(section, 0, 2, captions, words)
    steel = scene_source(section, 2, 4, captions, words)
    assert mail["source_narration"] == mail["source_sentence_context"] == "Mail uses linked rings."
    assert steel["source_narration"] == steel["source_sentence_context"] == "Steel plate deflects blows."
    assert mail["source_scope"] == steel["source_scope"] == "word_timing"
    assert mail["source_alignment"] == steel["source_alignment"] == "exact"
    assert mail["source_section_narration"] == steel["source_section_narration"] == section["narration"]
    assert (section, words, captions) == before


@pytest.mark.parametrize("source, words, excerpt, context", [
    ("Could this plate stop a bullet? Later, inspect the dent.",
     ["Could", "this", "plate", "stop", "a", "bullet", "Later", "inspect", "the", "dent"],
     "stop a bullet?", "Could this plate stop a bullet?"),
    ("This plate cannot stop a bullet. Later, inspect the dent.",
     ["This", "plate", "cannot", "stop", "a", "bullet", "Later", "inspect", "the", "dent"],
     "stop a bullet.", "This plate cannot stop a bullet."),
])
def test_original_question_and_negation_survive_in_separate_sentence_context(source, words, excerpt, context):
    section = {"id": "proof", "start": 0, "duration": 10, "narration": source}
    measured = [{"word": word, "start": index, "end": index + .5, "section_id": "proof"}
                for index, word in enumerate(words)]
    result = scene_source(section, 3, 6, words=measured)
    assert result["source_narration"] == excerpt
    assert result["source_sentence_context"] == context
    assert "Later" not in result["source_narration"] + result["source_sentence_context"]


def test_silent_window_does_not_borrow_next_caption_or_guess_future_words():
    section = {"id": "one", "start": 0, "duration": 4, "narration": "Mail. Steel."}
    measured = [{"word": "Mail", "start": 0, "end": .4}, {"word": "Steel", "start": 3, "end": 3.5}]
    result = scene_source(section, 1, 2, [{"start": 1, "end": 4, "text": "Steel"}], measured)
    assert result["source_narration"] == result["source_sentence_context"] == ""
    assert result["source_scope"] == "word_timing_gap"
    assert result["source_alignment"] == "exact"


def test_partial_word_alignment_cannot_borrow_source_punctuation_or_negation():
    section = {"id": "one", "start": 0, "duration": 4, "narration": "A bullet did not stop."}
    measured = [{"word": "A", "start": 0, "end": .4}, {"word": "bullet", "start": .5, "end": 1},
                {"word": "stopped", "start": 1.2, "end": 1.8}]
    result = scene_source(section, 0, 2, words=measured)
    assert result["source_narration"] == "A bullet stopped"
    assert result["source_sentence_context"] == ""
    assert result["source_scope"] == "word_timing_unaligned"
    assert result["source_alignment"] == "unverified"


def test_original_punctuation_and_unidentified_word_events_align_without_neighbor_section_words():
    section = {"id": "two", "start": 2, "duration": 2, "narration": "Don’t assume 3.5 seconds proves it!"}
    measured = [{"word": "Other", "start": 0, "end": 1},
                *[{"word": word, "start": 2 + index * .2, "end": 2.1 + index * .2}
                  for index, word in enumerate(["Don't", "assume", "3.5", "seconds", "proves", "it"])]]
    result = scene_source(section, 2, 4, words=measured)
    assert result["source_alignment"] == "exact"
    assert result["source_narration"] == result["source_sentence_context"] == section["narration"]
