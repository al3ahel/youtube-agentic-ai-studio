"""Motion studio contracts: offline preparation, safe previews and scoped edits."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import types

import pytest

import config
from studio import create_app, pipeline, preflight, visuals
from studio.state import state, stop_event


@pytest.fixture
def client(tmp_path, monkeypatch):
    previous = deepcopy(state)
    state.update(running=False, visual_editing=False, uploading=False,
                 visual_plan=None, preview_path=None, video_path=None, error=None)
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(config, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    app = create_app()
    app.config["TESTING"] = True
    yield app.test_client()
    state.clear()
    state.update(previous)
    stop_event.clear()


def test_research_preflight_does_not_need_render_or_speech(monkeypatch):
    checked = []
    monkeypatch.setattr(preflight, "_available", lambda module: checked.append(module) or True)
    monkeypatch.setattr(preflight, "_node_version", lambda: pytest.fail("Node is unrelated to research"))
    result = preflight.check_environment(["research", "script"], "motion")
    assert result["ok"]
    assert checked == ["requests"]


def test_missing_tts_fix_uses_running_interpreter(monkeypatch):
    monkeypatch.setattr(preflight, "_available", lambda module: False)
    monkeypatch.setattr(sys, "executable", "/some Python/venv/bin/python")
    result = preflight.check_environment(["narration"], "motion")
    missing = next(check for check in result["checks"] if check["name"] == "edge_tts")
    assert not result["ok"] and not missing["ok"]
    assert '"/some Python/venv/bin/python" -m pip install "edge-tts>=7.3.1"' in missing["fix"]
    assert not any(check["name"] == "moviepy" for check in result["checks"])


def test_motion_preflight_requires_local_renderer_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT_DIR", str(tmp_path))
    monkeypatch.setattr(preflight, "_available", lambda module: True)
    monkeypatch.setattr(preflight, "_node_version", lambda: (20, 0, 0))
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    result = preflight.check_environment(["video"], "motion")
    failed = {item["name"] for item in result["checks"] if not item["ok"]}
    assert failed == {"node", "ffmpeg", "ffprobe", "renderer_dependencies"}
    assert not any(item["name"] == "moviepy" for item in result["checks"])


def test_preflight_api_is_read_only_and_validates_mode(client, monkeypatch):
    monkeypatch.setattr(preflight, "check_environment", lambda steps, mode: {"ok": True, "checks": []})
    assert client.post("/api/preflight", json={"steps": ["script"], "visual_mode": "motion"}).json["ok"]
    assert client.post("/api/preflight", json={"steps": ["script"], "visual_mode": "remote-url"}).status_code == 400
    assert not state["running"]


def test_motion_defaults_and_saved_preferences_are_honoured(tmp_path):
    assert config.DEFAULTS["VISUAL_MODE"] == "motion"
    assert config.DEFAULTS["OPENROUTER_MODEL"] == "openrouter/free"
    assert config.DEFAULTS["VISUAL_REVIEW_ENABLED"] is False
    saved = tmp_path / "saved.json"
    saved.write_text(json.dumps({"VISUAL_MODE": "classic", "OPENROUTER_MODEL": "existing/model:free"}), encoding="utf-8")
    assert config.load_settings(str(saved)) == {"VISUAL_MODE": "classic", "OPENROUTER_MODEL": "existing/model:free"}


def test_settings_reject_invalid_mode_before_memory_changes(client, monkeypatch):
    monkeypatch.setattr(config, "VISUAL_MODE", "motion")
    response = client.post("/api/save-settings", json={"visual_mode": "arbitrary", "motion_workers": 2})
    assert response.status_code == 400
    assert config.VISUAL_MODE == "motion"
    assert not Path(config.SETTINGS_PATH).exists()


@pytest.mark.parametrize("value", [0, 5, True, 1.5, "many"])
def test_render_workers_are_bounded(client, value):
    assert client.post("/api/save-settings", json={"motion_workers": value}).status_code == 400


def test_active_jobs_block_settings_changes(client):
    state["running"] = True
    assert client.post("/api/save-settings", json={"visual_mode": "classic"}).status_code == 409


def test_pipeline_reserves_job_before_starting_thread(client, monkeypatch):
    threads = []
    class Thread:
        def __init__(self, **kwargs):
            threads.append(kwargs)
        def start(self):
            assert state["running"]
    monkeypatch.setattr(pipeline.threading, "Thread", Thread)
    response = client.post("/api/run", json={"steps": ["video"], "visual_mode": "motion", "preview_only": True})
    assert response.json == {"ok": True}
    assert threads[0]["kwargs"]["visual_mode"] == "motion"
    assert threads[0]["kwargs"]["preview_only"] is True
    assert threads[0]["kwargs"]["profile_id"] == "default"
    assert client.post("/api/run", json={"steps": ["video"]}).status_code == 409


def test_bad_run_input_is_rejected_before_thread_creation(client, monkeypatch):
    monkeypatch.setattr(pipeline.threading, "Thread", lambda **kwargs: pytest.fail("Must not start an invalid request"))
    assert client.post("/api/run", json={"steps": ["../../file"]}).status_code == 400
    assert client.post("/api/run", json={"steps": ["video"], "preview_only": "yes"}).status_code == 400
    assert client.post("/api/run", json={"steps": ["video"], "visual_mode": "classic", "preview_only": True}).status_code == 400


def test_preview_serves_local_assets_with_sandbox_and_no_api_cors(client):
    motion = Path(config.OUTPUT_DIR) / "motion"
    motion.mkdir()
    (motion / "index.html").write_text("<html>preview</html>", encoding="utf-8")
    response = client.get("/api/visuals/preview/index.html")
    assert response.status_code == 200
    assert "sandbox allow-scripts" in response.headers["Content-Security-Policy"]
    assert "allow-same-origin" not in response.headers["Content-Security-Policy"]
    assert response.headers["Access-Control-Allow-Origin"] == "null"
    assert "Access-Control-Allow-Origin" not in client.get("/api/settings").headers
    response.close()


@pytest.mark.parametrize("path", ["../secret.json", "%2e%2e/secret.json", "..%5csecret.json", "C:%5csecret.json", ".env", "code.py"])
def test_preview_rejects_traversal_and_non_assets(client, path):
    motion = Path(config.OUTPUT_DIR) / "motion"
    motion.mkdir(exist_ok=True)
    (Path(config.OUTPUT_DIR) / "secret.json").write_text("secret", encoding="utf-8")
    (motion / ".env").write_text("secret", encoding="utf-8")
    (motion / "code.py").write_text("secret", encoding="utf-8")
    assert client.get("/api/visuals/preview/" + path).status_code == 404


def test_preview_cannot_follow_a_symlink_outside_motion(client, tmp_path):
    motion = Path(config.OUTPUT_DIR) / "motion"
    motion.mkdir()
    secret = tmp_path / "secret.json"
    secret.write_text("secret", encoding="utf-8")
    try:
        (motion / "linked.json").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable on this platform")
    assert client.get("/api/visuals/preview/linked.json").status_code == 404


def test_preview_video_is_distinct_and_confined_to_output(client, tmp_path):
    outside = tmp_path / "other.mp4"
    outside.write_bytes(b"not a real video")
    state["preview_path"] = str(outside)
    assert client.get("/preview-video").status_code == 404
    inside = Path(config.OUTPUT_DIR) / "motion_preview.mp4"
    inside.write_bytes(b"preview")
    state["preview_path"] = str(inside)
    response = client.get("/preview-video?download=1")
    assert response.status_code == 200
    assert "motion-draft-preview.mp4" in response.headers["Content-Disposition"]
    response.close()
    status = client.get("/api/status").json
    assert status["has_preview"] and not status["has_video"]


def _model_catalogue(monkeypatch):
    module = types.ModuleType("apis.openrouter")
    module.discover_models = lambda: [
        {"id": f"provider/free-{i}", "name": f"Free {i}", "free": True, "supports_schema": True,
         "supports_vision": i % 2 == 0, "input_modalities": ["text", "image"]} for i in range(60)
    ] + [{"id": "paid/model", "free": False, "supports_schema": True, "supports_vision": True}]
    monkeypatch.setitem(sys.modules, "apis.openrouter", module)


def test_model_discovery_is_bounded_free_and_capability_filtered(client, monkeypatch):
    _model_catalogue(monkeypatch)
    response = client.get("/api/visuals/models?purpose=reviewer&limit=4")
    data = response.json
    assert data["ok"] and len(data["models"]) == 4 and data["total"] == 30
    assert all(model["free"] and model["supports_vision"] for model in data["models"])
    assert client.get("/api/visuals/models?limit=1000").status_code == 400
    assert client.get("/api/visuals/models?purpose=image-generator").status_code == 400


def _editable_plan(monkeypatch):
    output = Path(config.OUTPUT_DIR)
    motion = output / "motion"
    motion.mkdir()
    (motion / "index.html").write_text("original preview", encoding="utf-8")
    audio = output / "narration.mp3"
    audio.write_bytes(b"audio")
    from agents.visual_director import plan_scenes, plan_uses_project_photos, validate_plan
    script = {"title": "A script", "sections": [{
        "id": 1, "title": "Original", "narration": "First observe the pattern. Then compare the evidence.",
    }]}
    timeline = {"fps": 24, "width": 640, "height": 360, "duration": 5,
                "sections": [{"id": 1, "start": 0, "duration": 5}], "captions": []}
    plan = plan_scenes(script, timeline, use_ai=False)
    state.update(visual_plan=deepcopy(plan), script=script, timeline=timeline,
                 audio_path=str(audio), video_path="previous-final.mp4")
    module = types.ModuleType("agents.visual_director")
    module.validate_plan = validate_plan
    module.plan_uses_project_photos = plan_uses_project_photos
    def regenerate(original, scene_id, script, instructions=""):
        assert state["visual_editing"] and not state["running"]
        assert scene_id == "scene-001" and script["title"] == "A script"
        updated = deepcopy(original)
        updated["scenes"][0]["title"] = instructions or "Updated"
        return updated
    module.regenerate_scene = regenerate
    monkeypatch.setitem(sys.modules, "agents.visual_director", module)
    renderer = types.ModuleType("video.renderers.html")
    def prepare(updated, audio_path, output_dir):
        assert Path(audio_path).read_bytes() == b"audio"
        project = Path(output_dir) / "motion"
        project.mkdir()
        (project / "index.html").write_text(updated["scenes"][0]["title"], encoding="utf-8")
        return project / "index.html"
    renderer.prepare_motion_project = prepare
    monkeypatch.setitem(sys.modules, "video.renderers.html", renderer)
    return plan, renderer


def test_single_scene_edit_rebuilds_preview_and_invalidates_final(client, monkeypatch):
    original, _ = _editable_plan(monkeypatch)
    response = client.post("/api/visuals/scenes/scene-001/regenerate", json={"instructions": "Improved"})
    assert response.status_code == 200 and response.json["ok"]
    assert state["visual_plan"]["scenes"][0]["title"] == "Improved"
    assert state["visual_plan"]["scenes"][0]["duration"] == original["scenes"][0]["duration"]
    assert state["video_path"] is None and state["preview_path"] is None
    assert not state["visual_editing"]
    output = Path(config.OUTPUT_DIR)
    assert (output / "motion" / "index.html").read_text() == "Improved"
    assert json.loads((output / "scene_plan.json").read_text())["scenes"][0]["title"] == "Improved"
    assert not list(output.glob(".motion-edit-*"))


def test_failed_scene_edit_keeps_previous_plan_and_preview(client, monkeypatch):
    original, renderer = _editable_plan(monkeypatch)
    def fail(*args, **kwargs):
        raise RuntimeError("Renderer missing")
    renderer.prepare_motion_project = fail
    response = client.post("/api/visuals/scenes/scene-001/regenerate", json={})
    assert response.status_code == 422
    assert state["visual_plan"] == original and state["video_path"] == "previous-final.mp4"
    assert (Path(config.OUTPUT_DIR) / "motion" / "index.html").read_text() == "original preview"
    assert not state["visual_editing"]


def test_scene_edits_reject_active_runs_external_paths_and_unknown_ids(client, monkeypatch):
    _editable_plan(monkeypatch)
    assert client.post("/api/visuals/scenes/scene-001/regenerate", json={"audio_path": "/private/file"}).status_code == 400
    assert client.post("/api/visuals/scenes/missing/regenerate", json={}).status_code == 404
    state["running"] = True
    assert client.post("/api/visuals/scenes/scene-001/regenerate", json={}).status_code == 409
    assert client.post("/api/visuals/scenes/scene-001/regenerate", json={}, headers={"Origin": "null"}).status_code == 403


def test_page_has_motion_controls_and_opaque_preview_iframe(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="visual-mode"' in html and 'id="draft-preview"' in html
    assert 'id="s-visual_review_enabled"' in html
    assert 'sandbox="allow-scripts"' in html and "allow-same-origin" not in html
    assert 'src="/static/js/visuals.js?v=' in html


def _review_files():
    output = Path(config.OUTPUT_DIR)
    return {path.relative_to(output).as_posix(): path.read_bytes()
            for path in output.rglob("*") if path.is_file()}


def _forbid_review_mutations(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A rejected Review request must not edit files or start work")
    monkeypatch.setattr(sys.modules["agents.visual_director"], "regenerate_scene", forbidden)
    monkeypatch.setattr(sys.modules["video.renderers.html"], "prepare_motion_project", forbidden)
    monkeypatch.setattr(visuals, "_save_plan", forbidden)
    monkeypatch.setattr(pipeline.threading, "Thread", forbidden)


@pytest.mark.parametrize("route,options", [
    ("/api/visuals/scenes/scene-001/regenerate", {"instructions": "Updated"}),
    ("/api/run", {"steps": ["video"], "visual_mode": "motion", "preview_only": True}),
    ("/api/run", {"steps": ["visuals", "video"], "visual_mode": "motion", "preview_only": True, "use_project_photos": False}),
])
@pytest.mark.parametrize("revision", [False, 7, "not-a-revision", "a" * 63, "G" * 64])
def test_malformed_storyboard_revision_rejects_before_any_mutation(
        client, monkeypatch, route, options, revision):
    _editable_plan(monkeypatch)
    _forbid_review_mutations(monkeypatch)
    before, files = deepcopy(state), _review_files()
    response = client.post(route, json={**options, "expected_plan_revision": revision})
    assert response.status_code == 400
    assert response.json["ok"] is False
    assert state == before and _review_files() == files


@pytest.mark.parametrize("route,options", [
    ("/api/visuals/scenes/scene-001/regenerate", {"instructions": "Updated"}),
    ("/api/run", {"steps": ["video"], "visual_mode": "motion", "preview_only": True}),
    ("/api/run", {"steps": ["visuals", "video"], "visual_mode": "motion", "preview_only": True, "use_project_photos": False}),
])
def test_stale_storyboard_revision_rejects_without_losing_current_edits(
        client, monkeypatch, route, options):
    _editable_plan(monkeypatch)
    stale_revision = client.get("/api/visuals").json["plan_revision"]
    state["visual_plan"]["scenes"][0]["title"] = "Saved in another tab"
    _forbid_review_mutations(monkeypatch)
    before, files = deepcopy(state), _review_files()
    response = client.post(route, json={**options, "expected_plan_revision": stale_revision})
    assert response.status_code == 409
    assert response.json["ok"] is False
    assert state == before and _review_files() == files


@pytest.mark.parametrize("module,route,options", [
    (visuals, "/api/visuals/scenes/scene-001/regenerate", {"instructions": "Updated"}),
    (pipeline, "/api/run", {"steps": ["video"], "visual_mode": "motion", "preview_only": True}),
    (pipeline, "/api/run", {"steps": ["visuals", "video"], "visual_mode": "motion", "preview_only": True, "use_project_photos": False}),
])
def test_revision_guard_checks_current_storyboard_after_acquiring_job_lock(
        client, monkeypatch, module, route, options):
    from contextlib import contextmanager
    _editable_plan(monkeypatch)
    stale_revision = client.get("/api/visuals").json["plan_revision"]
    _forbid_review_mutations(monkeypatch)
    before, files = deepcopy(state), _review_files()
    before["visual_plan"]["scenes"][0]["title"] = "Changed while waiting for the lock"

    @contextmanager
    def concurrent_edit():
        # A request may wait for another editor between validation and reservation.
        state["visual_plan"] = deepcopy(before["visual_plan"])
        yield

    monkeypatch.setattr(module, "job_lock", concurrent_edit())
    response = client.post(route, json={**options, "expected_plan_revision": stale_revision})
    assert response.status_code == 409
    assert state == before and _review_files() == files


def test_storyboard_revision_changes_for_same_scene_ids_and_count(client, monkeypatch):
    original, _ = _editable_plan(monkeypatch)
    first = client.get("/api/visuals").json
    assert len(first["plan_revision"]) == 64
    assert set(first["plan_revision"]) <= set("0123456789abcdef")
    assert first["plan_revision"] == client.get("/api/status").json["plan_revision"]
    assert client.get("/api/visuals").json["plan_revision"] == first["plan_revision"]
    state["visual_plan"]["scenes"][0]["title"] = "Another saved project with the same scene IDs"
    second = client.get("/api/visuals").json
    assert len(second["plan"]["scenes"]) == len(original["scenes"])
    assert second["plan"]["scenes"][0]["id"] == original["scenes"][0]["id"]
    assert second["revision"] == first["revision"]
    assert second["plan_revision"] != first["plan_revision"]
    assert second["preview_revision"] == first["preview_revision"]
    assert client.get("/api/status").json["plan_revision"] == second["plan_revision"]


def test_media_revisions_refresh_same_path_equal_size_replacements(client, monkeypatch):
    import os
    _editable_plan(monkeypatch)
    output = Path(config.OUTPUT_DIR)
    html_path = output / "motion" / "index.html"
    draft = output / "motion_preview.mp4"
    final = output / "final_video.mp4"
    draft.write_bytes(b"old draft")
    final.write_bytes(b"old final")
    state.update(preview_path=str(draft), video_path=str(final))
    visuals_before = client.get("/api/visuals").json
    status_before = client.get("/api/status").json
    assert status_before["preview_revision"] and status_before["video_revision"]
    for path in (html_path, draft, final):
        stat = path.stat()
        path.write_bytes(b"x" * stat.st_size)
        # Explicitly advance the clock; the test must not rely on timer precision.
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    visuals_after = client.get("/api/visuals").json
    status_after = client.get("/api/status").json
    assert visuals_after["plan_revision"] == visuals_before["plan_revision"]
    assert visuals_after["preview_revision"] != visuals_before["preview_revision"]
    assert status_after["preview_revision"] != status_before["preview_revision"]
    assert status_after["video_revision"] != status_before["video_revision"]
    response = client.get("/preview-video?v=" + status_after["preview_revision"])
    assert response.status_code == 200 and response.data == draft.read_bytes()
    assert response.headers["Cache-Control"] == "no-store"
    response.close()
    draft.unlink()
    final.unlink()
    missing = client.get("/api/status").json
    assert missing["preview_revision"] is None and missing["video_revision"] is None
    assert not missing["has_preview"] and not missing["has_video"]


@pytest.mark.parametrize("expected", ["current", None])
def test_scene_edit_returns_fresh_revisions_and_discards_previous_qa(client, monkeypatch, expected):
    _editable_plan(monkeypatch)
    output = Path(config.OUTPUT_DIR)
    qa_file = output / "visual_qa.json"
    qa_file.write_text(json.dumps({"passed": True, "summary": "Previous frames"}), encoding="utf-8")
    state["visual_qa"] = {"passed": True, "summary": "Previous frames"}
    before = client.get("/api/visuals").json
    response = client.post("/api/visuals/scenes/scene-001/regenerate", json={
        "instructions": "An updated visual direction", "expected_plan_revision":
        before["plan_revision"] if expected == "current" else None,
    })
    assert response.status_code == 200
    after = client.get("/api/visuals").json
    assert response.json["plan_revision"] == after["plan_revision"] != before["plan_revision"]
    assert response.json["preview_revision"] == after["preview_revision"] != before["preview_revision"]
    assert after["qa"] is None and not qa_file.exists()
    status = client.get("/api/status").json
    assert status["plan_revision"] == after["plan_revision"]
    assert status["preview_revision"] is None and status["video_revision"] is None


@pytest.mark.parametrize("replace_image", ["true", 1, None, {}])
def test_invalid_saved_photo_option_is_rejected_without_mutation(client, monkeypatch, replace_image):
    _editable_plan(monkeypatch)
    _forbid_review_mutations(monkeypatch)
    before, files = deepcopy(state), _review_files()
    response = client.post("/api/visuals/scenes/scene-001/regenerate", json={"replace_image": replace_image})
    assert response.status_code == 400
    assert state == before and _review_files() == files


def test_photo_replacement_passes_isolated_saved_images_and_project_output(client, monkeypatch):
    _editable_plan(monkeypatch)
    saved_images = {"1": [str(Path(config.OUTPUT_DIR) / "images" / "saved-photo.png")]}
    state["image_map"] = deepcopy(saved_images)
    received = []

    def regenerate(plan, scene_id, script, instructions="", **options):
        received.append(deepcopy(options))
        options["image_map"]["1"].append("must not alter the project's cached images")
        updated = deepcopy(plan)
        updated["scenes"][0]["title"] = "A different saved picture"
        return updated

    monkeypatch.setattr(sys.modules["agents.visual_director"], "regenerate_scene", regenerate)
    response = client.post("/api/visuals/scenes/scene-001/regenerate", json={"replace_image": True})
    assert response.status_code == 200
    assert received == [{"replace_image": True, "image_map": saved_images,
                         "output_dir": str(Path(config.OUTPUT_DIR).resolve())}]
    assert state["image_map"] == saved_images


def test_storyboard_guard_cannot_be_used_to_generate_new_content(client, monkeypatch):
    _editable_plan(monkeypatch)
    revision = client.get("/api/visuals").json["plan_revision"]
    _forbid_review_mutations(monkeypatch)
    before, files = deepcopy(state), _review_files()
    response = client.post("/api/run", json={
        "steps": ["research", "script", "narration", "visuals", "video"], "visual_mode": "motion", "preview_only": True,
        "expected_plan_revision": revision,
    })
    assert response.status_code == 400
    assert state == before and _review_files() == files


@pytest.mark.parametrize("use_project_photos", [False, True])
def test_guarded_visual_rebuild_accepts_existing_photo_storyboard(client, monkeypatch, use_project_photos):
    from PIL import Image
    _editable_plan(monkeypatch)
    picture = Path(config.OUTPUT_DIR) / "saved-photo.png"
    Image.new("RGB", (64, 36), "#266ca0").save(picture)
    state["visual_plan"]["use_project_photos"] = True
    state["visual_plan"]["scenes"][0]["image"] = {"path": str(picture), "source": "local"}
    original = deepcopy(state["visual_plan"])
    files = _review_files()
    revision = client.get("/api/visuals").json["plan_revision"]
    assert revision
    received = []
    def worker(*args, **kwargs):
        received.append((args, kwargs))
        state["running"] = False
    class InlineThread:
        def __init__(self, target, args, kwargs, **options):
            self.target, self.args, self.kwargs = target, args, kwargs
        def start(self):
            self.target(*self.args, **self.kwargs)
    monkeypatch.setattr(pipeline, "run_pipeline_thread", worker)
    monkeypatch.setattr(pipeline.threading, "Thread", InlineThread)
    response = client.post("/api/run", json={
        "steps": ["visuals", "video"], "visual_mode": "motion", "preview_only": True,
        "use_project_photos": use_project_photos, "expected_plan_revision": revision,
    })
    assert response.status_code == 200 and response.json["ok"]
    assert len(received) == 1
    assert received[0][0][:3] == (["visuals", "video"], "", "normal")
    assert received[0][1]["use_project_photos"] is use_project_photos
    assert received[0][1]["visual_mode"] == "motion" and received[0][1]["preview_only"]
    assert state["visual_plan"] == original and _review_files() == files
