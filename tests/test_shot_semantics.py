"""Regressions for narrated actions and visible subjects in the actual planner."""
from copy import deepcopy

import pytest
from PIL import Image

import config
from agents import object_direction, shot_planner, visual_director


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


def pair_source():
    text = "One test showed a limited result. Armour and firearms evolved together for centuries."
    script = {"title": "Protection had limits", "sections": [
        {"id": "answer", "title": "Protection had limits", "narration": text}]}
    words = [{"word": word.rstrip("."), "start": index * .5, "end": index * .5 + .4, "section_id": "answer"}
             for index, word in enumerate(text.split())]
    timeline = {"duration": 8, "fps": 30, "width": 1080, "height": 1920,
                "sections": [{"id": "answer", "start": 0, "duration": 8, "narration": text, "caption_timing": "word"}],
                "captions": [{"start": 0, "end": 4, "text": "One test showed a limited result. Armour and"},
                             {"start": 4, "end": 8, "text": "firearms evolved together for centuries."}], "words": words}
    return script, timeline


def test_cut_pair_retains_both_subjects_in_payoff_without_changing_speech(monkeypatch):
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", False)
    monkeypatch.setattr(config, "SHOT_PLANNING_ENABLED", True)
    script, timeline = pair_source()
    saved = deepcopy((script, timeline))
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    assert (script, timeline) == saved
    assert len(plan["scenes"]) == 2
    first, last = plan["scenes"]
    assert last["title"] != first["title"]
    assert [item["symbol"] for item in last["items"]] == ["armour", "firearm"]
    assert last["template"] == "comparison" and last["staging"]["action"] == "compare"
    assert "mechanism" not in last
    excerpt = visual_director._scene_text(plan["sections"][0], last["start"], 8, plan["captions"], plan["words"])
    assert excerpt.startswith("firearms") and "Armour" not in excerpt
    assert not object_direction.grounding_issues(last, excerpt, context=script["sections"][0]["narration"])
    assert plan["captions"] == timeline["captions"] and plan["words"] == timeline["words"]


def test_comparison_editor_can_repair_a_saved_lone_subject_without_retiming(monkeypatch):
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", False)
    monkeypatch.setattr(config, "SHOT_PLANNING_ENABLED", True)
    script, timeline = pair_source()
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    last = plan["scenes"][-1]
    last.update(template="explain", title="Protection had limits",
                items=[{"label": "Firearms", "symbol": "firearm", "role": "subject"}])
    last["staging"]["action"] = "reveal"
    last["beats"], last["beat_timing"] = visual_director._beats(last, plan["words"], plan["fps"], plan["captions"])
    saved = deepcopy(plan)
    edited = visual_director.regenerate_scene(plan, last["id"], script, "Use a comparison, medium framing and locked camera")
    assert plan == saved and edited["scenes"][0] == saved["scenes"][0]
    assert [item["symbol"] for item in edited["scenes"][-1]["items"]] == ["armour", "firearm"]
    assert all(edited[field] == saved[field] for field in ("sections", "captions", "words", "duration", "fps"))
    assert edited["scenes"][-1]["start"] == last["start"]
    assert edited["scenes"][-1]["duration"] == last["duration"]


@pytest.mark.parametrize("context,excerpt", [
    ("Armour was displayed. Firearms evolved together for centuries.", "Firearms evolved together for centuries."),
    ("Armour and firearms evolved together. Armour and firearms evolved together.", "firearms evolved together."),
    ("Armour and firearms evolved together.", "Armour and"),
    ("There was no armour and firearms evolved together.", "firearms evolved together."),
    ("Armour and firearms never evolved together.", "evolved together."),
])
def test_context_pair_does_not_borrow_distant_ambiguous_future_or_negated_subjects(context, excerpt):
    assert object_direction.paired_subjects(excerpt, context) == []


@pytest.mark.parametrize("context,excerpt", [
    ("Armour and firearms were displayed; rivers flow together.", "rivers flow together."),
    ("Armour and firearms were displayed;rivers flow together.", "rivers flow together."),
    ("Armour and firearms were displayed while the water flows together.", "the water flows together."),
    ("Armour and firearms were displayed whereas the water flows together.", "the water flows together."),
    ("Armour and firearms were displayed and rivers flow together.", "rivers flow together."),
    ("Armour and firearms were displayed, and rivers flow together.", "rivers flow together."),
    ("Armour and firearms were displayed, rivers flow together.", "rivers flow together."),
    ("Armour and firearms were displayed, then the rivers flowed together.", "then the rivers flowed together."),
    ("Armour and firearms were displayed and two cats ran together.", "two cats ran together."),
    ("Armour and firearms were displayed with visitors walking together.", "visitors walking together."),
    ("Solar panels and batteries were connected, then rivers flowed together.", "then rivers flowed together."),
    ("The answer depends on the armour and the weapon and the weather.", "depends on the armour and the weapon and the weather."),
])
def test_pair_relationship_cannot_borrow_another_clauses_subjects(context, excerpt):
    assert object_direction.paired_subjects(excerpt, context) == []
    # The same unrelated relation must not become evidence without a cut or
    # context argument either; both entry paths share the grounding rule.
    assert object_direction.paired_subjects(context) == []


@pytest.mark.parametrize("context,excerpt,expected", [
    ("Armour and firearms evolved together for centuries; protection always had limits.",
     "firearms evolved together for centuries; protection always had limits.", ["armour", "firearm"]),
    ("Solar panels and batteries were connected, then evolved together.",
     "then evolved together.", ["solar", "battery"]),
    ("The answer depends on the armour and the weapon; one test has limits.",
     "depends on the armour and the weapon", ["armour", "concept"]),
])
def test_owned_pair_clause_preserves_unique_cuts_and_direct_dependencies(context, excerpt, expected):
    assert [item["symbol"] for item in object_direction.paired_subjects(excerpt, context)] == expected


def test_unrelated_clause_cannot_rebuild_a_water_scene_as_armour_comparison():
    context = "Armour and firearms were displayed; rivers flow together."
    excerpt = "rivers flow together."
    scene = {"template": "explain", "title": "Flowing rivers", "items": [
        {"label": "Rivers", "symbol": "water", "role": "subject"}],
        "staging": {"version": 1, "world": "generic", "action": "reveal", "camera": "locked"}}
    before = deepcopy(scene)
    assert not shot_planner.apply_paired_comparison(scene, excerpt, context)
    assert scene == before
    borrowed = {**scene, "template": "comparison", "items": [
        {"label": "Armour", "symbol": "armour", "role": "subject"},
        {"label": "Firearms", "symbol": "firearm", "role": "support"}]}
    borrowed["staging"] = {**scene["staging"], "action": "compare"}
    assert "A depicted actor is not supported by this scene's narration." in object_direction.grounding_issues(
        borrowed, excerpt, context=context)


def test_context_pair_supplies_names_but_not_an_earlier_connection_action():
    context = "Solar panels and batteries were connected, then evolved together."
    excerpt = "then evolved together."
    actors = object_direction.paired_subjects(excerpt, context)
    assert [item["symbol"] for item in actors] == ["solar", "battery"]
    scene = {"template": "explain", "items": actors,
             "staging": {"version": 1, "world": "generic", "action": "connect", "camera": "locked"}}
    assert object_direction.grounding_issues(scene, excerpt, context=context) == [
        "The selected action is not supported by this scene's narration."]


def test_ambiguous_weapon_changes_later_heading_without_inventing_a_firearm_or_cube_pair():
    text = "The answer depends on the armour and the weapon."
    scene = {"template": "explain", "title": "Could armour stop a bullet?",
             "items": [{"label": "Armour", "symbol": "armour", "role": "subject"}]}
    shot_planner.direct_shot(scene, text, {"narration": text}, 1, 2, global_index=1)
    assert scene["title"] == "Armour and weapon"
    assert scene["template"] == "explain"
    assert scene["items"] == [{"label": "Armour", "symbol": "armour", "role": "subject"}]
    assert "mechanism" not in scene


def test_paired_names_do_not_replace_an_owned_physical_interaction():
    text = "Solar panels and batteries work together to power a circuit."
    scene = {"template": "explain", "title": "Power flows", "items": [
        {"label": "Solar panels", "symbol": "solar", "role": "subject"},
        {"label": "Batteries", "symbol": "battery", "role": "support"},
        {"label": "Circuit", "symbol": "circuit", "role": "support"}],
        "staging": {"version": 1, "world": "generic", "action": "connect", "camera": "follow"}}
    saved = deepcopy(scene)
    assert not shot_planner.apply_paired_comparison(scene, text, text)
    assert scene == saved


def test_hybrid_keeps_concrete_pair_payoff_instead_of_repeating_its_section_photos(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", False)
    monkeypatch.setattr(config, "SHOT_PLANNING_ENABLED", True)
    script, timeline = pair_source()
    pictures = [tmp_path / "armour.png", tmp_path / "firearm.png"]
    for path, colour in zip(pictures, ("red", "blue")):
        Image.new("RGB", (60, 60), colour).save(path)
    plan = visual_director.plan_scenes(script, timeline, mode="hybrid", use_ai=False,
                                     image_map={"answer": [str(path) for path in pictures]}, output_dir=tmp_path)
    first, last = plan["scenes"]
    assert first["shot"]["technique"] == "collage" and len(first["media_layers"]) == 2
    assert last["template"] == "comparison" and last["shot"]["technique"] == "stage2d"
    assert "image" not in last and "media_layers" not in last
    assert plan["image_status"]["scenes"] == 1
