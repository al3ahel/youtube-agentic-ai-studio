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
