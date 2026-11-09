"""New shots preserve real speech, trusted assets and source-grounded outcomes."""
import copy

import pytest
from PIL import Image

import config
from agents import shot_planner, visual_director


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(config, "SHOT_PLANNING_ENABLED", True)
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", False)
    monkeypatch.setattr(config, "CHANNEL_PRESET", "general")
    monkeypatch.setattr(config, "PIXABAY_API_KEY", "")
    monkeypatch.setattr(config, "PEXELS_API_KEY", "")


def source():
    text = "A steel breastplate meets an incoming projectile. Its curved surface can deflect an impact. The outcome depends on thickness and range."
    script = {"title": "Armour and impact", "video_type": "shorts", "sections": [{"id": "plate", "title": "A steel breastplate", "narration": text}]}
    timeline = {"width": 1080, "height": 1920, "fps": 30, "duration": 12.1,
                "sections": [{"id": "plate", "start": 0, "duration": 12.1, "narration": text, "caption_timing": "word"}],
                "captions": [{"start": 0, "end": 4, "text": "A steel breastplate meets an incoming projectile."},
                             {"start": 4, "end": 8, "text": "Its curved surface can deflect an impact."},
                             {"start": 8, "end": 12.1, "text": "The outcome depends on thickness and range."}],
                "words": [{"word": "impact.", "start": 7.5, "end": 8, "section_id": "plate"}]}
    return script, timeline


def test_real_speech_clock_is_unchanged_while_section_becomes_shots():
    script, timeline = source()
    untouched = copy.deepcopy((script, timeline))
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    assert (script, timeline) == untouched
    assert len(plan["scenes"]) == 3
    assert [scene["start"] for scene in plan["scenes"]] == [0, 4, 8]
    assert [scene["shot"]["role"] for scene in plan["scenes"]] == ["establish", "detail", "payoff"]
    assert plan["captions"] == timeline["captions"]
    assert plan["scenes"][-1]["start"] + plan["scenes"][-1]["duration"] == pytest.approx(12.1)
    assert plan["scenes"][1]["mechanism"]["action"] == "deflect"
    assert plan["scenes"][1]["shot"]["technique"] == "diagram3d"
    assert len({scene["shot"]["framing"] for scene in plan["scenes"]}) >= 2


def test_short_sections_never_get_zero_or_overlapping_shots():
    section = {"id": "s", "start": .1, "duration": .05}
    plan = {"fps": 30, "captions": [], "words": []}
    assert shot_planner.cut_boundaries(section, 12, plan) == [.1, .15]


def test_calm_profile_changes_rhythm_without_fabricating_speech():
    sections = [{"duration": 21}]
    assert shot_planner.shot_counts(sections, calm=True)[0] < shot_planner.shot_counts(sections)[0]
    assert sum(shot_planner.shot_counts([{"duration": 900}] * 80)) <= 120


def test_mechanisms_require_supported_materials_and_outcomes():
    assert shot_planner.mechanism_for("Bronze armour was worn in antiquity.") is None
    assert shot_planner.mechanism_for("Financial layers of investment.") is None
    assert shot_planner.mechanism_for("The sun reached the solar panel.", "Steel armour deflected a bullet. The sun reached the solar panel.") is None
    assert shot_planner.mechanism_for("Postal mail was sent in layers of fiber packaging.") is None
    assert shot_planner.mechanism_for("The fiber optic cable has layers.") is None
    assert shot_planner.mechanism_for("Mail uses linked rings.")["material"] == "mail"
    assert shot_planner.mechanism_for("When firearms spread, steel armour became thicker.")["action"] == "reveal"
    assert shot_planner.mechanism_for("Could steel armour stop a bullet?")["action"] == "reveal"
    assert shot_planner.grounding_issues({"version": 1, "kind": "armour", "material": "steel", "action": "absorb"},
                                       "Could steel armour stop a bullet?")
    assert shot_planner.mechanism_for("No mail was used. Steel armour deflected the bullet.")["material"] == "steel"
    scene = {"version": 1, "kind": "armour", "material": "steel", "action": "penetrate"}
    assert shot_planner.grounding_issues(scene, "The projectile cannot penetrate the steel breastplate.")
    assert not shot_planner.grounding_issues({**scene, "action": "deflect"}, "The steel breastplate can deflect the projectile.")
    assert shot_planner.grounding_issues({**scene, "material": "ceramic"}, "The projectile penetrates the steel breastplate.")
    two_subjects = "Mail uses linked rings. A curved steel plate can deflect a projectile."
    assert shot_planner.mechanism_for(two_subjects) == {"version": 1, "kind": "armour", "material": "steel", "action": "deflect"}
    assert shot_planner.grounding_issues({**scene, "material": "mail", "action": "deflect"}, two_subjects)
    assert shot_planner.mechanism_for("Steel armour has a reinforcing layer.")["kind"] == "layers"
    assert shot_planner.mechanism_for("Ceramic and fiber armour layers absorb a hit.") is None
    assert shot_planner.mechanism_for("Bronze armour was worn.", "Steel plate was also known.") is None
    assert shot_planner.mechanism_for("Protective ceramic layers absorb heat.")["action"] == "separate"
    assert shot_planner.mechanism_for("Fiber layers protect the skin and absorb moisture.")["action"] == "separate"
    assert shot_planner.grounding_issues({**scene, "kind": "layers", "material": "ceramic", "action": "absorb"},
                                       "Protective ceramic layers absorb heat.")
    assert shot_planner.mechanism_for("Mail armour has several layers.")["kind"] == "armour"


def test_numeric_chart_does_not_claim_an_unused_3d_mechanism():
    scene = {"id": "chart", "template": "chart", "title": "Measured impacts"}
    narration = "A steel breastplate meets a projectile impact."
    shot_planner.direct_shot(scene, narration, {"narration": narration}, 0, 2, global_index=1)
    assert scene["shot"]["technique"] == "stage2d" and "mechanism" not in scene


def test_collage_reuses_distinct_real_section_pictures(tmp_path):
    script, timeline = source()
    a, b, duplicate = (tmp_path / name for name in ("one.png", "two.png", "copy.png"))
    Image.new("RGB", (60, 60), "red").save(a)
    Image.new("RGB", (60, 60), "blue").save(b)
    duplicate.write_bytes(a.read_bytes())
    plan = visual_director.plan_scenes(script, timeline, mode="hybrid", image_map={"plate": [str(a), str(duplicate), str(b)]}, output_dir=tmp_path)
    for scene in plan["scenes"]:
        assert scene["shot"]["technique"] == "collage"
        assert len(scene["media_layers"]) == 2
        assert {visual_director._image_digest(layer["path"]) for layer in scene["media_layers"]} == {
            visual_director._image_digest(str(a)), visual_director._image_digest(str(b))}
        assert "mechanism" not in scene
    other = visual_director.plan_scenes(script, timeline, mode="hybrid", image_map={"plate": [str(a)]}, output_dir=tmp_path)
    assert all(scene["shot"]["technique"] == "stage2d" for scene in other["scenes"])
    assert all("media_layers" not in scene for scene in other["scenes"])


@pytest.mark.parametrize("framing", ["close", "macro"])
def test_detail_collage_edit_projection_ignores_only_unrendered_photo_identities(tmp_path, framing):
    script, timeline = source()
    images = []
    for index, colour in enumerate(("red", "blue", "green")):
        path = tmp_path / f"photo-{index}.png"
        Image.new("RGB", (60, 60), colour).save(path)
        images.append(str(path))
    plan = visual_director.plan_scenes(script, timeline, mode="hybrid", image_map={"plate": images}, output_dir=tmp_path)
    scene = plan["scenes"][0]
    scene["shot"]["framing"] = framing
    before = visual_director._visible_design(scene)
    hidden_edit = copy.deepcopy(scene)
    hidden_edit["media_layers"][2]["credit"] = "A different unused third photograph"
    assert visual_director._visible_design(hidden_edit) == before
    visible_edit = copy.deepcopy(scene)
    visible_edit["media_layers"][1]["credit"] = "A different visible second photograph"
    assert visual_director._visible_design(visible_edit) != before
    scene["shot"]["framing"] = "wide"
    hidden_edit["shot"]["framing"] = "wide"
    assert visual_director._visible_design(hidden_edit) != visual_director._visible_design(scene)


def test_graphics_only_never_adopts_media_or_remote_paths(tmp_path):
    script, timeline = source()
    plan = visual_director.plan_scenes(script, timeline, image_map={"plate": ["https://example.com/image.png"]}, output_dir=tmp_path)
    assert all(not scene.get("image") for scene in plan["scenes"])
    bad = copy.deepcopy(plan)
    bad["scenes"][0]["shot"]["technique"] = "diagram3d"
    bad["scenes"][0].pop("mechanism")
    with pytest.raises(ValueError, match="mechanism"):
        visual_director.validate_plan(bad)


def test_single_shot_edit_preserves_other_shots_and_narration():
    script, timeline = source()
    plan = visual_director.plan_scenes(script, timeline)
    updated = visual_director.regenerate_scene(plan, "scene-001", script, "closer framing")
    assert updated["scenes"][0]["shot"]["framing"] == "close"
    assert updated["scenes"][1:] == plan["scenes"][1:]
    assert updated["sections"] == plan["sections"] and updated["captions"] == plan["captions"]
    assert updated["duration"] == plan["duration"]


def test_planning_prose_is_not_reported_as_a_visible_edit(monkeypatch):
    script, timeline = source()
    plan = visual_director.plan_scenes(script, timeline)
    original = plan["scenes"][0]
    response = {key: copy.deepcopy(original[key]) for key in
                ("id", "template", "title", "visual_goal", "items", "composition", "staging", "shot")}
    response["mechanisms"] = [copy.deepcopy(original["mechanism"])]
    response["shot"].update(focus="A clearer view of the breastplate", action="Inspect the narrated steel surface.", transition="match")
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", True)
    monkeypatch.setattr(visual_director, "_visual_ai_available", lambda: True)
    monkeypatch.setattr(visual_director, "_request_design", lambda *args, **kwargs: {"scenes": [response]})
    with pytest.raises(ValueError, match="visible design is unchanged"):
        visual_director.regenerate_scene(plan, original["id"], script, "Clarify the intention of this view")


@pytest.mark.parametrize("technique", ["stage2d", "diagram3d"])
def test_material_layout_only_edit_is_rejected_without_changing_the_plan(technique):
    script, timeline = source()
    plan = visual_director.plan_scenes(script, timeline)
    original = plan["scenes"][0]
    original["shot"]["technique"] = technique
    assert original["composition"] == "split"
    saved = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="visible design is unchanged"):
        visual_director.regenerate_scene(plan, original["id"], script, "stack composition")
    assert plan == saved


@pytest.mark.parametrize("instruction,field,value", [
    ("closer framing", "framing", "close"),
    ("stack composition and closer framing", "framing", "close"),
    ("track camera", "camera", "track"),
    ("3D", "technique", "diagram3d"),
    ("warmer colors", "palette", "ember"),
    ("brisk rhythm", "rhythm", "brisk"),
    ("poster look", "look", "poster"),
    ("editorial look", "look", "editorial"),
])
def test_material_edits_keep_real_camera_geometry_and_text_treatment(instruction, field, value):
    script, timeline = source()
    plan = visual_director.plan_scenes(script, timeline)
    original = plan["scenes"][0]
    original.update(look="studio", rhythm="measured", palette="ocean")
    original["shot"].update(framing="wide", camera="locked", technique="stage2d")
    saved = copy.deepcopy(plan)
    result = visual_director.regenerate_scene(plan, original["id"], script, instruction)
    edited = result["scenes"][0]
    assert (edited["shot"][field] if field in {"framing", "camera", "technique"} else edited[field]) == value
    assert result["last_edit"]["changes"]
    assert not any(change.startswith("Composition:") for change in result["last_edit"]["changes"])
    assert result["scenes"][1:] == saved["scenes"][1:]
    assert result["sections"] == saved["sections"] and result["captions"] == saved["captions"]
    assert plan == saved


def test_equivalent_material_typeface_is_not_a_visible_style_edit():
    script, timeline = source()
    plan = visual_director.plan_scenes(script, timeline)
    plan["scenes"][0]["look"] = "editorial"
    with pytest.raises(ValueError, match="visible design is unchanged"):
        visual_director.regenerate_scene(plan, "scene-001", script, "botanical look")


@pytest.mark.parametrize("closer", [False, True])
def test_material_edit_does_not_claim_unused_actor_or_label_changes(monkeypatch, closer):
    script, timeline = source()
    plan = visual_director.plan_scenes(script, timeline)
    original = plan["scenes"][0]
    response = {key: copy.deepcopy(original[key]) for key in
                ("id", "template", "title", "visual_goal", "items", "composition", "staging", "shot")}
    response["mechanisms"] = [copy.deepcopy(original["mechanism"])]
    response["items"] = [{"label": "Steel plate", "symbol": "document", "role": "subject"}]
    response["staging"]["action"] = "assemble"
    if closer:
        response["shot"]["framing"] = "close"
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", True)
    monkeypatch.setattr(visual_director, "_visual_ai_available", lambda: True)
    monkeypatch.setattr(visual_director, "_request_design", lambda *args, **kwargs: {"scenes": [response]})
    if not closer:
        with pytest.raises(ValueError, match="visible design is unchanged"):
            visual_director.regenerate_scene(plan, original["id"], script, "Clarify this view")
    else:
        result = visual_director.regenerate_scene(plan, original["id"], script, "Clarify this view")
        assert result["scenes"][0]["shot"]["framing"] == "close"
        assert result["scenes"][0]["items"] == response["items"]
        assert result["last_edit"]["changes"] == ["Updated the shot framing, technique or camera direction."]


@pytest.mark.parametrize("technique", ["stage2d", "diagram3d"])
def test_material_edit_keeps_visible_layer_labels(monkeypatch, technique):
    text = "Steel armour has a reinforcing layer."
    script = {"title": "Reinforcing armour", "sections": [{"id": "plate", "title": "Steel layers", "narration": text}]}
    timeline = {"width": 1080, "height": 1920, "fps": 30, "duration": 4,
                "sections": [{"id": "plate", "start": 0, "duration": 4}], "captions": [], "words": []}
    plan = visual_director.plan_scenes(script, timeline)
    original = plan["scenes"][0]
    assert original["mechanism"]["kind"] == "layers" and original["mechanism"]["action"] == "separate"
    original["shot"]["technique"] = technique
    response = {key: copy.deepcopy(original[key]) for key in
                ("id", "template", "title", "visual_goal", "items", "composition", "staging", "shot")}
    response["mechanisms"] = [copy.deepcopy(original["mechanism"])]
    response["items"] = [{"label": "Steel", "symbol": "concept", "role": "subject"},
                         {"label": "Reinforcement", "symbol": "concept", "role": "support"}]
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", True)
    monkeypatch.setattr(visual_director, "_visual_ai_available", lambda: True)
    monkeypatch.setattr(visual_director, "_request_design", lambda *args, **kwargs: {"scenes": [response]})
    result = visual_director.regenerate_scene(plan, original["id"], script, "Clarify these layer labels")
    assert result["scenes"][0]["items"] == response["items"]
    assert result["last_edit"]["changes"] == ["Updated the on-screen labels."]


def test_legacy_plan_stays_legacy_until_explicit_rebuild(monkeypatch):
    script, timeline = source()
    monkeypatch.setattr(config, "SHOT_PLANNING_ENABLED", False)
    plan = visual_director.plan_scenes(script, timeline)
    assert all("shot" not in scene for scene in plan["scenes"])
    monkeypatch.setattr(config, "SHOT_PLANNING_ENABLED", True)
    assert visual_director.validate_plan(plan) == plan
    updated = visual_director.regenerate_scene(plan, "scene-001", script, "closer camera")
    assert all("shot" not in scene for scene in updated["scenes"])


def test_contact_sound_uses_real_word_time_without_invented_timestamp():
    from agents.audio_director import _event_start
    _, timeline = source()
    timeline["timing_source"] = "measured_sections"
    event = {"section_id": "plate", "cue_word": "impact"}
    assert _event_start(event, timeline) == 7.5
    scene = {"start": 4, "duration": 4, "mechanism": {"action": "impact"}}
    assert shot_planner.contact_beat(scene, timeline["words"])["time"] == 3.5
    with pytest.raises(ValueError, match="unique measured"):
        _event_start({**event, "cue_word": "explosion"}, timeline)
    timeline["timing_source"] = "estimated"
    with pytest.raises(ValueError, match="measured word"):
        _event_start(event, timeline)


def test_contact_anchor_rejects_negative_and_ambiguous_spoken_matches():
    scene = {"start": 0, "duration": 8, "mechanism": {"action": "absorb"}}
    text = "The thin steel plate did not stop it. The thick steel plate stopped it."
    words = [{"word": "stop", "start": 2, "end": 2.2}, {"word": "stopped", "start": 6, "end": 6.3}]
    assert shot_planner.contact_beat(scene, words, text)["time"] == 6
    text = "The steel plate cannot stop it."
    assert shot_planner.contact_beat(scene, words[:1], text) is None
    repeated = [{"word": "hit", "start": 2, "end": 2.2}, {"word": "hit", "start": 6, "end": 6.3}]
    assert shot_planner.contact_beat({**scene, "mechanism": {"action": "impact"}}, repeated,
                                   "The steel plate was hit. It was hit again.") is None
