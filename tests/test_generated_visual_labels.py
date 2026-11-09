"""Generated words must describe their source and the geometry that is drawn."""
from copy import deepcopy
import json

import pytest
from PIL import Image

import config
from agents import object_direction, shot_planner, visual_director


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(config, "SHOT_PLANNING_ENABLED", True)
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", False)
    monkeypatch.setattr(config, "CHANNEL_PRESET", "general")
    monkeypatch.setattr(config, "PEXELS_API_KEY", "")
    monkeypatch.setattr(config, "PIXABAY_API_KEY", "")


def source(text, title="A source detail", duration=4):
    script = {"title": title, "sections": [{"id": "source", "title": title, "narration": text}]}
    timeline = {"width": 1080, "height": 1920, "fps": 30, "duration": duration,
                "sections": [{"id": "source", "start": 0, "duration": duration}], "words": [], "captions": []}
    return script, timeline


def proposal(scene):
    row = {key: deepcopy(scene[key]) for key in
           ("id", "template", "title", "visual_goal", "items", "composition", "staging", "shot")}
    row["mechanisms"] = [deepcopy(scene["mechanism"])] if scene.get("mechanism") else []
    return row


@pytest.mark.parametrize("symbol,label,narration,diagnostic", [
    ("armour", "Linked mail rings", "Mail armour uses linked rings.", "literal catalogue depiction"),
    ("firearm", "Early pistol", "An early pistol is a firearm.", "literal catalogue depiction"),
    ("battery", "Solar panels", "Solar panels feed batteries.", "literal catalogue depiction"),
    ("solar", "Wind turbine", "Solar panels and a wind turbine supply energy.", "literal catalogue depiction"),
    ("battery", "Lithium battery", "Batteries store energy.", "name or qualifier"),
    ("building", "Coastal school", "Buildings shelter people.", "name or qualifier"),
])
def test_label_needs_depiction_and_source_even_when_the_symbol_is_supported(symbol, label, narration, diagnostic):
    scene = {"template": "explain", "items": [{"label": label, "symbol": symbol, "role": "subject"}]}
    assert object_direction.symbol_supported(symbol, narration)
    assert any(diagnostic in issue for issue in object_direction.label_grounding_issues(scene, narration))


@pytest.mark.parametrize("symbol,label,narration", [
    ("armour", "Steel breastplate", "A steel breastplate covered the torso."),
    ("firearm", "Firearms", "Firearms developed over time."),
    ("battery", "Rechargeable battery", "Rechargeable batteries store energy."),
    ("solar", "Solar panels", "Solar panels produce electricity."),
    ("concept", "Battery costs", "Battery costs vary."),
])
def test_supported_label_variants_and_abstractions_remain_available(symbol, label, narration):
    scene = {"template": "explain", "items": [{"label": label, "symbol": symbol, "role": "subject"}]}
    assert not object_direction.label_grounding_issues(scene, narration)


def test_label_context_supplies_only_the_recovered_pair_identity():
    context = "Mail armour uses linked rings. Armour and firearms evolved together."
    narration = "firearms evolved together."
    scene = {"template": "comparison", "items": [
        {"label": "Armour", "symbol": "armour", "role": "subject"},
        {"label": "Firearms", "symbol": "firearm", "role": "support"}]}
    assert not object_direction.label_grounding_issues(scene, narration, context=context)
    scene["items"][0]["label"] = "Linked mail rings"
    assert object_direction.label_grounding_issues(scene, narration, context=context)


def test_material_part_labels_use_the_drawn_layers_and_source_not_dormant_actor_symbols():
    scene = {"template": "explain", "mechanism": {
        "version": 1, "kind": "layers", "material": "steel", "action": "separate"},
        "items": [{"label": "exposed inner section", "symbol": "building", "role": "subject"}]}
    narration = "The armour was thickened, or given a reinforcing layer over the breastplate."
    assert not object_direction.label_grounding_issues(scene, narration,
        visible_labels=["thickened outer plate", "exposed inner section"])
    assert object_direction.label_grounding_issues(scene, narration,
        visible_labels=["insulating cavity", "exposed inner section"])


@pytest.mark.parametrize("mode", ["motion", "hybrid"])
@pytest.mark.parametrize("text,bad_labels", [
    ("Armour and firearms evolved together.", ["Linked mail rings", "Early pistol"]),
    ("Solar panels feed batteries.", ["Wind turbine", "Lithium battery"]),
])
def test_normal_model_application_rejects_bad_visible_labels_without_crediting_a_repair(mode, text, bad_labels):
    script, timeline = source(text)
    plan = visual_director.plan_scenes(script, timeline, mode=mode, use_ai=False)
    old = deepcopy(plan["scenes"][0])
    row = proposal(old)
    assert len(row["items"]) == len(bad_labels)
    for item, label in zip(row["items"], bad_labels):
        item["label"] = label
    row["shot"]["framing"] = "close"
    journal, directed = {}, set()
    visual_director._apply_design(plan, {"scenes": [row]},
        {old["id"]: {"section": script["sections"][0], "narration": text}},
        provenance=journal, directed=directed)
    assert plan["scenes"][0] == old and not directed
    assert journal["scenes"][0]["status"] == "rejected"
    assert journal["scenes"][0]["reason"] == "unsupported_displayed_labels"
    assert journal["scenes"][0]["changes"] == []


@pytest.mark.parametrize("title,text,material,valid", [
    ("Layered Defense Gap", "Steel armour has a reinforcing layer.", "steel", False),
    ("Insulating cavity", "Ceramic armour has a protective outer layer.", "ceramic", False),
    ("Reinforcing layer", "Steel armour has a reinforcing layer.", "steel", True),
    ("Hardening the Steel", "Steel armour was hardened.", "steel", True),
    ("Thickening the Steel", "Steel armour was hardened.", "steel", False),
    ("Steel layers", "Ceramic armour has a protective layer.", "ceramic", False),
    ("Air gap", "Steel armour has an air gap between layers.", "steel", True),
    ("No air gap", "Steel armour has no air gap between layers.", "steel", True),
])
def test_material_headings_require_source_features_and_keep_supported_processes(title, text, material, valid):
    mechanism = {"version": 1, "kind": "layers", "material": material, "action": "separate"}
    assert bool(shot_planner.title_grounding_issues(title, mechanism, text)) is not valid


def test_heading_cannot_borrow_a_future_feature_or_lose_negation_at_a_cut():
    mechanism = {"version": 1, "kind": "layers", "material": "steel", "action": "separate"}
    context = "Steel armour has a reinforcing layer. A later design includes an air gap."
    assert shot_planner.title_grounding_issues("Air gap", mechanism, "Steel armour has a reinforcing layer.", context)
    context = "The steel armour does not contain an air gap."
    assert shot_planner.title_grounding_issues("Air gap", mechanism, "an air gap.", context)


@pytest.mark.parametrize("mode", ["motion", "hybrid"])
def test_model_exploded_view_does_not_justify_a_new_gap_heading(mode):
    text = "Steel armour has a reinforcing layer."
    script, timeline = source(text, "Reinforcing layer")
    plan = visual_director.plan_scenes(script, timeline, mode=mode, use_ai=False)
    old = deepcopy(plan["scenes"][0])
    row = proposal(old)
    row["title"] = "Layered Defense Gap"
    row["items"] = [{"label": "Steel", "symbol": "concept", "role": "subject"},
                    {"label": "Reinforcement", "symbol": "concept", "role": "support"}]
    journal = {}
    visual_director._apply_design(plan, {"scenes": [row]},
        {old["id"]: {"section": script["sections"][0], "narration": text}}, provenance=journal)
    assert plan["scenes"][0] == old
    assert journal["scenes"][0]["reason"] == "unsupported_source_heading"


@pytest.mark.parametrize("mode", ["motion", "hybrid"])
def test_normal_model_application_keeps_grounded_hardening_and_material(mode):
    text = "Steel armour was hardened."
    script, timeline = source(text, "Steel armour")
    plan = visual_director.plan_scenes(script, timeline, mode=mode, use_ai=False)
    old = deepcopy(plan["scenes"][0])
    row = proposal(old)
    row["title"] = "Hardening the Steel"
    row["shot"]["framing"] = "close"
    journal = {}
    visual_director._apply_design(plan, {"scenes": [row]},
        {old["id"]: {"section": script["sections"][0], "narration": text}}, provenance=journal)
    assert journal["scenes"][0]["status"] == "accepted"
    assert plan["scenes"][0]["title"] == "Hardening the Steel"
    assert plan["scenes"][0]["mechanism"] == old["mechanism"]


@pytest.mark.parametrize("mode", ["motion", "hybrid"])
def test_normal_editor_repairs_existing_pair_labels_from_source_without_retiming(mode):
    script, timeline = source("Armour and firearms evolved together.")
    plan = visual_director.plan_scenes(script, timeline, mode=mode, use_ai=False)
    old = plan["scenes"][0]
    old["items"][0]["label"], old["items"][1]["label"] = "Linked mail rings", "Early pistol"
    saved = deepcopy(plan)
    result = visual_director.regenerate_scene(plan, old["id"], script,
        'Use a comparison, medium framing and locked camera. Set heading to "Evolved together"')
    assert [i["label"] for i in result["scenes"][0]["items"]] == ["Armour", "Firearms"]
    assert result["scenes"][0]["title"] == "Evolved together"
    assert result["last_edit"]["status"] == "local"
    assert plan == saved
    for key in ("sections", "captions", "words", "duration", "fps"):
        assert result[key] == saved[key]


@pytest.mark.parametrize("mode", ["motion", "hybrid"])
def test_quoted_heading_and_3d_detail_are_normal_edits_not_rejected_model_repairs(mode, monkeypatch):
    text = "Steel armour has a reinforcing layer."
    script, timeline = source(text, "Reinforcing layer")
    plan = visual_director.plan_scenes(script, timeline, mode=mode, use_ai=False)
    old = plan["scenes"][0]
    old["title"] = "Layered Defense Gap"
    old["shot"]["technique"] = "stage2d"
    saved = deepcopy(plan)
    bad = proposal(old)
    bad["items"] = [{"label": "Air cavity", "symbol": "concept", "role": "subject"}]
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", True)
    monkeypatch.setattr(visual_director, "_visual_ai_available", lambda: True)
    monkeypatch.setattr(visual_director, "_request_design", lambda *a, **k: {"scenes": [bad]})
    result = visual_director.regenerate_scene(plan, old["id"], script,
        'Set heading to "Reinforcing layer". Use 3D, close-up and locked camera.')
    scene = result["scenes"][0]
    assert scene["title"] == "Reinforcing layer"
    assert scene["shot"]["technique"] == "diagram3d" and scene["shot"]["framing"] == "close"
    assert scene["items"] == old["items"] and scene["mechanism"] == old["mechanism"]
    assert result["last_edit"]["status"] == "local" and plan == saved
    with pytest.raises(ValueError, match="heading is not source-grounded"):
        visual_director.regenerate_scene(plan, old["id"], script, 'Set heading to "An insulating cavity"')


def test_quoted_heading_preserves_case_and_does_not_change_direction_or_follow_negation():
    script, timeline = source("A battery stores energy.", "Battery")
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    old = deepcopy(plan["scenes"][0])
    result = visual_director.regenerate_scene(plan, old["id"], script, 'Set heading to "Wide Energy Poster"')
    assert result["scenes"][0]["title"] == "Wide Energy Poster"
    assert result["scenes"][0]["shot"] == old["shot"]
    assert result["scenes"][0].get("look") == old.get("look")
    with pytest.raises(ValueError, match="No available AI"):
        visual_director.regenerate_scene(plan, old["id"], script, 'Do not set heading to "Wide Energy Poster"')


@pytest.mark.parametrize("mode", ["motion", "hybrid"])
def test_close_collage_edit_preserves_verified_photo_identity_and_binding(mode, tmp_path):
    text = "Armour construction can differ."
    script, timeline = source(text, "Armour construction")
    assets, credits = [], []
    for i, color in enumerate(("red", "green", "blue")):
        path = tmp_path / f"asset-{i}.jpg"
        Image.new("RGB", (100, 160), color).save(path)
        assets.append(str(path))
        credits.append({"file": path.name, "source": "Museum", "credit": f"Verified object {i}",
                        "source_url": f"https://example.org/object/{i}", "license": "Public domain"})
    (tmp_path / "credits.json").write_text(json.dumps(credits))
    plan = visual_director.plan_scenes(script, timeline, mode=mode, use_ai=False,
        image_map={"source": assets}, output_dir=tmp_path, use_project_photos=True)
    old = deepcopy(plan["scenes"][0])
    result = visual_director.regenerate_scene(plan, old["id"], script,
        'Use a close-up collage, locked camera. Set heading to "Armour construction"')
    edited = result["scenes"][0]
    assert edited["shot"]["framing"] == "close" and edited["shot"]["technique"] == "collage"
    assert edited["image"] == old["image"] and edited["media_layers"] == old["media_layers"]
    assert len(edited["media_layers"]) == 3  # Renderer chooses the first two; trusted assets are not rewritten.
    assert [asset["credit"] for asset in edited["media_layers"]] == [f"Verified object {i}" for i in range(3)]
    assert result["sections"] == plan["sections"] and result["captions"] == plan["captions"]
