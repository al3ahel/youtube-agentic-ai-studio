"""Integration boundaries: preparation, cache identity, preview and final output."""
import copy
import json
import shutil
import wave
from pathlib import Path

import pytest

import config
from studio import runner
from studio.state import state, stop_event
from video.timeline import script_signature


@pytest.fixture
def job(tmp_path, monkeypatch):
    previous = copy.deepcopy(state)
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "IMAGES_DIR", str(tmp_path / "images"))
    monkeypatch.setattr(config, "MUSIC_ENABLED", False)
    monkeypatch.setattr(config, "MUSIC_TRACK", "")
    monkeypatch.setattr(config, "SFX_ENABLED", False)
    monkeypatch.setattr(config, "SFX_TRACK", "")
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", False)
    monkeypatch.setattr(config, "VISUAL_REVIEW_ENABLED", False)
    from studio import preflight
    from video.renderers import html
    monkeypatch.setattr(preflight, "check_environment", lambda *args: {"ok": True, "checks": []})
    monkeypatch.setattr(html, "prepare_motion_project", lambda *args: tmp_path / "motion" / "index.html")
    monkeypatch.setattr(runner, "make_thumbnail", lambda *args: None)
    archived = []
    monkeypatch.setattr(runner, "archive_project", lambda: archived.append(True))
    script = {"title": "From idea to evidence", "sections": [
        {"id": 1, "title": "Test an idea", "narration": "Observe a pattern. Test the idea. Compare the evidence."}]}
    with wave.open(str(tmp_path / "narration.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b"\x00\x00" * 48000 * 6)
    (tmp_path / "narration.timings.json").write_text(json.dumps({
        "version": 1, "script_signature": script_signature(script), "duration": 6,
        "sections": [{"id": 1, "start": 0, "duration": 6, "caption_timing": "estimated"}], "words": []}))
    (tmp_path / "script.json").write_text(json.dumps(script))
    state.update(script=script, research={}, visual_plan=None, timeline=None, audio_path=None,
                 video_path=None, preview_path=None, image_map={}, error=None, visual_qa=None,
                 running=False, library_id=None, visual_editing=False, description=None, thumb_path=None)
    stop_event.clear()
    yield tmp_path, script, archived
    state.clear()
    state.update(previous)
    stop_event.clear()


def test_preview_then_final_reuses_edited_storyboard_without_provider_or_voice(job, monkeypatch):
    from agents import visual_director
    from video import narrator
    from video.renderers import html
    path, script, archived = job
    seen = []
    def render(script, audio, output, plan, **kwargs):
        seen.append(copy.deepcopy(plan))
        target = Path(output) / ("motion_preview.mp4" if kwargs["preview"] else "final_video.mp4")
        target.write_bytes(b"rendered")
        return str(target)
    monkeypatch.setattr(html, "render_motion_video", render)
    runner.run_pipeline_thread(["visuals", "video"], visual_mode="motion", preview_only=True)
    assert state["error"] is None
    assert state["preview_path"].endswith("motion_preview.mp4")
    assert state["video_path"] is None and not archived
    assert state["visual_qa"]["status"] == "disabled"
    state["visual_plan"]["scenes"][0]["title"] = "An edited scene title"
    def forbidden(*args, **kwargs):
        pytest.fail("A render-only run must not call a planner or synthesize speech")
    monkeypatch.setattr(visual_director, "plan_scenes", forbidden)
    monkeypatch.setattr(narrator, "generate_narration", forbidden)
    runner.run_pipeline_thread(["video"], visual_mode="motion")
    assert state["error"] is None
    assert seen[-1]["scenes"][0]["title"] == "An edited scene title"
    assert state["video_path"].endswith("final_video.mp4")
    assert archived == [True]


def test_render_only_rejects_mode_changed_or_missing_plan(job, monkeypatch):
    from video.renderers import html
    monkeypatch.setattr(html, "render_motion_video", lambda *a, **k: pytest.fail("No matching plan"))
    runner.run_pipeline_thread(["video"], visual_mode="hybrid")
    assert "No matching storyboard" in state["error"]
    assert not state["running"]


def test_changed_script_cannot_reuse_old_audio(job):
    state["script"]["sections"][0]["narration"] = "Changed words."
    runner.run_pipeline_thread(["visuals"], visual_mode="motion")
    assert "script changed" in state["error"]


def test_same_length_replacement_audio_cannot_reuse_old_storyboard(job):
    path, _, _ = job
    runner.run_pipeline_thread(["visuals"], visual_mode="motion")
    assert state["error"] is None
    voice = path / "narration.wav"
    samples = bytearray(voice.read_bytes())
    samples[-2:] = b"\x01\x00"
    voice.write_bytes(samples)
    runner.run_pipeline_thread(["video"], visual_mode="motion")
    assert "No matching storyboard" in state["error"]


def test_render_only_preserves_prepared_soundtrack_after_global_setting_change(job, monkeypatch):
    from video.renderers import html
    path, _, _ = job
    runner.run_pipeline_thread(["visuals"], visual_mode="motion")
    mixed = path / "narration_mixed.mp3"
    mixed.write_bytes(b"prepared soundtrack")
    runner._save_json(path / "render_audio.json", {
        "path": mixed.name, "audio_sha256": state["timeline"]["audio_sha256"]})
    seen = []
    def render(script, audio, output, plan, **kwargs):
        seen.append(audio)
        target = path / "final_video.mp4"
        target.write_bytes(b"video")
        return str(target)
    monkeypatch.setattr(html, "render_motion_video", render)
    monkeypatch.setattr(config, "MUSIC_ENABLED", False)
    runner.run_pipeline_thread(["video"], visual_mode="motion")
    assert seen == [str(mixed)]
    assert state["error"] is None
    # Explicitly selecting Music applies the new disabled setting.
    runner.run_pipeline_thread(["music", "video"], visual_mode="motion")
    assert seen[-1] == str(path / "narration.wav")


def test_preparation_error_is_reported_at_named_stage(job, monkeypatch):
    from studio import preflight
    monkeypatch.setattr(preflight, "check_environment", lambda *args: {
        "ok": False, "checks": [{"ok": False, "message": "edge_tts missing", "fix": "python -m pip install edge-tts"}]} )
    runner.run_pipeline_thread(["narration"], visual_mode="motion")
    assert state["stages"]["preparation"] == "error"
    assert "python -m pip install edge-tts" in state["error"]
    assert not state["running"]


def test_stop_arriving_before_worker_is_not_lost(job):
    stop_event.set()
    runner.run_pipeline_thread(["visuals", "video"], visual_mode="motion")
    assert stop_event.is_set() and state["error"] is None
    assert state["visual_plan"] is None and not state["running"]


def test_research_only_run_does_not_require_voice_or_video(job, monkeypatch):
    from agents import researcher
    monkeypatch.setattr(researcher, "research_topic", lambda *a, **k: {"video_title": "Fresh topic"})
    runner.run_pipeline_thread(["research"], "Fresh topic", visual_mode="motion")
    assert state["error"] is None
    assert state["research"]["video_title"] == "Fresh topic"
    assert state["script"] is None and state["audio_path"] is None


@pytest.mark.parametrize("section_id", [1, "intro"])
@pytest.mark.parametrize("video_type", ["normal", "shorts"])
def test_classic_render_and_thumbnail_reuse_cached_pictures_with_numeric_or_text_ids(
        job, monkeypatch, section_id, video_type):
    """Exercise the worker, classic image lookup and thumbnail selection together."""
    from moviepy.editor import AudioClip, ImageClip, VideoClip
    from PIL import Image
    from studio import thumbs
    from video import creator, thumbnail

    output, script, archived = job
    script["sections"][0]["id"] = section_id
    # Existing pre-Motion narration has no measured sidecar and must be reused.
    (output / "narration.timings.json").unlink()
    image_dir = output / "images"
    image_dir.mkdir()
    picture = image_dir / "cached.png"
    Image.new("RGB", (64, 36), (35, 90, 140)).save(picture)
    runner._save_json(output / "image_map.json", {str(section_id): ["images/cached.png"]})
    monkeypatch.setattr(config, "VIDEO_WIDTH", 64)
    monkeypatch.setattr(config, "VIDEO_HEIGHT", 36)
    monkeypatch.setattr(config, "SHORTS_WIDTH", 36)
    monkeypatch.setattr(config, "SHORTS_HEIGHT", 64)
    # Keep actual clip composition/image loading; replace encoding and speech
    # decoding so this regression remains small and independent of FFmpeg.
    audio = AudioClip(lambda t: 0, duration=6, fps=48000)
    monkeypatch.setattr(creator, "AudioFileClip", lambda path: audio)
    loaded = []
    def picture_clip(images, duration):
        assert len(images) == 1, "The saved section photo must reach the classic renderer"
        loaded.append(images[0][0, 0].tolist())
        return ImageClip(images[0]).set_duration(duration)
    monkeypatch.setattr(creator, "_make_clip", lambda section, duration, images, *a, **k: picture_clip(images, duration))
    monkeypatch.setattr(creator, "_build_shorts_clip", lambda images, duration, captions: picture_clip(images, duration))
    monkeypatch.setattr(creator, "_apply_watermark", lambda clip, *a: clip)
    monkeypatch.setattr(VideoClip, "write_videofile", lambda clip, filename, **k: Path(filename).write_bytes(b"encoded test output"))
    backgrounds = []
    def render_thumb(style, path, text, background, *a, **k):
        backgrounds.append(background)
        Path(path).write_bytes(b"thumbnail fixture")
        return path
    monkeypatch.setattr(thumbnail, "render", render_thumb)
    monkeypatch.setattr(runner, "make_thumbnail", thumbs.make_thumbnail)
    runner.run_pipeline_thread(["video"], visual_mode="classic", video_type=video_type)
    assert state["error"] is None
    assert loaded == [[35, 90, 140]]
    assert backgrounds == [str(picture)]
    assert Path(state["video_path"]).is_file() and Path(state["thumb_path"]).is_file()
    assert state["timeline"]["narration_reused"] is True
    assert archived == [True]


@pytest.mark.parametrize("saved_description", ["My carefully edited publishing description.", ""])
@pytest.mark.parametrize("legacy_photo_plan", [False, True])
def test_scene_edit_then_video_only_draft_reuses_audio_plan_and_publishing_edits(
        job, monkeypatch, saved_description, legacy_photo_plan):
    """Review saves one scene, then the guarded render encodes that exact plan."""
    from PIL import Image
    from agents import visual_director
    from studio import create_app, pipeline
    from video import narrator
    from video.renderers import html

    output, script, archived = job
    script["sections"] = [
        {"id": 1, "title": "Observe", "narration": "Observe the pattern. Test the idea."},
        {"id": 2, "title": "Compare", "narration": "Compare the evidence. Record the result."},
    ]
    (output / "script.json").write_text(json.dumps(script), encoding="utf-8")
    (output / "narration.timings.json").write_text(json.dumps({
        "version": 1, "script_signature": script_signature(script), "duration": 6,
        "sections": [
            {"id": 1, "start": 0, "duration": 3, "caption_timing": "estimated"},
            {"id": 2, "start": 3, "duration": 3, "caption_timing": "estimated"},
        ], "words": [],
    }), encoding="utf-8")
    images = output / "images"
    images.mkdir()
    for section_id in (1, 2):
        picture = images / f"section-{section_id}.png"
        Image.new("RGB", (64, 36), (30 * section_id, 80, 120)).save(picture)
        state["image_map"][section_id] = [str(picture)]

    prepared, rendered, initial_packaging = [], [], []

    def prepare(plan, audio, directory):
        prepared.append(copy.deepcopy(plan))
        index = Path(directory) / "motion" / "index.html"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps(plan), encoding="utf-8")
        return index

    def render(current_script, audio, directory, plan, **kwargs):
        assert kwargs["preview"] is True
        rendered.append({"plan": copy.deepcopy(plan), "audio": audio,
                         "script": copy.deepcopy(current_script)})
        target = Path(directory) / "motion_preview.mp4"
        target.write_bytes(json.dumps(plan, sort_keys=True).encode("utf-8"))
        return str(target)

    def initial_thumbnail(*args):
        initial_packaging.append("thumbnail")
        target = output / "thumbnail.jpg"
        target.write_bytes(b"generated thumbnail")
        return str(target)

    def initial_description(*args, **kwargs):
        initial_packaging.append("description")
        return "Initially generated description"

    monkeypatch.setattr(html, "prepare_motion_project", prepare)
    monkeypatch.setattr(html, "render_motion_video", render)
    monkeypatch.setattr(runner, "make_thumbnail", initial_thumbnail)
    monkeypatch.setattr(runner, "build_description", initial_description)
    runner.run_pipeline_thread(["visuals", "video"], visual_mode="motion", preview_only=True,
                               use_project_photos=True)
    assert state["error"] is None
    assert initial_packaging == ["thumbnail", "description"]
    if legacy_photo_plan:
        state["visual_plan"].pop("use_project_photos")
        runner._save_json(output / "scene_plan.json", state["visual_plan"])
    original_plan = copy.deepcopy(state["visual_plan"])
    assert len(original_plan["scenes"]) >= 2
    assert all(scene.get("image") for scene in original_plan["scenes"])

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    saved = {"title": "My saved publishing title", "description": saved_description,
             "tags": ["my-tag", "second-tag"]}
    assert client.post("/api/save-output", json=saved).status_code == 200
    thumbnail = output / "custom-thumbnail.jpg"
    thumbnail.write_bytes(b"custom thumbnail chosen in Review")
    state["thumb_path"] = str(thumbnail)
    retained = {path: path.read_bytes() for path in [
        output / "narration.wav", output / "narration.timings.json", output / "script.json",
        thumbnail, *images.glob("*.png"),
    ]}
    image_map = copy.deepcopy(state["image_map"])
    timeline = copy.deepcopy(state["timeline"])
    before = client.get("/api/visuals").json
    previous_draft_revision = client.get("/api/status").json["preview_revision"]
    assert previous_draft_revision

    def forbidden(*args, **kwargs):
        pytest.fail("A scene-only draft refresh must preserve speech, planning and publishing edits")

    monkeypatch.setattr(visual_director, "plan_scenes", forbidden)
    monkeypatch.setattr(narrator, "generate_narration", forbidden)
    monkeypatch.setattr(runner, "make_thumbnail", forbidden)
    monkeypatch.setattr(runner, "build_description", forbidden)
    scene_id = original_plan["scenes"][0]["id"]
    layout = "text" if original_plan["scenes"][0]["template"] != "text" else "flow"
    edited = client.post(f"/api/visuals/scenes/{scene_id}/regenerate", json={
        "instructions": f"Use a {layout} layout", "expected_plan_revision": before["plan_revision"],
    })
    assert edited.status_code == 200, edited.json
    edited_plan = edited.json["plan"]
    assert edited.json["plan_revision"] != before["plan_revision"]
    assert edited_plan["scenes"][0]["template"] == layout
    assert edited_plan["scenes"][1:] == original_plan["scenes"][1:]
    assert edited_plan["scenes"][0]["image"] == original_plan["scenes"][0]["image"]
    assert state["preview_path"] is None and state["video_path"] is None
    assert not (output / "visual_qa.json").exists()

    dispatched = []

    class InlineThread:
        def __init__(self, *, target, args, kwargs, daemon):
            self.target, self.args, self.kwargs = target, args, kwargs
            dispatched.append({"steps": args[0], **kwargs})

        def start(self):
            assert state["running"] is True
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(pipeline.threading, "Thread", InlineThread)
    response = client.post("/api/run", json={
        "steps": ["video"], "visual_mode": "motion", "preview_only": True,
        "expected_plan_revision": edited.json["plan_revision"],
    })
    assert response.status_code == 200 and response.json["ok"]
    assert state["error"] is None and not state["running"]
    assert dispatched[0]["steps"] == ["video"] and dispatched[0]["preview_only"] is True
    assert dispatched[0]["use_project_photos"] is True
    assert len(rendered) == 2
    assert rendered[-1]["plan"] == edited_plan
    assert prepared[-1] == edited_plan
    assert json.loads((output / "scene_plan.json").read_text(encoding="utf-8")) == edited_plan
    assert rendered[-1]["audio"] == rendered[0]["audio"] == str(output / "narration.wav")
    assert state["timeline"] == {**timeline, "title": saved["title"]}
    assert state["image_map"] == image_map
    assert all(path.read_bytes() == data for path, data in retained.items())
    assert state["description"] == saved_description
    assert state["script"]["title"] == saved["title"] and state["script"]["tags"] == saved["tags"]
    assert state["thumb_path"] == str(thumbnail)
    assert state["preview_path"] == str(output / "motion_preview.mp4")
    assert state["video_path"] is None and not archived
    status = client.get("/api/status").json
    assert status["plan_revision"] == edited.json["plan_revision"]
    assert status["preview_revision"] != previous_draft_revision
    assert status["has_preview"] and not status["has_video"]


@pytest.mark.parametrize("steps", [["video"], ["script", "narration", "visuals", "video"]])
def test_worker_rejects_new_topic_with_partial_steps_before_sources_change(job, monkeypatch, steps):
    from agents import scriptwriter
    from studio import preflight
    from video.renderers import html
    output, script, _ = job
    previous_script = copy.deepcopy(script)
    files = {path: path.read_bytes() for path in output.rglob("*") if path.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail("A new topic without Research must stop before provider or render work")
    monkeypatch.setattr(preflight, "check_environment", forbidden)
    monkeypatch.setattr(scriptwriter, "write_script", forbidden)
    monkeypatch.setattr(html, "render_motion_video", forbidden)

    runner.run_pipeline_thread(steps, "New ocean topic", visual_mode="motion", preview_only=True)

    assert "Enable Research" in state["error"]
    assert state["stages"]["preparation"] == "error" and not state["running"]
    assert state["script"] == previous_script
    assert all(path.read_bytes() == data for path, data in files.items())


def test_motion_rebuild_omits_saved_photos_without_losing_explicit_reuse_or_narration(job, monkeypatch):
    from PIL import Image
    from studio import preflight
    from video import narrator, stock
    from video.renderers import html
    output, script, _ = job
    picture = output / "images" / "saved-project-photo.png"
    picture.parent.mkdir()
    Image.new("RGB", (64, 36), "#266ca0").save(picture)
    state["image_map"] = {1: [str(picture)]}
    runner._save_image_map(output, state["image_map"])
    encoded = []
    def render(script, audio, directory, plan, **kwargs):
        encoded.append(copy.deepcopy(plan))
        target = Path(directory) / "motion_preview.mp4"
        target.write_bytes(json.dumps(plan, sort_keys=True).encode("utf-8"))
        return str(target)
    monkeypatch.setattr(html, "render_motion_video", render)
    runner.run_pipeline_thread(["visuals", "video"], visual_mode="motion", preview_only=True,
                               use_project_photos=True)
    assert state["error"] is None and state["visual_plan"]["scenes"][0].get("image")
    photo_plan = copy.deepcopy(state["visual_plan"])
    retained = {path: path.read_bytes() for path in (
        output / "script.json", output / "narration.wav", output / "narration.timings.json",
        output / "image_map.json", picture,
    )}
    def forbidden(*args, **kwargs):
        pytest.fail("Switching visual style must preserve existing speech and saved photos")
    monkeypatch.setattr(stock, "download_images", forbidden)
    monkeypatch.setattr(stock, "section_images", forbidden)
    monkeypatch.setattr(runner, "_load_image_map", forbidden)
    monkeypatch.setattr(narrator, "generate_narration", forbidden)
    checked = []
    monkeypatch.setattr(preflight, "check_environment", lambda steps, mode: checked.append(steps) or {
        "ok": True, "checks": []})

    # Home's graphics default cannot quietly reuse a photo storyboard, even
    # when the script, narration and dimensions all still match.
    runner.run_pipeline_thread(["video"], visual_mode="motion", preview_only=True)
    assert "different photo option" in state["error"] and len(encoded) == 1
    assert state["visual_plan"] == photo_plan

    runner.run_pipeline_thread(["images", "visuals", "video"], visual_mode="motion", preview_only=True,
                               use_project_photos=False)
    assert state["error"] is None and len(encoded) == 2
    graphics = encoded[-1]
    assert graphics["use_project_photos"] is False
    assert not any(scene.get("image") or scene.get("footage") for scene in graphics["scenes"])
    assert state["stages"]["images"] == "skipped" and all("images" not in steps for steps in checked)
    assert state["image_map"] == {1: [str(picture)]}
    assert all(path.read_bytes() == data for path, data in retained.items())

    # The separate Animate these photos action still uses those same files.
    runner.run_pipeline_thread(["visuals"], visual_mode="motion", use_project_photos=True)
    assert state["error"] is None
    assert state["visual_plan"]["use_project_photos"] is True
    assert state["visual_plan"]["scenes"][0]["image"]["path"] == str(picture)
    assert all(path.read_bytes() == data for path, data in retained.items())


@pytest.mark.parametrize("saved_photo_opt_in", [False, True])
def test_fresh_topic_does_not_inherit_previous_plan_or_images(job, monkeypatch, saved_photo_opt_in):
    from PIL import Image
    from agents import researcher, scriptwriter
    from video import narrator, stock
    from video.renderers import html
    output, old_script, _ = job
    images = output / "images"
    if saved_photo_opt_in:
        # An unmanaged linked image folder is deliberately not deleted during
        # invalidation. Old filenames in it still must not enter a fresh script.
        source = output / "saved-source-images"
        source.mkdir()
        try:
            images.symlink_to(source, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks unavailable on this platform")
    else:
        images.mkdir()
    old_picture = images / "section_01_img01.png"
    Image.new("RGB", (64, 36), "#b83020").save(old_picture)
    state["image_map"] = {1: [str(old_picture)]}
    runner._save_image_map(output, state["image_map"])
    runner.run_pipeline_thread(["visuals"], visual_mode="motion", use_project_photos=True)
    old_plan = copy.deepcopy(state["visual_plan"])
    assert old_plan["scenes"][0].get("image")
    new_script = {"title": "New ocean video", "sections": [
        {"id": 1, "title": "Ocean currents", "narration": "Water moves through the ocean. Currents carry heat."}]}
    requested_topics = []
    monkeypatch.setattr(researcher, "research_topic", lambda *args, **kwargs:
                        requested_topics.append(kwargs["topic_override"]) or {"video_title": new_script["title"]})
    monkeypatch.setattr(scriptwriter, "write_script", lambda *args, **kwargs: copy.deepcopy(new_script))
    def narrate(script, directory, **kwargs):
        path = Path(directory) / "narration.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            audio.writeframes(b"\x01\x00" * 48000 * 6)
        runner._save_json(Path(directory) / "narration.timings.json", {
            "version": 1, "script_signature": script_signature(script), "duration": 6,
            "sections": [{"id": 1, "start": 0, "duration": 6, "caption_timing": "estimated"}], "words": [],
        })
        return str(path)
    monkeypatch.setattr(narrator, "generate_narration", narrate)
    def forbidden(*args, **kwargs):
        pytest.fail("New source material must not rediscover or fetch photos in this run")
    monkeypatch.setattr(stock, "download_images", forbidden)
    monkeypatch.setattr(stock, "section_images", forbidden)
    rendered = []
    def render(script, audio, directory, plan, **kwargs):
        rendered.append(copy.deepcopy(plan))
        target = Path(directory) / "motion_preview.mp4"
        target.write_bytes(b"new ocean draft")
        return str(target)
    monkeypatch.setattr(html, "render_motion_video", render)
    steps = ["research", "script", "narration", "visuals", "video"]
    if not saved_photo_opt_in:
        steps.insert(2, "images")

    runner.run_pipeline_thread(steps, "New ocean topic", visual_mode="motion", preview_only=True,
                               use_project_photos=saved_photo_opt_in)

    assert state["error"] is None and requested_topics == ["New ocean topic"]
    assert state["script"] == new_script and len(rendered) == 1
    assert rendered[0]["script_signature"] != old_plan["script_signature"]
    assert rendered[0]["title"] == "New ocean video"
    assert not any(scene.get("image") for scene in rendered[0]["scenes"])
    assert state["image_map"] == {} and not (output / "image_map.json").exists()
    assert old_picture.exists() is saved_photo_opt_in


def _import_audio_fixture(profile_id, directory, kind, frequency):
    import base64
    import numpy as np
    from studio.audio_library import import_audio_asset
    path = directory / (kind + ".wav")
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        times = np.arange(48000) / 48000
        audio.writeframes((np.sin(2 * np.pi * frequency * times) * 12000).astype("<i2").tobytes())
    return import_audio_asset(profile_id, {
        "filename": path.name, "kind": kind, "title": f"Imported {kind}", "creator": "Offline fixture",
        "source_url": "https://pixabay.com/", "license_note": "Locally generated test recording",
        "content_id": "unknown", "data_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    })


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools required for imported audio")
def test_apply_audio_uses_project_profile_and_retains_edits_and_prepared_mix(job, monkeypatch):
    import hashlib
    from studio import audio_library, channels, create_app, pipeline
    from studio.project_identity import write_project_identity
    from video import music, narrator, sound_effects
    from video.renderers import html
    output, script, _ = job
    monkeypatch.setattr(config, "SETTINGS_PATH", str(output / "settings.json"))
    owner = channels.create_profile("News project owner", defaults={"visual_ai_enabled": False})
    selected_music = _import_audio_fixture(owner["id"], output, "music", 700)
    selected_effect = _import_audio_fixture(owner["id"], output, "sfx", 1200)
    channels.update_profile_defaults(owner["id"], {
        "music_enabled": True, "music_track": selected_music["id"], "music_volume": .08,
        "sfx_enabled": True, "sfx_track": selected_effect["id"], "sfx_mode": "ambience", "sfx_volume": .12,
    })
    other = channels.create_profile("Different active channel", "asmr")
    assert channels.active_profile_id() == other["id"]
    state.update(profile_id=owner["id"], profile_name=owner["name"])
    write_project_identity(output, owner["id"], owner["name"])
    runner.run_pipeline_thread(["visuals"], visual_mode="motion", profile_id=owner["id"])
    assert state["error"] is None
    plan = copy.deepcopy(state["visual_plan"])
    state["description"] = ""  # A deliberately empty publishing description is an edit too.
    thumbnail = output / "chosen-thumbnail.jpg"
    thumbnail.write_bytes(b"user-selected thumbnail")
    state["thumb_path"] = str(thumbnail)
    retained = {path: path.read_bytes() for path in (
        output / "script.json", output / "scene_plan.json", output / "narration.wav",
        output / "narration.timings.json", thumbnail,
    )}
    seen_profiles = []
    original_resolver = audio_library.resolve_audio_asset
    def resolve(profile_id, asset_id, kind=None):
        seen_profiles.append((profile_id, asset_id, kind))
        return original_resolver(profile_id, asset_id, kind)
    monkeypatch.setattr(audio_library, "resolve_audio_asset", resolve)
    rendered_audio = []
    def render(script, audio, directory, current_plan, **kwargs):
        assert current_plan == state["visual_plan"]
        rendered_audio.append(audio)
        target = Path(directory) / "motion_preview.mp4"
        target.write_bytes(b"draft with prepared soundtrack")
        return str(target)
    monkeypatch.setattr(html, "render_motion_video", render)
    def forbidden(*args, **kwargs):
        pytest.fail("Audio/visual rerenders must preserve voice and publishing edits")
    monkeypatch.setattr(narrator, "generate_narration", forbidden)
    monkeypatch.setattr(runner, "make_thumbnail", forbidden)
    monkeypatch.setattr(runner, "build_description", forbidden)
    class InlineThread:
        def __init__(self, *, target, args, kwargs, daemon):
            self.target, self.args, self.kwargs = target, args, kwargs
        def start(self):
            self.target(*self.args, **self.kwargs)
    monkeypatch.setattr(pipeline.threading, "Thread", InlineThread)
    app = create_app()
    app.testing = True
    client = app.test_client()
    from studio.revisions import plan_revision
    revision = plan_revision(plan)
    response = client.post("/api/run", json={
        "steps": ["music", "video"], "visual_mode": "motion", "preview_only": True,
        "profile_id": owner["id"], "expected_plan_revision": revision,
    })
    assert response.status_code == 200 and state["error"] is None
    assert state["stages"]["music"] == "done" and not state["running"]
    assert seen_profiles == [(owner["id"], selected_music["id"], "music"),
                             (owner["id"], selected_effect["id"], "sfx")]
    assert channels.active_profile_id() == other["id"]
    assert rendered_audio == [str(output / "narration_effects.wav")]
    assert state["visual_plan"] == plan and plan_revision(state["visual_plan"]) == revision
    original_hash = hashlib.sha256(retained[output / "narration.wav"]).hexdigest()
    credits_path = output / "audio_credits.json"
    credits = json.loads(credits_path.read_text(encoding="utf-8"))
    assert credits["audio_sha256"] == original_hash and credits["path"] == "narration_effects.wav"
    assert [(asset["id"], asset["mode"]) for asset in credits["assets"]] == [
        (selected_music["id"], "bed"), (selected_effect["id"], "ambience")]
    assert all(asset["creator"] == "Offline fixture" and asset["source_url"] == "https://pixabay.com/"
               for asset in credits["assets"])
    soundtrack_manifest = json.loads((output / "render_audio.json").read_text(encoding="utf-8"))
    assert soundtrack_manifest == {"path": "narration_effects.wav", "audio_sha256": original_hash}
    with wave.open(rendered_audio[0], "rb") as recording:
        assert recording.getnframes() == 6 * 48000
    retained.update({path: path.read_bytes() for path in (
        credits_path, output / "render_audio.json", output / "narration_effects.wav",
    )})

    monkeypatch.setattr(audio_library, "resolve_audio_asset", forbidden)
    monkeypatch.setattr(music, "mix_music_with_narration", forbidden)
    monkeypatch.setattr(sound_effects, "mix_sound_effects", forbidden)
    # Both exact render-only and a rebuilt graphic storyboard retain the mixed
    # soundtrack instead of rereading the active channel's audio selections.
    for steps in (["video"], ["visuals", "video"]):
        runner.run_pipeline_thread(steps, visual_mode="motion", preview_only=True,
                                   profile_id=owner["id"], use_project_photos=False)
        assert state["error"] is None
    assert rendered_audio == [str(output / "narration_effects.wav")] * 3
    assert state["description"] == "" and state["thumb_path"] == str(thumbnail)
    assert all(path.read_bytes() == data for path, data in retained.items())


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools required for imported audio")
def test_unavailable_or_other_profile_audio_preserves_voice_without_random_fallback(job, monkeypatch):
    from studio import channels
    from video import music, sound_effects
    from video.timeline import audio_signature
    output, _, _ = job
    owner = channels.create_profile("Current project")
    other = channels.create_profile("Other audio library")
    foreign_music = _import_audio_fixture(other["id"], output, "music", 700)
    foreign_effect = _import_audio_fixture(other["id"], output, "sfx", 1200)
    voice = output / "narration.wav"
    original = voice.read_bytes()
    warnings = []
    monkeypatch.setattr(runner, "log", lambda message, level="info": warnings.append((level, message)))
    def forbidden(*args, **kwargs):
        pytest.fail("Missing selected audio must never choose a random or foreign replacement")
    monkeypatch.setattr(music, "mix_music_with_narration", forbidden)
    monkeypatch.setattr(sound_effects, "mix_sound_effects", forbidden)
    for music_id, effect_id in ((foreign_music["id"], foreign_effect["id"]), ("a" * 32, "b" * 32)):
        credits = output / "audio_credits.json"
        credits.write_text('{"assets": ["stale attribution"]}', encoding="utf-8")
        with config.profile_context({"MUSIC_ENABLED": True, "MUSIC_TRACK": music_id,
                                     "SFX_ENABLED": True, "SFX_TRACK": effect_id}):
            result = runner._mix_project_audio(str(voice), {"audio_sha256": audio_signature(voice)}, output, owner["id"])
        assert result == str(voice) and not credits.exists()
        assert voice.read_bytes() == original
    assert len(warnings) == 4
    assert all(level == "warn" and "unavailable" in message for level, message in warnings)


@pytest.mark.parametrize("steps", [["script"], ["narration"]])
def test_changed_sources_discard_previous_effects_and_credits(job, monkeypatch, steps):
    from video import narrator
    output, _, _ = job
    credits = output / "audio_credits.json"
    effect_mix = output / "narration_effects.wav"
    credits.write_text('{"assets": ["previous recording"]}', encoding="utf-8")
    effect_mix.write_bytes(b"previous recording with effects")
    if steps == ["script"]:
        runner._invalidate(output, script=True, audio=True)
    else:
        monkeypatch.setattr(narrator, "generate_narration", lambda *args, **kwargs: str(output / "narration.wav"))
        runner.run_pipeline_thread(steps, visual_mode="motion")
        assert state["error"] is None
    assert not credits.exists() and not effect_mix.exists()


@pytest.mark.parametrize("music_enabled,sfx_enabled", [(True, False), (False, True)])
def test_audio_preflight_uses_ffmpeg_without_moviepy_or_renderer_dependencies(monkeypatch, music_enabled, sfx_enabled):
    from studio import preflight
    monkeypatch.setattr(config, "MUSIC_ENABLED", music_enabled)
    monkeypatch.setattr(config, "SFX_ENABLED", sfx_enabled)
    monkeypatch.setattr(preflight, "_available", lambda module: pytest.fail("FFmpeg audio mixing needs no optional Python SDK"))
    monkeypatch.setattr(preflight, "_node_version", lambda: pytest.fail("Audio mixing does not use the renderer"))
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    result = preflight.check_environment(["music"], "motion")
    assert {check["name"] for check in result["checks"] if not check["ok"]} == {"ffmpeg", "ffprobe"}
