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
])
def test_subject_requires_support_in_its_own_narration(label, source, expected):
    assert object_direction.symbol_for_item(label, source) == expected


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


def test_new_plan_persists_grounded_graphics_and_leaves_source_timing_unchanged():
    script, timeline = source()
    before = deepcopy((script, timeline))
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    assert (script, timeline) == before
    assert all(scene["graphics_version"] == 1 for scene in plan["scenes"])
    assert plan["scenes"][0]["items"][0]["symbol"] == "census"
    assert plan["scenes"][1]["items"][0]["symbol"] == "mining"
    assert not any("image" in scene or "footage" in scene for scene in plan["scenes"])
    clean = _validate_plan(plan)
    assert [scene["items"] for scene in clean["scenes"]] == [scene["items"] for scene in plan["scenes"]]
    assert all(scene["graphics_version"] == 1 for scene in clean["scenes"])
    assert clean["captions"] == plan["captions"]


def test_old_plan_stays_old_until_explicit_object_rebuild(monkeypatch):
    script, timeline = source()
    monkeypatch.setattr(visual_director.config, "VISUAL_AI_ENABLED", False)
    old = visual_director.plan_scenes(script, timeline, use_ai=False)
    for scene in old["scenes"]:
        scene.pop("graphics_version")
        for item in scene["items"]:
            item.pop("symbol")
    before = deepcopy(old)
    assert not any("graphics_version" in scene for scene in _validate_plan(old)["scenes"])
    updated = visual_director.regenerate_scene(old, old["scenes"][0]["id"], script, "Use animated objects")
    assert old == before
    assert updated["scenes"][0]["graphics_version"] == 1
    assert updated["scenes"][1] == old["scenes"][1]
    assert updated["sections"] == old["sections"]
    assert updated["captions"] == old["captions"]
    assert any("animated objects" in item for item in updated["last_edit"]["changes"])


@pytest.mark.parametrize("field,value", [("graphics_version", True), ("graphics_version", 2), ("symbol", "<script>"), ("symbol", "made-up-country-map")])
def test_object_schema_rejects_executable_or_unbounded_fields(field, value):
    script, timeline = source()
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    target = plan["scenes"][0]["items"][0] if field == "symbol" else plan["scenes"][0]
    target[field] = value
    with pytest.raises(ValueError):
        visual_director.validate_plan(plan)
    with pytest.raises(ValueError):
        _validate_plan(plan)
