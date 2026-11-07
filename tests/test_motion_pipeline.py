"""Integration boundaries: preparation, cache identity, preview and final output."""
import copy
import json
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
                 running=False, library_id=None, visual_editing=False)
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
def test_scene_edit_then_video_only_draft_reuses_audio_plan_and_publishing_edits(
        job, monkeypatch, saved_description):
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
    runner.run_pipeline_thread(["visuals", "video"], visual_mode="motion", preview_only=True)
    assert state["error"] is None
    assert initial_packaging == ["thumbnail", "description"]
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
