"""Small and free models make predictable mistakes; none may reach the finished video.

They write far less than requested, copy the prompt's example values, repeat a
section, add markup that speech would read aloud, or get an optional list
wrong. Each case must end in a usable script or an explicit failure.
"""
from copy import deepcopy
import json

import pytest

import config
from agents import researcher, script_editor, scriptwriter
from video import stock

RESEARCH = {"topic": "The local river", "video_title": "Understanding the river",
            "key_points": ["The river follows a wooded valley."], "tags": ["river"]}
ONE_MINUTE = {"CHANNEL_PRESET": "general", "VOICE_RATE": "+0%", "TARGET_DURATION_MINUTES": 1}   # about 135 words


def script(words_per_section=45, sections=3):
    def narration(number):
        return " ".join(f"river{number}word{index}" for index in range(words_per_section)) + "."
    return {"title": "The river", "tags": ["river"], "sections": [
        {"title": f"Part {number}", "narration": narration(number), "image_query": f"river view {number}"}
        for number in range(1, sections + 1)]}


def answers(monkeypatch, responses):
    calls = []
    remaining = iter(responses)

    def generate(prompt, **kwargs):
        calls.append(prompt)
        return next(remaining)

    monkeypatch.setattr(scriptwriter, "generate", generate)
    return calls


def write():
    with config.profile_context(ONE_MINUTE):
        return scriptwriter.write_script(deepcopy(RESEARCH))


def spoken_words(result):
    return sum(len(row["narration"].split()) for row in result["sections"])


def test_prompt_states_a_word_target_for_each_section(monkeypatch):
    calls = answers(monkeypatch, [json.dumps(script())])
    write()
    assert "roughly 45 words of narration in EACH section" in calls[0]
    assert "never copy them" in calls[0]


def test_far_too_short_script_gets_one_replacement_with_concrete_numbers(monkeypatch, capsys):
    calls = answers(monkeypatch, [json.dumps(script(words_per_section=8)), json.dumps(script())])
    result = write()
    assert len(calls) == 2 and spoken_words(result) == 135
    assert calls[1].startswith(calls[0])
    assert "only 24 spoken words; about 135 are needed" in calls[1]
    assert "roughly 45 words of narration in each section" in calls[1]
    assert "Using the best complete script" not in capsys.readouterr().out


def test_two_short_scripts_keep_the_longer_one_with_a_warning_instead_of_failing(monkeypatch, capsys):
    calls = answers(monkeypatch, [json.dumps(script(words_per_section=20)), json.dumps(script(words_per_section=8))])
    result = write()
    assert len(calls) == 2 and spoken_words(result) == 60
    assert "shorter than requested" in capsys.readouterr().out


def test_script_of_the_requested_length_needs_no_second_request(monkeypatch):
    calls = answers(monkeypatch, [json.dumps(script())])
    assert spoken_words(write()) == 135 and len(calls) == 1


def test_copied_example_narration_is_never_spoken(monkeypatch):
    echoed = script()
    echoed["sections"][1]["narration"] = scriptwriter._EXAMPLE_SECTION["narration"]
    calls = answers(monkeypatch, [json.dumps(echoed), json.dumps(script())])
    result = write()
    assert len(calls) == 2 and "repeats the example narration" in calls[1]
    assert all("Complete spoken sentences" not in row["narration"] for row in result["sections"])
    answers(monkeypatch, [json.dumps(echoed), json.dumps(echoed)])
    with pytest.raises(ValueError, match="example narration"):
        write()


def test_repeated_section_is_replaced_not_recorded_twice(monkeypatch):
    repeated = script()
    repeated["sections"][2]["narration"] = repeated["sections"][0]["narration"].upper()
    calls = answers(monkeypatch, [json.dumps(repeated), json.dumps(script())])
    result = write()
    assert len(calls) == 2 and "sections 1 and 3 repeat" in calls[1]
    assert len({row["narration"] for row in result["sections"]}) == 3


def test_markup_and_stage_directions_are_removed_before_speech(monkeypatch):
    marked = script()
    plain = marked["sections"][0]["narration"]
    marked["sections"][0]["narration"] = f"Narrator: **{plain}** [pause]"
    marked["sections"][1]["narration"] = "# " + marked["sections"][1]["narration"] + " [1]"
    calls = answers(monkeypatch, [json.dumps(marked)])
    result = write()
    assert len(calls) == 1
    assert result["sections"][0]["narration"] == plain
    assert result["sections"][1]["narration"] == script()["sections"][1]["narration"]


def test_section_of_only_stage_directions_is_not_accepted(monkeypatch):
    empty = script()
    empty["sections"][0]["narration"] = "[soft music] [pause]"
    answers(monkeypatch, [json.dumps(empty), json.dumps(empty)])
    with pytest.raises(ValueError, match="production directions"):
        write()


def test_sound_narration_survives_bad_optional_details_when_the_retry_also_fails(monkeypatch, capsys):
    loose = script()
    loose["tags"] = "river, valley; water"
    loose["description"] = scriptwriter._EXAMPLE_DESCRIPTION
    loose["sections"][0].update(bullet_points="One point written as a sentence", image_query=None,
                                image_query_2=scriptwriter._EXAMPLE_SECTION["image_query_2"])
    loose["sections"][1]["bullet_points"] = [f"point {number}" for number in range(12)] + [7]
    calls = answers(monkeypatch, [json.dumps(loose), '{"title":"The river","sections":[{"narration":"cut o'])
    result = write()
    assert len(calls) == 2
    assert [row["narration"] for row in result["sections"]] == [row["narration"] for row in script()["sections"]]
    assert result["tags"] == ["river", "valley", "water"] and "description" not in result
    first, second = result["sections"][:2]
    assert first["bullet_points"] == ["One point written as a sentence"]
    assert "image_query" not in first and "image_query_2" not in first
    assert second["bullet_points"] == [f"point {number}" for number in range(8)]
    assert "optional details" in capsys.readouterr().out


def test_strictly_valid_replacement_is_preferred_over_a_repaired_first_response(monkeypatch):
    loose = script()
    loose["tags"] = "river, valley"
    replacement = script()
    replacement["title"] = "The replacement"
    answers(monkeypatch, [json.dumps(loose), json.dumps(replacement)])
    assert write()["title"] == "The replacement"


# ── research ────────────────────────────────────────────────────────────────

def research(monkeypatch, replies):
    calls = []
    remaining = iter(replies)
    monkeypatch.setattr(researcher, "generate", lambda prompt, **kwargs: calls.append(prompt) or next(remaining))
    monkeypatch.setattr(researcher, "_grounding", lambda topic: {"facts": "", "sources": []})
    return calls


def test_research_that_copies_the_example_title_is_requested_again(monkeypatch):
    echoed = {**RESEARCH, "video_title": researcher._SHAPE["video_title"]}
    calls = research(monkeypatch, [json.dumps(echoed), json.dumps(RESEARCH)])
    result = researcher.research_topic("River walks")
    assert len(calls) == 2 and result["video_title"] == "Understanding the river"


def test_research_with_only_example_key_points_is_requested_again(monkeypatch):
    echoed = {**RESEARCH, "key_points": researcher._SHAPE["key_points"]}
    calls = research(monkeypatch, [json.dumps(echoed), json.dumps(RESEARCH)])
    assert researcher.research_topic("River walks")["key_points"] == RESEARCH["key_points"] and len(calls) == 2


def test_research_shape_slips_are_normalised_without_spending_the_retry(monkeypatch):
    loose = {"research": {**RESEARCH, "tags": "river, valley; #water",
                          "key_points": "- The river follows a wooded valley.\n- A second supported point\n• It reaches the sea.",
                          "why_now": researcher._SHAPE["why_now"], "hook_question": "Where does the river go?",
                          "thumbnail_concept": ["not", "text"]}}
    calls = research(monkeypatch, ["Here is the plan:\n```json\n" + json.dumps(loose) + "\n```"])
    result = researcher.research_topic("River walks")
    assert len(calls) == 1
    assert result["tags"] == ["river", "valley", "water"]
    assert result["key_points"] == ["The river follows a wooded valley.", "It reaches the sea."]
    assert "why_now" not in result and "thumbnail_concept" not in result
    assert result["hook_question"] == "Where does the river go?"


def test_requested_topic_is_kept_even_when_the_model_copies_the_example(monkeypatch):
    echoed = {**RESEARCH, "topic": researcher._SHAPE["topic"]}
    calls = research(monkeypatch, [json.dumps(echoed)])
    assert researcher.research_topic("River walks", topic_override="Tides")["topic"] == "Tides" and len(calls) == 1


# ── images and editor ───────────────────────────────────────────────────────

def test_section_without_a_query_searches_for_its_own_subject(tmp_path, monkeypatch):
    searched = []
    monkeypatch.setattr(stock, "_fetch_images", lambda query, *args, **kwargs: searched.append(query) or [])
    stock.download_images({"title": "Bread baking at home", "sections": [
        {"id": 1, "title": "Kneading the dough", "narration": "Knead."},
        {"id": 2, "narration": "Bake.", "image_query": "  ", "image_query_2": None},
        {"id": 3, "narration": "Cool.", "image_query": "bread cooling rack"},
    ]}, str(tmp_path))
    assert searched == ["Kneading the dough", "Bread baking at home", "bread cooling rack"]


EDITED = {"title": "A river explained", "sections": [
    {"id": 1, "title": "Beginning", "narration": "Water moves through this river and supports many living things along its banks.", "duration_seconds": 6},
    {"id": 2, "title": "Development", "narration": "Trees protect the soil while the river carries water toward the sea each day.", "duration_seconds": 6},
]}


@pytest.mark.parametrize("title,status", [("Beginning", "reviewed"), ("A new invented heading", "original_kept")])
def test_editor_accepts_an_echoed_title_but_not_a_changed_one(monkeypatch, title, status):
    monkeypatch.setattr(config, "SCRIPT_REVIEW_ENABLED", True)
    monkeypatch.setattr(config, "CHANNEL_PRESET", "general")
    reply = {"notes": [], "sections": [{"id": row["id"], "title": row["title"], "narration": row["narration"]}
                                       for row in EDITED["sections"]]}
    reply["sections"][0].update(title=title,
                                narration="This river carries water and supports many living things along its green banks.")
    monkeypatch.setattr(script_editor, "generate", lambda *args, **kwargs: json.dumps(reply))
    result = script_editor.review_script(EDITED, {})
    assert result["quality_review"]["status"] == status
    assert result["sections"][0]["title"] == "Beginning"
