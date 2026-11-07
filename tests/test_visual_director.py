"""Constrained scene planning against measured narration, entirely offline."""
import copy
import json

import pytest

from agents import visual_director as director


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    for name in ("OPENROUTER_API_KEY", "PEXELS_API_KEY"):
        monkeypatch.setattr(director.config, name, "", raising=False)
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(director.config, "VISUAL_AI_ENABLED", True, raising=False)
    monkeypatch.setattr(director.config, "VISUAL_SCENE_SECONDS", 12, raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected live network request")

    monkeypatch.setattr(director.openrouter.requests, "get", forbidden)
    monkeypatch.setattr(director.openrouter.requests, "post", forbidden)


def source():
    script = {"title": "How energy flows", "sections": [
        {"id": 1, "title": "From light to electricity", "duration_seconds": 9,
         "narration": "First sunlight reaches the panel. Then electrons flow through a circuit. Finally electricity powers your home.",
         "bullet_points": ["Sunlight", "Electron flow", "Electricity"]},
        {"id": 2, "title": "Storage compared with direct use", "duration_seconds": 9,
         "narration": "Direct use powers the home immediately, whereas batteries store energy for later use.",
         "bullet_points": ["Immediate use", "Stored energy"]},
    ]}
    timeline = {"fps": 24, "width": 1920, "height": 1080, "duration": 18,
                "sections": [{"id": 1, "start": 0, "duration": 9, "speech_start": 0.0, "speech_end": 8.8, "caption_timing": "word"},
                             {"id": 2, "start": 9, "duration": 9, "speech_start": 9.0, "speech_end": 18.0, "caption_timing": "phrase"}],
                "captions": [{"start": 0, "end": 3, "text": "First sunlight reaches the panel."},
                             {"start": 3, "end": 6, "text": "Then electrons flow through a circuit."},
                             {"start": 6, "end": 9, "text": "Finally electricity powers your home."},
                             {"start": 9, "end": 18, "text": script["sections"][1]["narration"]}],
                "words": [{"word": "sunlight", "start": 1.0, "end": 1.5, "section_id": 1},
                          {"word": "electrons", "start": 3.5, "end": 4, "section_id": 1},
                          {"word": "flow", "start": 4, "end": 4.5, "section_id": 1},
                          {"word": "electricity", "start": 6.5, "end": 7, "section_id": 1}]}
    return script, timeline


def test_no_key_produces_repeatable_saved_motion_plan_and_word_anchors(tmp_path):
    script, timeline = source()
    before = copy.deepcopy((script, timeline))
    plan = director.plan_scenes(script, timeline, output_dir=tmp_path)
    assert plan == director.plan_scenes(script, timeline)
    assert (script, timeline) == before
    assert plan["ai_status"] == {"status": "unavailable", "reason": "missing_key"}
    assert plan["scenes"][0]["template"] == "flow"
    assert plan["scenes"][1]["template"] == "comparison"
    reveal = next(beat for beat in plan["scenes"][0]["beats"]
                  if beat["target"] == "item-0" and beat["action"] == "reveal")
    assert reveal["time"] == pytest.approx(.88)
    assert plan["scenes"][0]["beat_timing"] in {"word", "mixed"}
    assert all(2 <= len(scene["items"]) <= 6 for scene in plan["scenes"])
    assert all(len(item["label"]) <= 64 for scene in plan["scenes"] for item in scene["items"])
    assert plan["sections"][0]["speech_end"] == 8.8
    assert json.loads((tmp_path / "scene_plan.json").read_text()) == plan


def test_long_section_has_multiple_scenes_and_exact_coverage():
    script = {"title": "The process", "sections": [{"id": "one", "title": "Observe, model, test",
              "narration": "Observe the world. Develop a model. Test predictions. Refine the explanation.",
              "bullet_points": ["Observe", "Model", "Test", "Refine"]}]}
    timeline = {"fps": 30, "width": 1080, "height": 1920, "duration": 33.3,
                "sections": [{"id": "one", "start": 0, "duration": 33.3}], "captions": []}
    plan = director.plan_scenes(script, timeline, use_ai=False)
    assert len(plan["scenes"]) == 3
    assert sum(scene["duration"] for scene in plan["scenes"]) == pytest.approx(33.3)
    assert plan["scenes"][-1]["start"] + plan["scenes"][-1]["duration"] == pytest.approx(plan["duration"])
    assert all(scene["beat_timing"] == "distributed" for scene in plan["scenes"])
    assert any("approximate" in warning for warning in plan["warnings"])
    assert plan["width"] < plan["height"]


def test_chart_uses_only_explicit_data_and_never_casual_narration_numbers():
    script, timeline = source()
    script["sections"][0]["narration"] = "Output increased by 25 percent over 2 years. This is an example."
    plan = director.plan_scenes(script, timeline, use_ai=False)
    assert all(scene["template"] != "chart" for scene in plan["scenes"])
    explicit = [{"label": "First observation", "value": 10}, {"label": "Second observation", "value": 12.5}]
    script["sections"][0]["chart_data"] = explicit
    chart = director.plan_scenes(script, timeline, use_ai=False)["scenes"][0]
    assert chart["template"] == "chart"
    assert chart["items"] == explicit
    assert any(beat["action"] == "count" for beat in chart["beats"])


@pytest.mark.parametrize("change", [
    {"start": 2}, {"duration": float("nan")}, {"template": "javascript"},
    {"code": "alert(1)"}, {"items": [{"label": "A"}]},
    {"beats": [{"time": 90, "target": "scene", "action": "reveal"}]},
    {"beats": [{"time": 1, "target": "item-99", "action": "execute"}]},
    {"template": "chart"}, {"template": "footage"},
])
def test_invalid_timing_code_or_template_data_is_rejected(change):
    script, timeline = source()
    plan = director.plan_scenes(script, timeline, use_ai=False)
    plan["scenes"][0].update(change)
    with pytest.raises(ValueError):
        director.validate_plan(plan)


def test_ai_can_change_only_design_and_falls_back_on_unsupported_numbers(monkeypatch):
    script, timeline = source()
    offline = director.plan_scenes(script, timeline, use_ai=False)
    monkeypatch.setattr(director.config, "OPENROUTER_API_KEY", "test-secret-credential", raising=False)
    design = {"scenes": [{"id": scene["id"], "template": "explain", "title": "Energy conversion",
                         "visual_goal": "Explain how useful electricity reaches the home",
                         "items": [{"label": "Sunlight"}, {"label": "Electricity"}]}
                        for scene in offline["scenes"]]}
    design["scenes"][1]["title"] = "99 percent better"
    monkeypatch.setattr(director.openrouter, "plan_json", lambda *a, **k: design)
    plan = director.plan_scenes(script, timeline)
    assert plan["scenes"][0]["title"] == "Energy conversion"
    assert plan["scenes"][1] == offline["scenes"][1]
    assert plan["sections"] == offline["sections"] and plan["captions"] == offline["captions"]
    assert any("unsupported number" in warning for warning in plan["warnings"])


@pytest.mark.parametrize("failure", ["code", "timing", "missing_scene", "quota"])
def test_invalid_ai_design_or_quota_keeps_working_local_plan(monkeypatch, failure):
    script, timeline = source()
    expected = director.plan_scenes(script, timeline, use_ai=False)
    monkeypatch.setattr(director.config, "OPENROUTER_API_KEY", "test-secret-credential", raising=False)
    design = {"scenes": [{key: scene[key] for key in ("id", "template", "title", "visual_goal", "items")}
                        for scene in expected["scenes"]]}
    if failure == "code":
        design["scenes"][0]["code"] = "executeThis()"
    elif failure == "timing":
        design["scenes"][0]["start"] = 100
    elif failure == "missing_scene":
        design["scenes"].pop()

    def plan_json(*args, **kwargs):
        if failure == "quota":
            raise director.openrouter.OpenRouterError("rate_limited", "Unavailable")
        return design

    monkeypatch.setattr(director.openrouter, "plan_json", plan_json)
    plan = director.plan_scenes(script, timeline)
    assert plan["ai_status"]["status"] == "fallback"
    assert plan["scenes"] == expected["scenes"]


def test_hybrid_without_stock_key_still_produces_complete_motion(tmp_path):
    script, timeline = source()
    plan = director.plan_scenes(script, timeline, mode="hybrid", output_dir=tmp_path)
    assert plan["stock_status"]["reason"] == "missing_key"
    assert not any("footage" in scene for scene in plan["scenes"])
    assert director.validate_plan(plan) is plan


def test_regeneration_preserves_timeline_captions_other_scenes_and_attribution(tmp_path):
    script, timeline = source()
    original = director.plan_scenes(script, timeline, use_ai=False)
    regenerated = director.regenerate_scene(original, "scene-001", script, "Make a comparison with less text")
    assert regenerated["scenes"][0]["template"] == "comparison"
    assert regenerated["scenes"][1] == original["scenes"][1]
    assert regenerated["sections"] == original["sections"] and regenerated["captions"] == original["captions"]
    for field in ("start", "duration", "id", "section_id"):
        assert regenerated["scenes"][0][field] == original["scenes"][0][field]
    assert original["scenes"][0]["template"] == "flow"
    footage = {"path": str(tmp_path / "asset.mp4"), "source_url": "https://www.pexels.com/video/123/",
               "credit": "Video by Author on Pexels", "in_point": 0.5}
    original["scenes"][0].update(template="footage", footage=footage)
    regenerated = director.regenerate_scene(original, "scene-001", script, "Simpler labels")
    assert regenerated["scenes"][0]["footage"] == footage
    assert regenerated["scenes"][0]["template"] == "footage"


def test_arabic_text_survives_and_credentials_never_reach_saved_plan(monkeypatch, tmp_path):
    script, timeline = source()
    key = "private-test-key-123456789"
    monkeypatch.setattr(director.config, "OPENROUTER_API_KEY", key)
    script["title"] = "كيف تعمل الطاقة الشمسية"
    script["sections"][0]["title"] = "الشمس والكهرباء " + key
    director.plan_scenes(script, timeline, use_ai=False, output_dir=tmp_path)
    saved = (tmp_path / "scene_plan.json").read_text()
    assert "كيف تعمل الطاقة الشمسية" in saved
    assert key not in saved
