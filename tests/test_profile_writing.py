"""Profile-specific planning, writing targets and honest source provenance."""
from copy import deepcopy
import json

import pytest

import config
from agents import researcher, scriptwriter
from studio import channels


RESEARCH = {"topic": "The local river", "video_title": "Understanding the river",
            "key_points": ["A sourced point", "Another sourced point"], "tags": ["river"]}
SOURCES = {"facts": "A retrieved river background excerpt.",
           "sources": [{"source": "Wikipedia", "title": "River", "url": "https://en.wikipedia.org/wiki/River"}]}
SCRIPT = {"title": "The river", "sections": [
    {"id": 100, "title": "A gentle beginning", "narration": "The river moves slowly through this quiet place.",
     "image_query": "river landscape", "duration_seconds": 500},
    {"id": 100, "title": "Along the bank", "narration": "Notice the trees beside the water.",
     "image_query": "river trees", "duration_seconds": 500},
    {"id": 100, "title": "An ending", "narration": "We can return to this view another day.",
     "image_query": "river view", "duration_seconds": 500},
]}


def test_profile_duration_and_style_reach_writer_without_changing_base(monkeypatch):
    prompts = []
    monkeypatch.setattr(scriptwriter, "generate", lambda prompt: prompts.append(prompt) or json.dumps(SCRIPT))
    original = config.CHANNEL_NAME
    with config.profile_context({"CHANNEL_NAME": "Quiet River", "CHANNEL_DESCRIPTION": "Restful river walks.",
                                 "CHANNEL_PRESET": "asmr", "TARGET_DURATION_MINUTES": 10, "VOICE_RATE": "-15%"}):
        calm = scriptwriter.write_script({**RESEARCH, "grounding": SOURCES})
    with config.profile_context({"CHANNEL_NAME": "River News", "CHANNEL_DESCRIPTION": "Regional reporting.",
                                 "CHANNEL_PRESET": "news", "TARGET_DURATION_MINUTES": 3, "VOICE_RATE": "+0%"}):
        news = scriptwriter.write_script({**RESEARCH, "grounding": SOURCES, "news_evidence": "background_only"})
    assert calm["channel_preset"] == "asmr" and calm["target_duration_seconds"] == 600
    assert news["channel_preset"] == "news" and news["target_duration_seconds"] == 180
    assert "Quiet River" in prompts[0] and "Restful river walks." in prompts[0] and "River News" not in prompts[0]
    assert "River News" in prompts[1] and "Regional reporting." in prompts[1] and "Quiet River" not in prompts[1]
    assert "No urgency, loud exclamations" in prompts[0]
    assert "Do not claim to generate binaural" in prompts[0]
    assert "A trend title is a lead" in prompts[1]
    assert "MANDATORY: Second re-hook" not in " ".join(prompts)
    assert config.CHANNEL_NAME == original


def test_short_word_budget_respects_slow_voice_and_keeps_short_format(monkeypatch):
    monkeypatch.setattr(scriptwriter, "generate", lambda prompt: json.dumps(SCRIPT))
    with config.profile_context({"CHANNEL_PRESET": "asmr", "VOICE_RATE": "-15%", "TARGET_DURATION_MINUTES": 30}):
        calm_budget = scriptwriter._word_budget("shorts")
        result = scriptwriter.write_script(RESEARCH, "shorts")
    with config.profile_context({"CHANNEL_PRESET": "news", "VOICE_RATE": "+0%"}):
        news_budget = scriptwriter._word_budget("shorts")
    assert calm_budget[0] == news_budget[0] == 50
    assert calm_budget[2] < news_budget[2]
    assert result["video_type"] == "shorts" and result["target_duration_seconds"] == 50


def test_script_timing_is_estimated_until_audio_is_measured(monkeypatch):
    monkeypatch.setattr(scriptwriter, "generate", lambda prompt: json.dumps(SCRIPT))
    result = scriptwriter.write_script(RESEARCH)
    assert result["timing_source"] == "estimated_before_recording"
    assert [section["id"] for section in result["sections"]] == [1, 2, 3]
    assert all(0 < section["duration_seconds"] < 20 for section in result["sections"])


def test_empty_narration_is_rejected_before_recording(monkeypatch):
    malformed = deepcopy(SCRIPT)
    malformed["sections"][1]["narration"] = "  "
    monkeypatch.setattr(scriptwriter, "generate", lambda prompt: json.dumps(malformed))
    with pytest.raises(ValueError, match="section 2"):
        scriptwriter.write_script(RESEARCH)


def test_news_exact_topic_keeps_real_sources_and_marks_background(monkeypatch):
    prompts = []
    invented = {**RESEARCH, "topic": "An unrelated story", "grounding": {"sources": [{"url": "https://fake.invalid"}]}}
    monkeypatch.setattr(researcher, "generate", lambda prompt: prompts.append(prompt) or json.dumps(invented))
    monkeypatch.setattr(researcher, "_grounding", lambda topic: deepcopy(SOURCES))
    with config.profile_context({"CHANNEL_PRESET": "news", "TARGET_DURATION_MINUTES": 5}):
        result = researcher.research_topic("Regional reporting", topic_override="The local river", focus_angle="Random science")
    assert result["topic"] == "The local river"
    assert result["grounding"] == SOURCES
    assert result["news_evidence"] == "background_only" and result["warnings"]
    assert "Random science" not in prompts[0]
    assert "5 minutes" in prompts[0]
    assert "not evidence that an event happened" in prompts[0]
    assert "fake.invalid" not in json.dumps(result["grounding"])


def test_automatic_news_has_a_source_informed_second_planning_pass(monkeypatch):
    prompts = []
    monkeypatch.setattr(researcher, "generate", lambda prompt: prompts.append(prompt) or json.dumps(RESEARCH))
    monkeypatch.setattr(researcher, "_grounding", lambda topic: deepcopy(SOURCES))
    with config.profile_context({"CHANNEL_PRESET": "news"}):
        result = researcher.research_topic("Regional reporting", trend_context=["A river headline"])
    assert len(prompts) == 2
    assert SOURCES["facts"] not in prompts[0] and SOURCES["facts"] in prompts[1]
    assert result["grounding"]["sources"][0]["url"] == SOURCES["sources"][0]["url"]


def test_calm_research_uses_channel_niche_without_random_science_topic(monkeypatch):
    prompts = []
    monkeypatch.setattr(researcher, "generate", lambda prompt: prompts.append(prompt) or json.dumps(RESEARCH))
    monkeypatch.setattr(researcher, "_grounding", lambda topic: {"facts": "", "sources": []})
    with config.profile_context({"CHANNEL_PRESET": "asmr", "TARGET_DURATION_MINUTES": 10}):
        researcher.research_topic("Quiet walks beside a river")
    assert len(prompts) == 1
    assert "Quiet walks beside a river" in prompts[0]
    assert "No startling revelations" in prompts[0]
    assert "viral science/tech channel" not in prompts[0]
    assert "The Dark Forest Theory" not in prompts[0]


def test_profile_presets_and_duration_become_effective_config(monkeypatch):
    from studio.state import state
    for key in ("running", "uploading", "visual_editing"):
        monkeypatch.setitem(state, key, False)
    profile = channels.create_profile("Small news bulletin", "news", {"duration_mins": 2.5})
    with config.profile_context(channels.config_overrides(profile["id"])):
        assert config.CHANNEL_PRESET == "news"
        assert scriptwriter._word_budget("normal")[0] == 150


@pytest.mark.parametrize("value", [0, 61, float("nan"), float("inf"), True, "10"])
def test_invalid_global_duration_settings_are_rejected(value):
    assert not config._valid_setting("TARGET_DURATION_MINUTES", value)
