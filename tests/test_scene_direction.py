"""Source-grounded composed scenes, editing and the real stock adapter boundary."""
from copy import deepcopy
import hashlib
from threading import Event

import pytest

import config
from agents import object_direction, visual_director as director
from apis import stock_video
from video.renderers.html import _validate_plan
from video.timeline import script_signature


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Six-second scenes intentionally retain their original actor/action IDs.
    monkeypatch.setattr(config, "SHOT_PLANNING_ENABLED", False)
    for name in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "PIXABAY_API_KEY", "PEXELS_API_KEY"):
        monkeypatch.setattr(config, name, "")
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "OLLAMA_MODEL", "")
    monkeypatch.setattr(config, "LLM_ROUTE", "auto")
    monkeypatch.setattr(config, "CHANNEL_PRESET", "general")
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", True)


def project(lines=None):
    lines = lines or ["Rising floodwater reaches homes on the coast.",
                      "Floating homes rise with water as communities adapt.",
                      "Solar panels feed batteries that supply power to homes."]
    script = {"title": "Living with rising water", "sections": [
        {"id": index + 1, "title": f"Scene {index + 1}", "narration": line}
        for index, line in enumerate(lines)]}
    timeline = {"fps": 12, "width": 640, "height": 360, "duration": 6 * len(lines),
                "sections": [{"id": index + 1, "start": index * 6, "duration": 6}
                             for index in range(len(lines))], "captions": [], "words": []}
    return script, timeline


def design(plan):
    return {"creative_brief": deepcopy(plan["creative_brief"]),
            "visual_direction": deepcopy(plan["visual_direction"]),
            "scenes": [{**{key: deepcopy(scene[key]) for key in
                          ("id", "template", "title", "visual_goal", "items", "staging", "composition")},
                        "look": plan["visual_direction"]["look"], "rhythm": plan["visual_direction"]["rhythm"],
                        "palette": plan["creative_brief"]["palette"], "stock_subjects": []}
                       for scene in plan["scenes"]]}


def test_local_film_directs_distinct_mechanisms_and_preserves_the_clock():
    script, timeline = project()
    before = deepcopy((script, timeline))
    plan = director.plan_scenes(script, timeline, use_ai=False)
    assert (script, timeline) == before
    assert [(scene["staging"]["world"], scene["staging"]["action"]) for scene in plan["scenes"]] == [
        ("flood", "rise"), ("flood", "float"), ("generic", "connect")]
    assert [item["symbol"] for item in plan["scenes"][2]["items"]] == ["solar", "battery", "building"]
    assert next(item for item in plan["scenes"][1]["items"] if item["role"] == "subject")["label"] == "Floating homes"
    assert next(item for item in plan["scenes"][1]["items"] if item["symbol"] == "water")["role"] == "context"
    assert [(scene["start"], scene["duration"]) for scene in plan["scenes"]] == [(0, 6), (6, 6), (12, 6)]
    assert all(len(item["label"].split()) <= 6 and len(item["label"]) <= 64 for scene in plan["scenes"] for item in scene["items"])
    assert plan["direction_report"]["source"] == "local"
    assert plan["direction_report"]["status"] == "checks_only"
    assert "not a fact check" in plan["direction_report"]["qualification"]
    assert _validate_plan(plan)["scenes"][1]["staging"] == plan["scenes"][1]["staging"]


def test_human_response_is_not_a_rain_icon_or_whole_film_blueprint():
    script, timeline = project(["With water covering the planet, humanity is redesigning survival technology through engineering."])
    plan = director.plan_scenes(script, timeline, use_ai=False)
    scene = plan["scenes"][0]
    assert scene["staging"]["world"] == "flood" and scene["staging"]["action"] == "adapt"
    assert next(item["symbol"] for item in scene["items"] if item["role"] == "subject") == "people"
    assert plan["visual_direction"]["look"] != "blueprint"
    assert not any(item["symbol"] == "building" for item in scene["items"])


def test_single_grounded_actor_needs_no_filler_and_data_mining_is_not_a_mine():
    script, timeline = project(["The battery recharges.", "Data mining connects records in a network."])
    plan = director.plan_scenes(script, timeline, use_ai=False)
    assert plan["scenes"][0]["items"] == [{"label": "Battery", "symbol": "battery", "role": "subject"}]
    assert plan["scenes"][1]["staging"]["world"] == "generic"
    assert "mining" not in [item["symbol"] for item in plan["scenes"][1]["items"]]


def test_building_nouns_do_not_invent_adaptation_and_native_census_needs_a_real_subject():
    script, timeline = project(["Floodwater reaches buildings and shelters.", "A census records people."])
    plan = director.plan_scenes(script, timeline, use_ai=False)
    assert plan["scenes"][0]["staging"]["action"] == "rise"
    plan["scenes"][1]["items"] = [{"label": "An idea", "symbol": "concept", "role": "subject"}]
    with pytest.raises(ValueError, match="census setting"):
        director.validate_plan(plan)


def test_saved_v2_world_cannot_masquerade_as_a_photo_scene(tmp_path):
    script, timeline = project()
    plan = director.plan_scenes(script, timeline, mode="hybrid", use_ai=False)
    plan["scenes"][0]["image"] = {"path": str(tmp_path / "photo.jpg"), "credit": "", "source": "", "source_url": "", "license": ""}
    with pytest.raises(ValueError, match="Composed graphics"):
        director.validate_plan(plan)
    with pytest.raises(ValueError, match="Composed graphics"):
        _validate_plan(plan)


def test_removing_a_single_label_photo_rebuilds_a_valid_grounded_world(tmp_path):
    script, timeline = project(["The battery recharges."])
    original = director.plan_scenes(script, timeline, mode="hybrid", use_ai=False)
    scene = original["scenes"][0]
    scene["image"] = {"path": str(tmp_path / "saved.jpg")}
    director._as_media_scene(scene)
    before = deepcopy(original)
    result = director.regenerate_scene(original, scene["id"], script, "Remove the photo")
    new = result["scenes"][0]
    assert "image" not in new and new["graphics_version"] == 2
    assert new["items"] == [{"label": "Battery", "symbol": "battery", "role": "subject"}]
    assert (new["start"], new["duration"]) == (scene["start"], scene["duration"])
    assert _validate_plan(result)["scenes"][0]["staging"] == new["staging"]
    assert original == before


def test_ai_actor_roles_camera_and_brief_survive_without_regex_overwrite(monkeypatch):
    script, timeline = project()
    plan = director.plan_scenes(script, timeline, use_ai=False)
    proposed = design(plan)
    proposed["creative_brief"]["palette"] = "sand"
    scene = proposed["scenes"][2]
    scene["staging"]["camera"] = "detail"
    scene["items"] = [{"label": "Batteries", "symbol": "battery", "role": "subject"},
                      {"label": "Solar panels", "symbol": "solar", "role": "support"}]
    for row in proposed["scenes"]:
        row["palette"] = "sand"
    prompts = []
    def respond(prompt, schema, **kwargs):
        director.openrouter.validate_json(proposed, schema)
        prompts.append(prompt)
        return proposed
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(director.openrouter, "plan_json", respond)
    result = director.plan_scenes(script, timeline)
    assert len(prompts) == 1 and "Graphics only: footage is prohibited" in prompts[0]
    assert result["creative_brief"]["palette"] == "sand"
    assert result["scenes"][2]["items"] == scene["items"]
    assert result["scenes"][2]["staging"]["camera"] == "detail"
    assert result["direction_report"]["source"] == "ai"
    assert result["sections"] == plan["sections"]


@pytest.mark.parametrize("bad", ["unsupported_actor", "floating_without_float", "invented_number"])
def test_scene_ai_cannot_borrow_facts_or_actors_from_elsewhere(monkeypatch, bad):
    script, timeline = project(["Rising floodwater reaches homes.", "Mining extracts ore from rock."])
    plan = director.plan_scenes(script, timeline, use_ai=False)
    proposed = design(plan)
    if bad == "unsupported_actor":
        proposed["scenes"][0]["items"][0].update(label="Ore", symbol="mining")
    elif bad == "floating_without_float":
        proposed["scenes"][0]["staging"]["action"] = "float"
    else:
        proposed["scenes"][0]["visual_goal"] = "Show 900 homes under water"
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(director.openrouter, "plan_json", lambda *a, **k: proposed)
    result = director.plan_scenes(script, timeline)
    assert result["scenes"][0] == plan["scenes"][0]
    assert any("scene-001" in warning for warning in result["warnings"])
    assert result["direction_report"]["scenes"][0]["source"] == "local"


def test_camera_palette_edit_changes_only_selected_scene_and_is_serializable(tmp_path):
    script, timeline = project()
    original = director.plan_scenes(script, timeline, use_ai=False)
    saved = deepcopy(original)
    result = director.regenerate_scene(original, "scene-002", script, "Move the camera closer and use warmer colors")
    assert result["scenes"][1]["staging"]["camera"] == "detail"
    assert result["scenes"][1]["palette"] == "ember"
    assert result["creative_brief"] == original["creative_brief"]
    assert result["scenes"][0] == original["scenes"][0] and result["scenes"][2] == original["scenes"][2]
    for name in ("id", "section_id", "start", "duration", "beats"):
        assert result["scenes"][1][name] == original["scenes"][1][name]
    assert any("Camera:" in change for change in result["last_edit"]["changes"])
    director._write_plan(result, tmp_path)
    assert _validate_plan(result)["scenes"][1]["palette"] == "ember"
    assert original == saved


def test_unsupported_mechanism_and_inert_layout_edit_are_not_reported_as_success():
    script, timeline = project()
    original = director.plan_scenes(script, timeline, use_ai=False)
    with pytest.raises(ValueError, match="not supported by this scene"):
        director.regenerate_scene(original, "scene-001", script, "Change action to float")
    with pytest.raises(ValueError, match="visible design is unchanged"):
        director.regenerate_scene(original, "scene-001", script, "Use text")


def stock_fixture(monkeypatch, tmp_path):
    calls = []
    def fetch(query, output, orientation, duration, **kwargs):
        calls.append((query, kwargs))
        provider_id = str(100 + len(calls))
        brief = kwargs["brief"]
        receipt = {"version": 1, "provider": "pixabay", "provider_id": provider_id,
                   "profile_id": kwargs["profile_id"], "script_signature": kwargs["script_signature"], "scene_id": kwargs["scene_id"],
                   "request_sha256": "1" * 64, "sha256": hashlib.sha256(provider_id.encode()).hexdigest(), "bytes": 1000, "width": 640, "height": 360,
                   "duration": duration + 1, "query": query, **brief, "matched_subjects": brief["subject_terms"],
                   "palette_score": .6, "crop_fraction": 1, "author": "Test Author",
                   "license": stock_video.LICENSES["pixabay"][0], "license_url": stock_video.LICENSES["pixabay"][1],
                   "validation": "metadata_and_decoded_frames"}
        return {"path": str(tmp_path / (provider_id + ".mp4")), "source_url": f"https://pixabay.com/videos/solar-panels-{provider_id}/",
                "credit": "Test Author on Pixabay", "in_point": 0, "selection": receipt}
    monkeypatch.setattr(stock_video, "availability_status", lambda: {"available": True, "reason": "ready", "provider": "pixabay"})
    monkeypatch.setattr(stock_video, "fetch_video", fetch)
    return calls


def test_local_hybrid_uses_concrete_source_query_with_profile_palette_and_timing(monkeypatch, tmp_path):
    calls = stock_fixture(monkeypatch, tmp_path)
    script, timeline = project(["Solar panels convert sunlight.", "The future of humanity inspires research."])
    script["sections"][0]["image_query"] = "solar panels"
    script["sections"][1]["image_query"] = "future world technology"
    owner, stop = "a" * 32, Event()
    plan = director.plan_scenes(script, timeline, mode="hybrid", use_ai=False, output_dir=tmp_path,
                                use_project_photos=False, profile_id=owner, stop_event=stop)
    assert len(calls) == 1 and calls[0][0] == "solar panels"
    sent = calls[0][1]
    assert sent["profile_id"] == owner and sent["script_signature"] == script_signature(script)
    assert sent["scene_id"] == "scene-001" and sent["stop_event"] is stop
    assert sent["brief"]["subject_terms"] == ["solar panels"] and len(sent["brief"]["palette"]) == 5
    scene = plan["scenes"][0]
    assert scene["template"] == "footage" and "staging" not in scene and "graphics_version" not in scene
    assert len(scene["items"]) == 1
    assert _validate_plan(plan)["scenes"][0]["footage"]["selection"] == scene["footage"]["selection"]
    assert plan["stock_status"]["provider"] == "pixabay"
    assert plan["scenes"][1]["graphics_version"] == 2


def test_new_clip_excludes_previous_provider_identity_and_preserves_others(monkeypatch, tmp_path):
    calls = stock_fixture(monkeypatch, tmp_path)
    script, timeline = project(["Solar panels convert sunlight.", "Batteries store energy."])
    script["sections"][0]["video_query"] = "solar panels"
    old = director.plan_scenes(script, timeline, mode="hybrid", use_ai=False, output_dir=tmp_path)
    before = deepcopy(old)
    edited = director.regenerate_scene(old, "scene-001", script, "Use another stock clip", output_dir=tmp_path, profile_id="default")
    assert calls[-1][1]["exclude_ids"] == ("pixabay:101",)
    assert edited["scenes"][0]["footage"]["selection"]["provider_id"] == "102"
    assert edited["scenes"][1] == old["scenes"][1]
    assert edited["sections"] == old["sections"] and old == before
    monkeypatch.setattr(stock_video, "fetch_video", lambda *a, **k: None)
    with pytest.raises(ValueError, match="scene was kept"):
        director.regenerate_scene(old, "scene-001", script, "Use new footage", output_dir=tmp_path)
    assert old == before


@pytest.mark.parametrize("duplicate", ["source", "recording"])
def test_legacy_clip_replacement_does_not_accept_same_source_or_content(monkeypatch, tmp_path, duplicate):
    calls = stock_fixture(monkeypatch, tmp_path)
    script, timeline = project(["Solar panels convert sunlight."])
    script["sections"][0]["video_query"] = "solar panels"
    old = director.plan_scenes(script, timeline, mode="hybrid", use_ai=False, output_dir=tmp_path)
    footage = old["scenes"][0]["footage"]
    footage.pop("selection")  # A real pre-receipt archive, still source attributed.
    content = b"existing legacy video content" * 2
    (tmp_path / "101.mp4").write_bytes(content)
    before = deepcopy(old)
    fetch = stock_video.fetch_video
    def same(query, output, orientation, duration, **kwargs):
        result = fetch(query, output, orientation, duration, **kwargs)
        assert kwargs["exclude_ids"] == ("pixabay:101",)
        if duplicate == "source":
            result["source_url"] = footage["source_url"]
        else:
            result["selection"]["sha256"] = hashlib.sha256(content).hexdigest()
        return result
    monkeypatch.setattr(stock_video, "fetch_video", same)
    with pytest.raises(ValueError, match="same source or recording"):
        director.regenerate_scene(old, "scene-001", script, "Use another stock clip", output_dir=tmp_path)
    assert len(calls) == 2  # One original operation and one bounded edit operation.
    assert old == before


@pytest.mark.parametrize("url,expected", [
    ("https://www.pexels.com/video/a-solar-array-123/", "pexels:123"),
    ("https://pixabay.com/videos/panels-456/", "pixabay:456"),
    ("https://example.test/pexels.com/video/123/", None),
])
def test_only_trusted_provider_pages_supply_legacy_clip_ids(url, expected):
    assert director._footage_provider_id({"source_url": url}) == expected


def test_motion_never_calls_stock_and_owner_stop_boundaries_hold(monkeypatch, tmp_path):
    script, timeline = project()
    monkeypatch.setattr(stock_video, "availability_status", lambda: pytest.fail("Motion inspected stock providers"))
    plan = director.plan_scenes(script, timeline, use_ai=False, output_dir=tmp_path)
    with pytest.raises(ValueError, match="Hybrid"):
        director.regenerate_scene(plan, "scene-001", script, "Use stock footage", output_dir=tmp_path)
    with pytest.raises(ValueError, match="different channel"):
        director.regenerate_scene(plan, "scene-001", script, "Move the camera closer", profile_id="b" * 32)
    stop = Event()
    stop.set()
    with pytest.raises(InterruptedError):
        director.plan_scenes(script, timeline, stop_event=stop, output_dir=tmp_path)
