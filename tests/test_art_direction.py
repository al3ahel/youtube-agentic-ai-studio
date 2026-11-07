"""Creative decisions must reach supported fields without changing source timing."""
from copy import deepcopy
import json

import pytest

import config
from agents import art_direction, gemini_client, visual_director


@pytest.fixture
def project(monkeypatch):
    for name in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY"):
        monkeypatch.setattr(config, name, "")
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "OLLAMA_MODEL", "")
    monkeypatch.setattr(config, "LLM_ROUTE", "auto")
    monkeypatch.setattr(config, "CHANNEL_PRESET", "general")
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", True)
    script = {"title": "How a river supports a forest", "sections": [
        {"id": 1, "title": "A living landscape", "narration": "Water carries nutrients. Trees hold the soil. Birds shelter in the canopy."}]}
    timeline = {"fps": 24, "width": 640, "height": 360, "duration": 24,
                "sections": [{"id": 1, "start": 0, "duration": 24}], "captions": []}
    return script, timeline


def test_topic_and_profile_direct_style_while_repeated_renders_are_stable(project):
    script, timeline = project
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    assert plan == visual_director.plan_scenes(script, timeline, use_ai=False)
    assert plan["visual_direction"]["look"] == "botanical"
    assert len({scene["composition"] for scene in plan["scenes"]}) > 1
    assert art_direction.local_direction(script, "news")["look"] == "editorial"
    assert art_direction.local_direction(script, "asmr")["rhythm"] == "calm"
    other = {"title": "Electric circuits", "sections": [{"narration": "Current flows through a circuit."}]}
    assert art_direction.local_direction(other)["look"] == "blueprint"


def test_visual_agent_uses_configured_gemini_without_an_openrouter_key(project, monkeypatch):
    script, timeline = project
    old = visual_director.plan_scenes(script, timeline, use_ai=False)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "configured-free-test-key")
    called = []
    design = {"visual_direction": {"version": 1, "look": "editorial", "rhythm": "measured", "rationale": "Explain the ecosystem with clear rows."},
              "scenes": [{"id": row["id"], "template": "explain", "title": "Water and trees",
                          "visual_goal": "Explain the ecosystem", "items": [{"label": "Water"}, {"label": "Trees"}],
                          "composition": "stack", "look": "editorial", "rhythm": "measured"} for row in old["scenes"]]}
    monkeypatch.setattr(gemini_client, "generate", lambda prompt, **kw: called.append(prompt) or json.dumps(design))
    monkeypatch.setattr(visual_director.openrouter, "plan_json", lambda *a, **kw: pytest.fail("Unconfigured OpenRouter used"))
    result = visual_director.plan_scenes(script, timeline)
    assert len(called) == 1 and result["ai_status"]["status"] == "planned"
    assert result["visual_direction"]["look"] == "editorial"
    assert result["scenes"][0]["title"] == "Water and trees"
    assert result["sections"] == old["sections"]


def test_unsupported_edit_keeps_original_and_does_not_fake_a_layout_change(project):
    script, timeline = project
    old = visual_director.plan_scenes(script, timeline, use_ai=False)
    before = deepcopy(old)
    with pytest.raises(ValueError, match="No available AI could interpret"):
        visual_director.regenerate_scene(old, old["scenes"][0]["id"], script,
                                          "Replace it with a lifelike flying elephant")
    assert old == before


def test_selected_scene_direction_changes_are_visible_fields_only(project):
    script, timeline = project
    old = visual_director.plan_scenes(script, timeline, use_ai=False)
    new = visual_director.regenerate_scene(old, old["scenes"][0]["id"], script,
                                           "Use an editorial look with a calm stack composition")
    assert new["scenes"][0]["look"] == "editorial"
    assert new["scenes"][0]["rhythm"] == "calm"
    assert new["scenes"][0]["composition"] == "stack"
    assert new["last_edit"]["changes"]
    assert new["scenes"][1:] == old["scenes"][1:]
    assert new["sections"] == old["sections"] and new["duration"] == old["duration"]
    assert new["scenes"][0]["start"] == old["scenes"][0]["start"]


@pytest.mark.parametrize("field,value", [("look", "css:execute"), ("rhythm", "999fps"), ("composition", "rawhtml")])
def test_unrenderable_direction_fields_are_rejected(project, field, value):
    script, timeline = project
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    plan["scenes"][0][field] = value
    with pytest.raises(ValueError):
        visual_director.validate_plan(plan)


def test_local_only_frame_review_does_not_extract_or_send_frames(monkeypatch, tmp_path):
    from studio import runner
    monkeypatch.setattr(config, "VISUAL_REVIEW_ENABLED", True)
    monkeypatch.setattr(config, "LLM_ROUTE", "local")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: pytest.fail("Local-only frame extraction"))
    report = runner._review_video("missing.mp4", {}, tmp_path)
    assert report["reason"] == "local_only" and report["passed"] is None


def _design_for_scene(plan, scene):
    row = {key: deepcopy(scene[key]) for key in ("id", "template", "title", "visual_goal")}
    row["items"] = [{"label": item["label"]} for item in scene["items"]]
    row["composition"] = scene.get("composition", "default")
    for field in ("look", "rhythm"):
        row[field] = scene.get(field, plan["visual_direction"][field])
    return {"visual_direction": deepcopy(plan["visual_direction"]), "scenes": [row]}


@pytest.mark.parametrize("field,value", [("look", "editorial"), ("rhythm", "calm")])
def test_scene_ai_override_wins_over_unchanged_film_direction(project, monkeypatch, field, value):
    script, timeline = project
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    before = deepcopy(plan)
    scene = plan["scenes"][0]
    design = _design_for_scene(plan, scene)
    design["scenes"][0][field] = value
    monkeypatch.setattr(config, "GEMINI_API_KEY", "configured-free-test-key")
    monkeypatch.setattr(gemini_client, "generate", lambda *a, **kw: json.dumps(design))

    updated = visual_director.regenerate_scene(plan, scene["id"], script, "Give this scene a softer treatment")

    assert updated["scenes"][0][field] == value
    assert updated["visual_direction"] == plan["visual_direction"]
    assert updated["scenes"][1:] == plan["scenes"][1:]
    assert updated["sections"] == plan["sections"] and updated["duration"] == plan["duration"]
    for key in ("id", "section_id", "start", "duration"):
        assert updated["scenes"][0][key] == scene[key]
    assert updated["last_edit"]["status"] == "ai"
    assert len(updated["last_edit"]["changes"]) == 1
    assert plan == before


def test_scene_ai_film_direction_is_fallback_when_per_scene_values_are_missing(project, monkeypatch):
    script, timeline = project
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    scene = plan["scenes"][0]
    design = _design_for_scene(plan, scene)
    design["visual_direction"].update(look="editorial", rhythm="calm")
    for field in ("look", "rhythm"):
        design["scenes"][0].pop(field)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "configured-free-test-key")
    monkeypatch.setattr(gemini_client, "generate", lambda *a, **kw: json.dumps(design))

    updated = visual_director.regenerate_scene(plan, scene["id"], script, "Give this scene a softer treatment")

    assert updated["scenes"][0]["look"] == "editorial"
    assert updated["scenes"][0]["rhythm"] == "calm"
    assert updated["visual_direction"] == plan["visual_direction"]
    assert updated["scenes"][1:] == plan["scenes"][1:]


@pytest.mark.parametrize("template", ["comparison", "timeline", "chart", "footage"])
def test_ai_unsupported_composition_cannot_claim_a_visible_edit(project, monkeypatch, tmp_path, template):
    script, timeline = project
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    scene = plan["scenes"][0]
    scene.update(template=template, composition="default")
    if template == "chart":
        scene["items"] = [{**item, "value": index + 1} for index, item in enumerate(scene["items"])]
        script["sections"][0]["chart_data"] = deepcopy(scene["items"])
    elif template == "footage":
        plan["use_project_photos"] = True
        scene["footage"] = {"path": str(tmp_path / "clip.mp4"), "source_url": "https://www.pexels.com/video/123/",
                            "credit": "Video by Author on Pexels", "in_point": 0}
    visual_director.validate_plan(plan)
    before = deepcopy(plan)
    design = _design_for_scene(plan, scene)
    design["scenes"][0]["composition"] = "focus"
    monkeypatch.setattr(config, "GEMINI_API_KEY", "configured-free-test-key")
    monkeypatch.setattr(gemini_client, "generate", lambda *a, **kw: json.dumps(design))

    with pytest.raises(ValueError, match="visible design is unchanged"):
        visual_director.regenerate_scene(plan, scene["id"], script, "Improve the visual arrangement")
    assert plan == before

    # A real heading change can succeed, but an inert composition or a stored
    # copy of an inherited look must not be advertised as additional changes.
    design["scenes"][0]["title"] = "A forest ecosystem"
    updated = visual_director.regenerate_scene(plan, scene["id"], script, "Improve the heading")
    assert updated["scenes"][0]["composition"] == "default"
    assert updated["last_edit"]["changes"] == ["Updated the scene heading."]
    assert updated["scenes"][1:] == plan["scenes"][1:]
    assert plan == before


@pytest.mark.parametrize("instruction", [
    "Do not use the blueprint look", "Don't use editorial", "Don’t use poster",
    "Never use a brisk rhythm", "Avoid blueprint, editorial or poster",
    "Remove the botanical look", "Use something without a calm rhythm",
    "Do not use a comparison", "Avoid the timeline layout", "Do not switch to flow",
    "Do not use a stacked composition", "Do not make it shorter", "Do not remove photos",
    "Do not use graphics only",
])
def test_negated_local_edits_are_not_inverted_when_ai_is_unavailable(project, instruction):
    script, timeline = project
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    before = deepcopy(plan)
    with pytest.raises(ValueError, match="No available AI could interpret"):
        visual_director.regenerate_scene(plan, plan["scenes"][0]["id"], script, instruction)
    assert plan == before


@pytest.mark.parametrize("instruction,field,value", [
    ("Do not use blueprint", "look", "editorial"),
    ("Do not use a comparison", "template", "flow"),
])
def test_negated_local_word_does_not_override_an_ai_alternative(project, monkeypatch, instruction, field, value):
    script, timeline = project
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    scene = plan["scenes"][0]
    design = _design_for_scene(plan, scene)
    design["scenes"][0][field] = value
    monkeypatch.setattr(config, "GEMINI_API_KEY", "configured-free-test-key")
    monkeypatch.setattr(gemini_client, "generate", lambda *a, **kw: json.dumps(design))
    updated = visual_director.regenerate_scene(plan, scene["id"], script, instruction)
    assert updated["scenes"][0][field] == value
    assert updated["scenes"][1:] == plan["scenes"][1:]


def test_separate_positive_request_remains_supported_after_an_exclusion(project):
    script, timeline = project
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    updated = visual_director.regenerate_scene(plan, plan["scenes"][0]["id"], script,
        "Do not use blueprint. Use editorial with a calm stack composition.")
    assert updated["scenes"][0]["look"] == "editorial"
    assert updated["scenes"][0]["rhythm"] == "calm"
    assert updated["scenes"][0]["composition"] == "stack"
