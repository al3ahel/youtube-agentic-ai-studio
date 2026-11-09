"""Regressions for narrated actions and visible subjects in the actual planner."""
from copy import deepcopy

import pytest

import config
from agents import shot_planner, visual_director


@pytest.mark.parametrize("text", [
    "Steel plate armour can deflect some blows away from the body.",
    "The steel breastplate deflects a blow.",
])
def test_unspecified_blows_use_force_directions_without_inventing_a_bullet(text):
    mechanism = shot_planner.mechanism_for(text)
    assert mechanism == {"version": 1, "kind": "armour", "material": "steel", "action": "glance"}
    assert not shot_planner.grounding_issues(mechanism, text)
    assert shot_planner.grounding_issues({**mechanism, "action": "deflect"}, text)


@pytest.mark.parametrize("text", [
    "Could steel plate armour deflect blows?",
    "Steel plate armour cannot deflect blows.",
    "Steel plate armour did not deflect the blow.",
    "Steel plate armour deflects a bullet.",
    "Steel plate armour absorbed heat.",
])
def test_glancing_blow_requires_its_actual_positive_source(text):
    mechanism = {"version": 1, "kind": "armour", "material": "steel", "action": "glance"}
    assert shot_planner.grounding_issues(mechanism, text)


def test_glance_uses_the_spoken_deflection_word_as_its_contact_clock():
    text = "Steel plate armour can deflect some blows."
    scene = {"start": 4, "duration": 3, "mechanism": shot_planner.mechanism_for(text)}
    words = [{"word": "deflect", "start": 5.25, "end": 5.6}]
    assert shot_planner.contact_beat(scene, words, text) == {"time": 1.25, "target": "scene", "action": "highlight"}


@pytest.mark.parametrize("context,excerpt,action", [
    ("We did not see steel armour stop a bullet.", "steel armour stop a bullet.", "absorb"),
    ("Could steel armour stop a bullet, or would it pass through?", "steel armour stop", "absorb"),
    ("Did the steel breastplate deflect the blow, or did it fail?", "steel breastplate deflect the blow,", "glance"),
    ("We did not see steel armour stop a bullet. A steel breastplate did stop one.",
     "steel armour stop a bullet.", "absorb"),
    ("We did not see steel armour stop a bullet. Later we saw steel armour stop a bullet.",
     "steel armour stop a bullet.", "absorb"),
])
def test_cut_cannot_remove_original_negation_or_question_or_borrow_later_positive_action(context, excerpt, action):
    expected = {"version": 1, "kind": "armour", "material": "steel", "action": action}
    inferred = shot_planner.mechanism_for(excerpt, context)
    assert inferred is None or inferred["action"] == "reveal"
    assert shot_planner.grounding_issues(expected, excerpt, context)
    token = "deflect" if action == "glance" else "stop"
    scene = {"start": 0, "duration": 3, "mechanism": expected}
    words = [{"word": token, "start": 1.2, "end": 1.6}]
    assert shot_planner.contact_beat(scene, words, excerpt, context) is None


def test_exact_planner_excerpt_keeps_original_sentence_polarity_through_action_inference():
    text = "We did not see steel armour stop a bullet."
    words = [{"word": word.rstrip("."), "start": index, "end": index + .5, "section_id": "one"}
             for index, word in enumerate(text.split())]
    section = {"id": "one", "start": 0, "duration": 10, "narration": text, "caption_timing": "word"}
    excerpt = visual_director._scene_text(section, 4, 10, [], words)
    assert excerpt == "steel armour stop a bullet."
    inferred = shot_planner.mechanism_for(excerpt, text)
    assert inferred is None or inferred["action"] == "reveal"


@pytest.mark.parametrize("context,excerpt,action,token", [
    ("We saw steel armour stop a bullet.", "steel armour stop a bullet.", "absorb", "stop"),
    ("The first plate failed. Later steel armour deflected a bullet.",
     "steel armour deflected a bullet.", "deflect", "deflected"),
    ("A shaped steel breastplate bounces blows away.",
     "steel breastplate bounces blows away.", "glance", "bounces"),
])
def test_supported_owned_action_survives_context_and_keeps_its_spoken_clock(context, excerpt, action, token):
    inferred = shot_planner.mechanism_for(excerpt, context)
    assert inferred["action"] == action
    assert not shot_planner.grounding_issues(inferred, excerpt, context)
    scene = {"start": 0, "duration": 3, "mechanism": inferred}
    words = [{"word": token, "start": 1.2, "end": 1.6}]
    assert shot_planner.contact_beat(scene, words, excerpt, context)["time"] == 1.2


def test_planner_owns_words_at_the_cut_instead_of_whole_subtitle_chunks():
    text = "Mail armour uses linked rings. Steel plate armour can deflect blows."
    words = [{"word": word.strip("."), "start": i * .3, "end": (i + 1) * .3, "section_id": "one"}
             for i, word in enumerate(text.split())]
    section = {"id": "one", "start": 0, "duration": 4, "narration": text, "caption_timing": "word"}
    captions = [{"start": 0, "end": 4, "text": text}]
    assert visual_director._scene_text(section, 0, 1.5, captions, words) == "Mail armour uses linked rings."
    assert visual_director._scene_text(section, 1.5, 4, captions, words) == "Steel plate armour can deflect blows."


def test_a_single_subject_is_established_before_its_late_spoken_emphasis():
    scene = {"start": 20, "duration": 4, "template": "explain", "items": [{"label": "Armour"}],
             "shot": {"technique": "stage2d"}}
    words = [{"word": "Armour", "start": 23.2, "end": 23.6}]
    beats, timing = visual_director._beats(scene, words, 30)
    reveal = next(row for row in beats if row["target"] == "item-0" and row["action"] == "reveal")
    emphasis = next(row for row in beats if row["target"] == "item-0" and row["action"] == "highlight")
    assert reveal["time"] <= .25
    assert emphasis["time"] >= 3
    assert timing == "mixed"


@pytest.mark.parametrize("proposal", [[], [{"version": 1, "kind": "armour", "material": "steel", "action": "reveal"}]])
def test_model_cannot_replace_narrated_glance_with_an_idle_surface(monkeypatch, proposal):
    monkeypatch.setattr(config, "SHOT_PLANNING_ENABLED", True)
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", False)
    text = "Steel plate armour can deflect some blows away from the body."
    script = {"title": "Shaped steel", "sections": [{"id": "one", "title": "Deflecting a blow", "narration": text}]}
    timeline = {"duration": 4, "fps": 30, "width": 360, "height": 640,
                "sections": [{"id": "one", "start": 0, "duration": 4}], "captions": [], "words": []}
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    scene = plan["scenes"][0]
    assert scene["mechanism"]["action"] == "glance"
    response = {key: deepcopy(scene[key]) for key in
                ("id", "template", "title", "visual_goal", "items", "composition", "staging", "shot")}
    response["mechanisms"] = proposal
    response["shot"]["framing"] = "close"
    journal = {}
    visual_director._apply_design(plan, {"scenes": [response]},
        {scene["id"]: {"section": script["sections"][0], "narration": text}}, provenance=journal)
    assert plan["scenes"][0]["mechanism"]["action"] == "glance"
    assert plan["scenes"][0]["shot"]["framing"] == "close"
    assert journal["scenes"][0]["rejected_fields"]["mechanism"] == "omitted_narrated_interaction"
