"""Archived motion projects must remain rerenderable and isolated from live jobs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import wave

import pytest

import config
from studio import create_app, description, library, upload
from studio.state import state
from video.timeline import build_timeline, script_signature


@pytest.fixture
def project(tmp_path, monkeypatch):
    previous = deepcopy(state)
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(config, "IMAGES_DIR", str(output / "images"))
    monkeypatch.setattr(library, "LIB_DIR", str(output / "library"))
    state.update(running=False, uploading=False, visual_editing=False, preview_only=False,
                 visual_plan=None, timeline=None, visual_qa=None, audio_path=None, render_audio_path=None,
                 preview_path=None, video_path=None, thumb_path=None, thumb_bg=None,
                 image_map={}, library_id=None, description="", error=None)
    app = create_app()
    app.config["TESTING"] = True
    yield output, app.test_client()
    state.clear()
    state.update(previous)


def _motion_project(output):
    script = {"title": "Packets take different routes", "tags": ["networks"],
              "sections": [{"id": index + 1, "title": name, "narration": f"Narration for {name}."}
                           for index, name in enumerate(("Split", "Travel", "Reassemble"))]}
    audio = output / "narration.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * (8000 * 30))
    timings = {"version": 1, "script_signature": script_signature(script), "duration": 30,
               "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
               "sections": [{"id": index + 1, "start": index * 10, "duration": 10, "caption_timing": "word"} for index in range(3)], "words": []}
    (output / "narration.timings.json").write_text(json.dumps(timings), encoding="utf-8")
    (output / "narration.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nSplit a message.\n", encoding="utf-8")
    timeline = build_timeline(script, str(audio), str(output))
    footage = output / "assets" / "videos" / "pexels-source.mp4"
    footage.parent.mkdir(parents=True)
    footage.write_bytes(b"licensed footage")
    scenes = [{"id": f"scene-{index + 1:03d}", "section_id": str(index + 1), "template": "process_flow",
               "start": index * 10, "duration": 10, "title": name, "visual_goal": f"Explain {name}", "items": [], "beats": []}
              for index, name in enumerate(("Split", "Travel", "Reassemble"))]
    scenes[1]["footage"] = {"path": str(footage), "source_url": "https://www.pexels.com/video/example-1/",
                            "credit": "Video by Example on Pexels", "in_point": 0}
    plan = {"version": 1, "title": script["title"], "duration": 30, "width": 1920, "height": 1080, "fps": 24,
            "sections": timeline["sections"], "scenes": scenes, "captions": timeline["captions"]}
    credits = [{"provider": "pexels", **scenes[1]["footage"]}]
    (output / "video_credits.json").write_text(json.dumps(credits), encoding="utf-8")
    (output / "credits.json").write_text("[]", encoding="utf-8")
    motion = output / "motion"
    (motion / "assets").mkdir(parents=True)
    (motion / "index.html").write_text("<html>Local preview</html>", encoding="utf-8")
    (motion / "project.json").write_text(json.dumps({"audio": {"path": "assets/narration.wav"}}), encoding="utf-8")
    (motion / "scene.js").write_text("window.sceneReady = true;", encoding="utf-8")
    shutil.copy2(audio, motion / "assets" / "narration.wav")
    shutil.copy2(footage, motion / "assets" / "footage.mp4")
    video = output / "final_video.mp4"
    video.write_bytes(b"final video")
    state.update(script=script, research={"topic": "Networks", "hook_question": "How does a message travel?"},
                 visual_mode="hybrid", visual_plan=plan, timeline=timeline,
                 visual_qa={"status": "reviewed", "passed": True, "summary": "Readable", "issues": []},
                 audio_path=str(audio), video_path=str(video), description="A saved description.")
    state["stages"]["video"] = "done"
    return script, plan


def test_archive_roundtrip_restores_portable_motion_narration_and_timing(project):
    output, client = project
    script, original_plan = _motion_project(output)
    pid = library.archive_project()
    archive = Path(library.lib_path(pid))
    archived_plan = json.loads((archive / "scene_plan.json").read_text())
    asset = archived_plan["scenes"][1]["footage"]["path"]
    assert not Path(asset).is_absolute() and (archive / asset).is_file()
    assert ".." not in Path(asset).parts
    assert (archive / "narration.wav").is_file()
    assert (archive / "narration.timings.json").is_file()
    assert (archive / "timeline.json").is_file()
    assert (archive / "motion" / "assets" / "narration.wav").is_file()
    assert (archive / "visual_qa.json").is_file()
    assert json.loads((archive / "video_credits.json").read_text())[0]["path"] == asset
    for name in ("motion", "assets"):
        shutil.rmtree(output / name)
    for name in ("narration.wav", "narration.srt", "narration.timings.json", "timeline.json"):
        (output / name).unlink()
    state.update(script={"title": "Wrong project"}, research={"topic": "Wrong"}, visual_plan=None, audio_path=None,
                 workspace_warning="Previous project has stale narration. Open its original saved video.",
                 workspace_restored=True)
    (output / "script.json").write_text('{"title":"Wrong cache"}', encoding="utf-8")
    response = client.post(f"/api/library/{pid}/load", json={})
    assert response.json == {"ok": True}
    assert state["workspace_warning"] is None and state["workspace_restored"] is False
    status = client.get("/api/status").json
    assert status["workspace_warning"] is None and status["workspace_restored"] is False
    assert state["script"] == script and state["visual_mode"] == "hybrid"
    assert state["audio_path"] == str(output / "narration.wav")
    restored_path = Path(state["visual_plan"]["scenes"][1]["footage"]["path"])
    assert restored_path.is_relative_to(output) and restored_path.read_bytes() == b"licensed footage"
    assert state["visual_plan"]["scenes"][0] == original_plan["scenes"][0]
    assert json.loads((output / "script.json").read_text()) == script
    assert json.loads((output / "research.json").read_text())["topic"] == "Networks"
    assert (output / "narration.srt").is_file()
    assert state["visual_qa"]["passed"] is True
    assert not state["preview_only"] and state["preview_path"] is None
    # A rerender can rebuild its measured clock immediately, with no TTS call.
    assert build_timeline(state["script"], state["audio_path"], str(output))["duration"] == 30


def test_loading_legacy_classic_project_clears_stale_motion_audio_and_captions(project):
    output, client = project
    _motion_project(output)
    state["preview_path"] = str(output / "motion_preview.mp4")
    (output / "motion_preview.mp4").write_bytes(b"draft")
    classic = Path(library.lib_path("legacy"))
    classic.mkdir(parents=True)
    (classic / "meta.json").write_text(json.dumps({"id": "legacy", "title": "Old classic", "files": {}}), encoding="utf-8")
    (classic / "script.json").write_text('{"title":"Old classic","sections":[]}', encoding="utf-8")
    (classic / "research.json").write_text('{"topic":"Older topic"}', encoding="utf-8")
    (classic / "video.mp4").write_bytes(b"old final")
    assert client.post("/api/library/legacy/load", json={}).json["ok"]
    assert state["visual_mode"] == "classic"
    for key in ("visual_plan", "timeline", "visual_qa", "audio_path", "render_audio_path", "preview_path"):
        assert state[key] is None
    for name in ("motion", "narration.wav", "narration.timings.json", "timeline.json", "narration.srt", "motion_preview.mp4"):
        assert not (output / name).exists(), name
    assert not client.get("/api/status").json["has_captions"]
    assert json.loads((output / "script.json").read_text())["title"] == "Old classic"


def test_library_rejects_escaping_scene_paths_before_changing_current_project(project):
    output, client = project
    _motion_project(output)
    pid = library.archive_project()
    plan_path = Path(library.lib_path(pid, "scene_plan.json"))
    plan = json.loads(plan_path.read_text())
    plan["scenes"][1]["footage"]["path"] = "../../private.mp4"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    state.update(workspace_warning="This current project was recovered without a playable draft.",
                 workspace_restored=True)
    previous_state = deepcopy(state)
    (output / "script.json").write_text('{"title":"Current cached script"}', encoding="utf-8")
    response = client.post(f"/api/library/{pid}/load", json={})
    assert not response.json["ok"]
    assert state == previous_state
    assert json.loads((output / "script.json").read_text())["title"] == "Current cached script"


def test_motion_archive_rejects_symlinked_assets(project, tmp_path):
    output, _ = project
    _motion_project(output)
    secret = tmp_path / "private.json"
    secret.write_text("private", encoding="utf-8")
    try:
        (output / "motion" / "leak.json").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    with pytest.raises(ValueError, match="symbolic"):
        library.archive_project()


def test_drafts_and_dirty_storyboards_are_not_archived(project):
    output, _ = project
    _motion_project(output)
    state["preview_only"] = True
    assert library.archive_project() is None
    state["preview_only"] = False
    state["stages"]["video"] = "pending"
    assert library.archive_project() is None


def test_archive_preserves_the_selected_soundtrack_after_setting_changes(project, monkeypatch):
    output, client = project
    _motion_project(output)
    mixed = output / "narration_mixed.mp3"
    mixed.write_bytes(b"voice plus music")
    (output / "render_audio.json").write_text(json.dumps({"path": "narration_mixed.mp3", "audio_sha256": "source-hash"}), encoding="utf-8")
    state["render_audio_path"] = str(mixed)
    pid = library.archive_project()
    assert library.read_meta(pid)["files"]["render_audio"] == "narration_mixed.mp3"
    monkeypatch.setattr(config, "MUSIC_ENABLED", False)
    mixed.unlink()
    assert client.post(f"/api/library/{pid}/load", json={}).json["ok"]
    assert Path(state["render_audio_path"]).read_bytes() == b"voice plus music"
    assert json.loads((output / "render_audio.json").read_text())["path"] == "narration_mixed.mp3"


@pytest.mark.parametrize("flag", ["running", "visual_editing", "uploading"])
def test_mutation_routes_reject_busy_projects(project, flag):
    _, client = project
    state[flag] = True
    for path in ("/api/save-output", "/api/regenerate-description", "/api/thumbnail",
                 "/api/regenerate-thumbnail", "/api/title-ideas", "/api/library/missing/load", "/api/upload"):
        assert client.post(path, json={}).status_code == 409, path
    assert client.delete("/api/library/missing").status_code == 409
    # Read-only scoring is still useful during a run and must not mutate it.
    assert client.post("/api/seo", json={"title": "A title", "description": "", "tags": []}).status_code == 200


def test_upload_reserves_the_job_before_thread_start(project, monkeypatch):
    output, client = project
    video = output / "final_video.mp4"
    video.write_bytes(b"final")
    state.update(video_path=str(video), script={"title": "Title"})
    monkeypatch.setattr(upload, "secret_ok", lambda *args: True)
    from studio import channels
    monkeypatch.setattr(channels, "upload_options", lambda profile_id: {
        "token_file": "test-token", "client_secret_file": "test-client",
        "expected_channel_id": "UC" + "x" * 22, "interactive": False})
    class Thread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            assert state["uploading"]
    monkeypatch.setattr(upload.threading, "Thread", Thread)
    assert client.post("/api/upload", json={"privacy": "private"}).json == {"ok": True}
    assert client.post("/api/upload", json={"privacy": "private"}).status_code == 409


def test_upload_rejects_preview_or_dirty_plan_even_with_an_old_final(project):
    output, client = project
    _motion_project(output)
    state["preview_only"] = True
    assert client.post("/api/upload", json={}).status_code == 409
    state["preview_only"] = False
    state["stages"]["video"] = "pending"
    assert client.post("/api/upload", json={}).status_code == 409


def test_chapters_use_measured_boundaries_and_reject_stale_timing(project, monkeypatch):
    output, _ = project
    script, _ = _motion_project(output)
    monkeypatch.setattr(description, "load_credits", lambda: [])
    built = description.build_description(script, {}, timeline=state["timeline"])
    assert "0:00 Split" in built and "0:10 Travel" in built and "0:20 Reassemble" in built
    assert "CHAPTERS" not in description.build_description(script, {})
    stale = {**state["timeline"], "script_signature": "wrong"}
    assert "CHAPTERS" not in description.build_description(script, {}, timeline=stale)
    assert "CHAPTERS" not in description.build_description(script, {}, "shorts", timeline=state["timeline"])


def test_pexels_video_contributor_and_source_survive_description(project):
    output, _ = project
    script, _ = _motion_project(output)
    built = description.build_description(script, {}, timeline=state["timeline"])
    assert "Video by Example on Pexels" in built
    assert "https://www.pexels.com/video/example-1/" in built
