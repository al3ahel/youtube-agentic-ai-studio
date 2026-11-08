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


@pytest.mark.parametrize("shortening", ["Simpler labels", "Use less text"])
def test_regeneration_preserves_timeline_captions_other_scenes_and_attribution(tmp_path, shortening):
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
    original.pop("use_project_photos")  # Existing pre-policy storyboards remain editable.
    original["scenes"][0].update(template="footage", footage=footage)
    director._as_media_scene(original["scenes"][0])
    original["scenes"][0]["items"][0]["label"] = "Sunlight reaches the panel and becomes electricity"
    regenerated = director.regenerate_scene(original, "scene-001", script, shortening)
    assert regenerated["scenes"][0]["footage"] == footage
    assert regenerated["scenes"][0]["template"] == "footage"
    assert regenerated["scenes"][0]["items"][0]["label"] == "Sunlight reaches the panel and"


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


@pytest.mark.parametrize("mode, photo_option", [("motion", True), ("hybrid", None)])
def test_saved_stock_pictures_and_credits_are_reused_without_network(tmp_path, mode, photo_option):
    script, timeline = source()
    image = tmp_path / "images" / "section_01_img0.jpg"
    image.parent.mkdir()
    image.write_bytes(b"Director fixture; decoding is the renderer boundary")
    (tmp_path / "credits.json").write_text(json.dumps([{
        "file": image.name, "source": "NASA", "author": "NASA/JPL", "license": "Public domain",
        "link": "https://images.nasa.gov/details/example",
    }]))
    image_map = {1: [str(image)], "2": [str(image)]}
    original = copy.deepcopy(image_map)
    timeline.update(timing_source="legacy_srt", timing_precision="phrase", narration_reused=True,
                    warnings=["Section boundaries are inferred from existing subtitles."])
    plan = director.plan_scenes(script, timeline, mode=mode, output_dir=tmp_path, image_map=image_map,
                                use_ai=False, use_project_photos=photo_option)
    assert plan["use_project_photos"] is True
    assert image_map == original
    assert plan["image_status"] == {"available": True, "scenes": 2, "reason": "reused_local_images"}
    asset = plan["scenes"][0]["image"]
    assert asset["path"] == str(image)
    assert asset["credit"] == "NASA/JPL · NASA"
    assert asset["license"] == "Public domain"
    assert asset["source_url"] == "https://images.nasa.gov/details/example"
    assert plan["timing_source"] == "legacy_srt" and plan["narration_reused"] is True
    assert "Section boundaries are inferred from existing subtitles." in plan["warnings"]
    edited = director.regenerate_scene(plan, "scene-001", script, "Use a comparison with less text")
    assert edited["scenes"][0]["image"] == asset
    assert edited["scenes"][0]["start"] == plan["scenes"][0]["start"]
    assert edited["scenes"][1] == plan["scenes"][1]


def test_missing_external_and_escaped_pictures_fall_back_to_graphics(tmp_path, monkeypatch):
    script, timeline = source()
    output = tmp_path / "job"
    output.mkdir()
    monkeypatch.setattr(director.config, "OUTPUT_DIR", str(output))
    private = tmp_path / "private.jpg"
    private.write_bytes(b"private")
    image_map = {1: [str(private), "https://example.com/remote.jpg"], 2: ["missing.jpg"]}
    plan = director.plan_scenes(script, timeline, output_dir=output, image_map=image_map,
                                use_ai=False, use_project_photos=True)
    assert not any("image" in scene for scene in plan["scenes"])
    assert plan["image_status"]["available"] is False
    assert any("Some saved pictures" in warning for warning in plan["warnings"])


def test_subtitle_phrase_beats_are_not_labelled_word_timings():
    script, timeline = source()
    timeline["words"] = []
    plan = director.plan_scenes(script, timeline, use_ai=False)
    scene = plan["scenes"][0]
    reveals = [beat["time"] for beat in scene["beats"] if beat["action"] == "reveal" and beat["target"].startswith("item-")]
    assert reveals == [0, 3, 6]
    assert scene["beat_timing"] == "phrase"
    assert plan["words"] == []


def test_long_sections_cycle_existing_pictures_and_charts_keep_data_space(tmp_path):
    script, timeline = source()
    for section in timeline["sections"]:
        section["duration"] = 24
    timeline["sections"][1]["start"] = 24
    timeline["duration"] = 48
    first, second = tmp_path / "first.jpg", tmp_path / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    script["sections"][1]["chart_data"] = [{"label": "A", "value": 1}, {"label": "B", "value": 2}]
    plan = director.plan_scenes(script, timeline, output_dir=tmp_path,
                                image_map={1: [str(first), str(second)], 2: [str(first)]},
                                use_ai=False, use_project_photos=True)
    assert [scene["image"]["path"] for scene in plan["scenes"][:2]] == [str(first), str(second)]
    assert all("image" not in scene for scene in plan["scenes"] if scene["template"] == "chart")


def _photo_plan(output):
    from PIL import Image
    first, duplicate, broken, second = [output / name for name in (
        "first.jpg", "duplicate.jpg", "broken.jpg", "second.jpg")]
    Image.new("RGB", (40, 30), "navy").save(first)
    duplicate.write_bytes(first.read_bytes())
    broken.write_bytes(b"not a picture")
    Image.new("RGB", (40, 30), "gold").save(second)
    (output / "credits.json").write_text(json.dumps([{
        "file": second.name, "source": "NASA", "author": "NASA/JPL", "license": "Public domain",
        "link": "https://images.nasa.gov/details/alternate",
    }]))
    script, timeline = source()
    image_map = {1: [path.name for path in (first, duplicate, broken, second)], "2": [second.name]}
    plan = director.plan_scenes(script, timeline, use_ai=False, image_map=image_map,
                                output_dir=output, use_project_photos=True)
    plan.update(script_signature="same-script", timeline_signature="same-narration")
    return script, plan, image_map


def test_scene_photo_replacement_cycles_distinct_saved_images_and_preserves_timing(tmp_path):
    script, plan, image_map = _photo_plan(tmp_path)
    before = copy.deepcopy((script, plan, image_map))
    edited = director.regenerate_scene(plan, "scene-001", script, replace_image=True,
                                        image_map=image_map, output_dir=tmp_path)
    asset = edited["scenes"][0]["image"]
    assert asset["path"] == str(tmp_path / "second.jpg")
    assert asset["credit"] == "NASA/JPL · NASA" and asset["license"] == "Public domain"
    assert asset["source_url"] == "https://images.nasa.gov/details/alternate"
    assert edited["scenes"][1:] == plan["scenes"][1:]
    for key in ("sections", "captions", "words", "script_signature", "timeline_signature", "duration"):
        assert edited[key] == plan[key]
    for key in ("id", "section_id", "start", "duration"):
        assert edited["scenes"][0][key] == plan["scenes"][0][key]
    cycled = director.regenerate_scene(edited, "scene-001", script, replace_image=True,
                                       image_map=image_map, output_dir=tmp_path)
    assert cycled["scenes"][0]["image"]["path"] == str(tmp_path / "first.jpg")
    assert (script, plan, image_map) == before


def test_scene_photo_replacement_rejects_missing_duplicate_and_unsafe_alternatives(tmp_path, monkeypatch):
    output = tmp_path / "project"
    output.mkdir()
    monkeypatch.setattr(director.config, "OUTPUT_DIR", str(output))
    script, plan, _ = _photo_plan(output)
    private = tmp_path / "outside.jpg"
    private.write_bytes((output / "second.jpg").read_bytes())
    (output / "escape.jpg").symlink_to(private)
    image_map = {1: ["first.jpg", "duplicate.jpg", "broken.jpg", "missing.jpg", "escape.jpg",
                     str(private), "https://example.com/remote.jpg"], 2: ["second.jpg"]}
    before = copy.deepcopy(plan)
    inspected = []
    digest = director._image_digest

    def inspect(path):
        inspected.append(path)
        assert str(path).startswith(str(output) + "/")
        return digest(path)

    monkeypatch.setattr(director, "_image_digest", inspect)
    with pytest.raises(ValueError, match="No alternative saved photo"):
        director.regenerate_scene(plan, "scene-001", script, replace_image=True,
                                  image_map=image_map, output_dir=output)
    assert str(private) not in inspected and str(output / "second.jpg") not in inspected
    assert plan == before


@pytest.mark.parametrize("use_footage", [False, True])
def test_explicit_scene_layout_and_short_labels_override_ai(tmp_path, monkeypatch, use_footage):
    script, plan, _ = _photo_plan(tmp_path)
    scene = plan["scenes"][0]
    scene["items"][0]["label"] = "Sunlight reaches the panel and becomes electricity"
    if use_footage:
        scene.pop("image")
        scene.update(template="footage", footage={
            "path": str(tmp_path / "clip.mp4"), "source_url": "https://www.pexels.com/video/123/",
            "credit": "Video by Author on Pexels", "in_point": .5,
        })
    original = copy.deepcopy(plan)
    repeated = {"scenes": [{key: copy.deepcopy(scene[key]) for key in (
        "id", "template", "title", "visual_goal", "items")}]}
    monkeypatch.setattr(director.config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(director.openrouter, "plan_json", lambda *a, **k: repeated)
    edited = director.regenerate_scene(plan, scene["id"], script, "Use a comparison with shorter labels")
    replacement = edited["scenes"][0]
    assert replacement["template"] == "comparison" and "footage" not in replacement
    assert replacement["items"][0]["label"] == ("Sunlight" if use_footage else "Sunlight reaches the panel and")
    if use_footage:
        assert replacement["graphics_version"] == 2
    if not use_footage:
        assert replacement["image"] == scene["image"]
    assert edited["scenes"][1:] == plan["scenes"][1:]
    assert edited["sections"] == plan["sections"] and edited["captions"] == plan["captions"]
    assert plan == original


@pytest.mark.parametrize("template", ["flow", "explain"])
def test_scene_regeneration_uses_visible_fallback_when_ai_repeats_design(tmp_path, monkeypatch, template):
    script, plan, _ = _photo_plan(tmp_path)
    scene = plan["scenes"][0]
    scene["template"] = template
    repeated = {"scenes": [{key: copy.deepcopy(scene[key]) for key in (
        "id", "template", "title", "visual_goal", "items")}]}
    repeated["scenes"][0]["visual_goal"] = "A different planning note that is never displayed"
    monkeypatch.setattr(director.config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(director.openrouter, "plan_json", lambda *a, **k: repeated)
    edited = director.regenerate_scene(plan, scene["id"], script)
    assert edited["scenes"][0]["template"] == ("text" if template == "explain" else "explain")
    assert edited["scenes"][0]["image"] == scene["image"]


@pytest.mark.parametrize("template,instructions", [("flow", "Use flow"), ("footage", ""), ("chart", "")])
def test_scene_regeneration_reports_no_visible_change_without_mutating_plan(tmp_path, template, instructions):
    script, timeline = source()
    if template == "chart":
        script["sections"][0]["chart_data"] = [{"label": "First", "value": 1}, {"label": "Second", "value": 2}]
    plan = director.plan_scenes(script, timeline, use_ai=False)
    if template == "footage":
        plan.pop("use_project_photos")  # Preserve legacy footage when no layout change was requested.
        plan["scenes"][0].update(template="footage", footage={
            "path": str(tmp_path / "clip.mp4"), "source_url": "https://www.pexels.com/video/123/",
            "credit": "Video by Author on Pexels", "in_point": .5,
        })
        director._as_media_scene(plan["scenes"][0])
    original = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="visible design is unchanged"):
        director.regenerate_scene(plan, "scene-001", script, instructions)
    assert plan == original


def test_photo_replacement_can_replace_footage_with_local_picture(tmp_path):
    script, plan, image_map = _photo_plan(tmp_path)
    plan["scenes"][0].pop("image")
    plan["scenes"][0].update(template="footage", footage={
        "path": str(tmp_path / "clip.mp4"), "source_url": "https://www.pexels.com/video/123/",
        "credit": "Video by Author on Pexels", "in_point": .5,
    })
    edited = director.regenerate_scene(plan, "scene-001", script, replace_image=True,
                                       image_map=image_map, output_dir=tmp_path)
    assert edited["scenes"][0]["template"] == "explain"
    assert "footage" not in edited["scenes"][0]
    assert edited["scenes"][0]["image"]["path"] == str(tmp_path / "first.jpg")
    assert "footage" in plan["scenes"][0]


def test_requested_chart_needs_source_data_and_cannot_silently_hide_replacement_photo(tmp_path):
    script, plan, image_map = _photo_plan(tmp_path)
    with pytest.raises(ValueError, match="no supplied chart data"):
        director.regenerate_scene(plan, "scene-001", script, "Use a chart")
    script["sections"][0]["chart_data"] = [{"label": "First", "value": 1}, {"label": "Second", "value": 2}]
    edited = director.regenerate_scene(plan, "scene-001", script, "Use a chart")
    assert edited["scenes"][0]["template"] == "chart"
    assert edited["scenes"][0]["items"] == script["sections"][0]["chart_data"]
    assert "image" not in edited["scenes"][0] and edited["image_status"]["scenes"] == 1
    with pytest.raises(ValueError, match="A chart does not display a photo"):
        director.regenerate_scene(plan, "scene-001", script, "Use a chart", replace_image=True,
                                  image_map=image_map, output_dir=tmp_path)


@pytest.mark.parametrize("mode, photo_option", [("motion", None), ("motion", False), ("hybrid", False)])
def test_graphics_planning_never_reads_or_inherits_cached_photos(tmp_path, monkeypatch, mode, photo_option):
    script, timeline = source()
    saved_photo = tmp_path / "old-project.png"
    saved_photo.write_bytes(b"A cached picture from a previous draft")
    image_map = {1: [str(saved_photo)], "2": [str(saved_photo)]}
    before = copy.deepcopy((script, timeline, image_map))

    def forbidden(*args, **kwargs):
        pytest.fail("Photo-disabled planning must not read the saved-image map or credits")

    monkeypatch.setattr(director, "_saved_images", forbidden)
    plan = director.plan_scenes(script, timeline, mode=mode, output_dir=tmp_path, use_ai=False,
                                image_map=image_map, use_project_photos=photo_option)
    assert plan["use_project_photos"] is False
    assert not any("image" in scene or "footage" in scene for scene in plan["scenes"])
    assert plan["image_status"]["available"] is False
    assert all(scene["items"] and scene["beats"] for scene in plan["scenes"])
    actions = {beat["action"] for scene in plan["scenes"] for beat in scene["beats"]}
    assert {"reveal", "connect"} <= actions
    assert json.loads((tmp_path / "scene_plan.json").read_text())["use_project_photos"] is False
    assert (script, timeline, image_map) == before
    assert saved_photo.read_bytes() == b"A cached picture from a previous draft"


@pytest.mark.parametrize("plan, expected", [
    (None, False),
    ({"mode": "motion", "scenes": []}, False),
    ({"mode": "motion", "scenes": [{"image": {"path": "saved.png"}}]}, True),
    ({"mode": "hybrid", "scenes": []}, True),
    ({"mode": "footage", "scenes": []}, True),
    ({"mode": "motion", "use_project_photos": True, "scenes": []}, True),
    ({"mode": "hybrid", "use_project_photos": False, "scenes": [{"image": {"path": "saved.png"}}]}, False),
])
def test_photo_policy_prefers_explicit_choice_and_recognizes_legacy_plans(plan, expected):
    before = copy.deepcopy(plan)
    assert director.plan_uses_project_photos(plan) is expected
    assert plan == before


@pytest.mark.parametrize("option", [0, 1, "true", [], {}])
def test_invalid_photo_option_is_rejected_before_planning_or_writing(tmp_path, monkeypatch, option):
    script, timeline = source()
    monkeypatch.setattr(director, "_offline_scenes", lambda *a, **k: pytest.fail("Invalid option reached planning"))
    with pytest.raises(ValueError, match="true or false"):
        director.plan_scenes(script, timeline, use_project_photos=option, output_dir=tmp_path)
    assert not (tmp_path / "scene_plan.json").exists()


@pytest.mark.parametrize("option", [None, 0, 1, "false", []])
def test_saved_photo_policy_must_be_boolean_when_present(option):
    script, timeline = source()
    plan = director.plan_scenes(script, timeline, use_ai=False)
    plan["use_project_photos"] = option
    with pytest.raises(ValueError, match="true or false"):
        director.plan_uses_project_photos(plan)
    with pytest.raises(ValueError, match="true or false"):
        director.validate_plan(plan)


@pytest.mark.parametrize("mode, media", [("motion", "image"), ("motion", "footage"), ("hybrid", "image")])
def test_explicit_graphics_policy_rejects_inherited_media_but_keeps_legacy_plans_valid(tmp_path, mode, media):
    script, timeline = source()
    plan = director.plan_scenes(script, timeline, mode=mode, use_ai=False, use_project_photos=False)
    scene = plan["scenes"][0]
    if media == "image":
        scene["image"] = {"path": str(tmp_path / "saved.png")}
    else:
        scene.update(template="footage", footage={
            "path": str(tmp_path / "clip.mp4"), "source_url": "https://www.pexels.com/video/123/",
            "credit": "Video by Author on Pexels", "in_point": 0,
        })
    director._as_media_scene(scene)  # A legacy media scene, not a v2 composed world.
    with pytest.raises(ValueError, match="disabled|cannot contain footage"):
        director.validate_plan(plan)
    plan.pop("use_project_photos")
    assert director.validate_plan(plan) is plan


def test_photo_disabled_hybrid_can_still_use_licensed_footage(tmp_path):
    script, timeline = source()
    plan = director.plan_scenes(script, timeline, mode="hybrid", use_ai=False, use_project_photos=False)
    plan["scenes"][0].update(template="footage", footage={
        "path": str(tmp_path / "clip.mp4"), "source_url": "https://www.pexels.com/video/123/",
        "credit": "Video by Author on Pexels", "in_point": 0,
    })
    director._as_media_scene(plan["scenes"][0])
    assert director.validate_plan(plan) is plan
    assert director.plan_uses_project_photos(plan) is False


def test_graphics_only_motion_cannot_gain_a_photo_during_regeneration(tmp_path, monkeypatch):
    script, timeline = source()
    plan = director.plan_scenes(script, timeline, use_ai=False)
    before = copy.deepcopy(plan)
    monkeypatch.setattr(director, "_next_saved_image", lambda *a, **k: pytest.fail("Graphics-only edit read photos"))
    with pytest.raises(ValueError, match="Animate these photos"):
        director.regenerate_scene(plan, "scene-001", script, replace_image=True,
                                  image_map={1: ["old.png"]}, output_dir=tmp_path)
    edited = director.regenerate_scene(plan, "scene-001", script, "Use a comparison")
    assert edited["use_project_photos"] is False
    assert not any("image" in scene or "footage" in scene for scene in edited["scenes"])
    assert edited["scenes"][1:] == plan["scenes"][1:]
    assert plan == before


def test_legacy_photo_plan_still_preserves_and_replaces_its_saved_pictures(tmp_path):
    script, plan, image_map = _photo_plan(tmp_path)
    plan.pop("use_project_photos")
    before = copy.deepcopy(plan)
    kept = director.regenerate_scene(plan, "scene-001", script, "Use a comparison")
    assert kept["scenes"][0]["image"] == plan["scenes"][0]["image"]
    swapped = director.regenerate_scene(plan, "scene-001", script, replace_image=True,
                                       image_map=image_map, output_dir=tmp_path)
    assert swapped["scenes"][0]["image"]["path"] == str(tmp_path / "second.jpg")
    assert swapped["scenes"][1:] == plan["scenes"][1:]
    assert "use_project_photos" not in kept and "use_project_photos" not in swapped
    assert plan == before


@pytest.mark.parametrize("instruction", [
    "Use animated graphics only", "No photos", "No pictures", "No images", "Remove the photo",
])
def test_explicit_photo_removal_does_not_restore_the_original_image(tmp_path, instruction):
    script, plan, _ = _photo_plan(tmp_path)
    before = copy.deepcopy(plan)
    edited = director.regenerate_scene(plan, "scene-001", script, instruction)
    assert "image" not in edited["scenes"][0] and "footage" not in edited["scenes"][0]
    assert edited["scenes"][1:] == plan["scenes"][1:]
    assert edited["use_project_photos"] is True
    assert edited["image_status"]["scenes"] == 1
    for key in ("sections", "captions", "words", "duration", "script_signature", "timeline_signature"):
        assert edited[key] == plan[key]
    for key in ("id", "section_id", "start", "duration"):
        assert edited["scenes"][0][key] == plan["scenes"][0][key]
    assert plan == before


def test_graphics_only_scene_instruction_removes_footage_even_if_ai_repeats_it(tmp_path, monkeypatch):
    script, plan, _ = _photo_plan(tmp_path)
    scene = plan["scenes"][0]
    scene.pop("image")
    scene.update(template="footage", footage={
        "path": str(tmp_path / "clip.mp4"), "source_url": "https://www.pexels.com/video/123/",
        "credit": "Video by Author on Pexels", "in_point": 0,
    })
    repeated = {"scenes": [{key: copy.deepcopy(scene[key]) for key in (
        "id", "template", "title", "visual_goal", "items")}]}
    before = copy.deepcopy(plan)
    monkeypatch.setattr(director.config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(director.openrouter, "plan_json", lambda *a, **k: repeated)
    edited = director.regenerate_scene(plan, scene["id"], script, "Use graphics only")
    assert edited["scenes"][0]["template"] == "explain"
    assert "image" not in edited["scenes"][0] and "footage" not in edited["scenes"][0]
    assert edited["scenes"][1:] == plan["scenes"][1:]
    assert edited["use_project_photos"] is True and plan == before


def test_removing_and_replacing_a_photo_is_an_actionable_conflict(tmp_path, monkeypatch):
    script, plan, image_map = _photo_plan(tmp_path)
    before = copy.deepcopy(plan)
    monkeypatch.setattr(director, "_next_saved_image", lambda *a, **k: pytest.fail("Conflicting edit read photos"))
    with pytest.raises(ValueError, match="conflicts.*Uncheck"):
        director.regenerate_scene(plan, "scene-001", script, "Remove the photo", replace_image=True,
                                  image_map=image_map, output_dir=tmp_path)
    assert plan == before
