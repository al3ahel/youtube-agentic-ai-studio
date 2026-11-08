"""Grounded vector subjects and legacy/rebuild compatibility, without providers."""
from copy import deepcopy

import pytest

from agents import object_direction, visual_director
from video.renderers.html import _validate_plan


@pytest.mark.parametrize(("label", "source", "expected"), [
    ("Census undercounts", "A census can undercount people when records are missing.", "census-gap"),
    ("Mining copper and iron", "Mining extracts copper and iron from ore.", "mining"),
    ("Current challenges", "Current challenges affect many people.", "people"),
    ("Data mining", "Data mining finds patterns in records.", "network"),
    ("Solar panels", "Solar panels convert sunlight into electricity.", "solar"),
    ("Stored energy", "Batteries release stored energy later.", "battery"),
    ("The next idea", "The next idea concerns a philosophical question.", "concept"),
    ("Mining industry", "This narration describes a garden, without any mining claim.", "concept"),
    ("Flying satellite", "Trees hold the soil and shelter birds.", "concept"),
    ("School buildings", "School buildings serve the community.", "building"),
    ("Armour", "Armour and firearms evolved together.", "armour"),
    ("Breastplate", "A breastplate covered the torso.", "armour"),
    ("Cuirass", "The cuirass was worn over clothing.", "armour"),
    ("Firearms", "Armour and firearms evolved together.", "firearm"),
    ("Arquebus", "An arquebus appears in the collection.", "firearm"),
    ("Firearm", "A projectile meets armour.", "concept"),
    ("Firearms", "No firearms were used. Armour remained on display.", "concept"),
    ("Armour", "This section is about confidence, without armour or weapons.", "concept"),
    ("Rifle", "Researchers rifle through the documents.", "concept"),
])
def test_subject_requires_support_in_its_own_narration(label, source, expected):
    assert object_direction.symbol_for_item(label, source) == expected


@pytest.mark.parametrize("narration,expected", [
    ("Armour and firearms evolved together for centuries; protection always had limits.",
     [("Armour", "armour"), ("Firearms", "firearm")]),
    ("Solar panels feed batteries.", [("Solar panels", "solar"), ("Batteries", "battery")]),
])
def test_concrete_local_subjects_are_not_crowded_out_by_four_narration_fragments(narration, expected):
    scene = {"template": "explain", "title": "Supported subjects", "items": [
        {"label": label} for label in ("A specific test is not a", "No universal guarantee",
                                      "This explanation has limits", "An additional thought")]}
    object_direction.apply_staging(scene, narration)
    assert [(item["label"], item["symbol"]) for item in scene["items"]] == expected
    assert len([item for item in scene["items"] if item["role"] == "subject"]) == 1
    assert not object_direction.grounding_issues(scene, narration)
    if expected[0][1] == "armour":
        assert scene["staging"]["action"] == "reveal"
        assert "mechanism" not in scene


def test_unknown_local_subject_does_not_invent_equipment():
    scene = {"template": "explain", "title": "A philosophical question",
             "items": [{"label": "A philosophical question"}]}
    object_direction.apply_staging(scene, "A philosophical question concerns the nature of certainty.")
    assert all(item["symbol"] == "concept" for item in scene["items"])


def test_equipment_vocabulary_survives_normal_planning_schema_and_legacy_validation(monkeypatch):
    monkeypatch.setattr(visual_director.config, "SHOT_PLANNING_ENABLED", True)
    narration = "Armour and firearms evolved together for centuries; protection always had limits."
    script = {"title": "Protection had limits", "sections": [{"id": "limits", "title": "Protection had limits", "narration": narration}]}
    timeline = {"fps": 30, "width": 1080, "height": 1920, "duration": 4,
                "sections": [{"id": "limits", "start": 0, "duration": 4}], "captions": [], "words": []}
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    scene = plan["scenes"][0]
    assert [item["symbol"] for item in scene["items"]] == ["armour", "firearm"]
    assert scene["staging"]["action"] == "reveal" and "mechanism" not in scene
    assert _validate_plan(plan)["scenes"][0]["items"] == scene["items"]
    schema = visual_director._ai_schema([scene["id"]], shots_enabled=True)
    symbols = schema["properties"]["scenes"]["items"]["properties"]["items"]["items"]["properties"]["symbol"]["enum"]
    assert {"armour", "firearm"} <= set(symbols)
    legacy = deepcopy(plan)
    old = legacy["scenes"][0]
    old.pop("shot"); old.pop("staging")
    for item in old["items"]:
        item.pop("role")
    object_direction.apply_objects(old, narration)
    assert visual_director.validate_plan(legacy)["scenes"][0]["graphics_version"] == 1
    assert _validate_plan(legacy)["scenes"][0]["items"] == old["items"]


def source():
    script = {"title": "Records and resources", "sections": [
        {"id": "records", "title": "Who makes it into the records?",
         "narration": "A census records people. Missing census records can lead to an undercount.",
         "bullet_points": ["People enter census records", "Census undercounts leave gaps"]},
        {"id": "ore", "title": "From rock to extracted ore",
         "narration": "Mining extracts ore from rock. A drill reaches the ore and a conveyor moves it.",
         "bullet_points": ["Mining drills reach the ore", "Ore moves along the conveyor"]},
    ]}
    timeline = {"fps": 12, "width": 640, "height": 360, "duration": 14,
                "sections": [{"id": row["id"], "start": i * 7, "duration": 7, "caption_timing": "phrase"}
                             for i, row in enumerate(script["sections"])],
                "captions": [{"start": i * 7, "end": i * 7 + 3.5, "text": row["narration"].split(". ")[0] + "."}
                             for i, row in enumerate(script["sections"])], "words": []}
    return script, timeline


def test_new_plan_persists_grounded_graphics_and_leaves_source_timing_unchanged(monkeypatch):
    # The two legacy object scenes map one-to-one to the two source sections.
    monkeypatch.setattr(visual_director.config, "SHOT_PLANNING_ENABLED", False)
    script, timeline = source()
    before = deepcopy((script, timeline))
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    assert (script, timeline) == before
    assert all(scene["graphics_version"] == 2 for scene in plan["scenes"])
    assert plan["scenes"][0]["items"][0]["symbol"] == "census"
    assert plan["scenes"][1]["items"][0]["symbol"] == "mining"
    assert not any("image" in scene or "footage" in scene for scene in plan["scenes"])
    clean = _validate_plan(plan)
    assert [scene["items"] for scene in clean["scenes"]] == [scene["items"] for scene in plan["scenes"]]
    assert all(scene["graphics_version"] == 2 for scene in clean["scenes"])
    assert clean["captions"] == plan["captions"]


def test_old_plan_stays_old_until_explicit_object_rebuild(monkeypatch):
    script, timeline = source()
    monkeypatch.setattr(visual_director.config, "VISUAL_AI_ENABLED", False)
    old = visual_director.plan_scenes(script, timeline, use_ai=False)
    for scene in old["scenes"]:
        scene.pop("graphics_version")
        scene.pop("staging")
        for item in scene["items"]:
            item.pop("symbol")
            item.pop("role")
        if len(scene["items"]) == 1:
            scene["items"].append({"label": scene["title"]})
    before = deepcopy(old)
    assert not any("graphics_version" in scene for scene in _validate_plan(old)["scenes"])
    updated = visual_director.regenerate_scene(old, old["scenes"][0]["id"], script, "Use animated objects")
    assert old == before
    assert updated["scenes"][0]["graphics_version"] == 2
    assert updated["scenes"][1] == old["scenes"][1]
    assert updated["sections"] == old["sections"]
    assert updated["captions"] == old["captions"]
    assert any("animated objects" in item for item in updated["last_edit"]["changes"])


@pytest.mark.parametrize("field,value", [("graphics_version", True), ("graphics_version", 3), ("symbol", "<script>"), ("symbol", "made-up-country-map")])
def test_object_schema_rejects_executable_or_unbounded_fields(field, value):
    script, timeline = source()
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    target = plan["scenes"][0]["items"][0] if field == "symbol" else plan["scenes"][0]
    target[field] = value
    with pytest.raises(ValueError):
        visual_director.validate_plan(plan)
    with pytest.raises(ValueError):
        _validate_plan(plan)
