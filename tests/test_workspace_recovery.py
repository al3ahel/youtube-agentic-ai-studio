"""A restart recovers the saved draft, never an obsolete MP4 left on disk."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import wave

import pytest
from PIL import Image

import config
from agents.visual_director import plan_scenes
from studio import create_app, runner, workspace
from studio.state import fresh_project_state, state
from video.timeline import build_timeline, script_signature


@pytest.fixture(scope="module")
def encoded_video(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("The real restart recovery proof needs ffmpeg and ffprobe")
    path = tmp_path_factory.mktemp("recovery-video") / "video.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=navy:size=128x72:rate=24",
                    "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
                   check=True, capture_output=True, timeout=30)
    return path.read_bytes()


@pytest.fixture
def project(tmp_path, monkeypatch, encoded_video):
    previous = deepcopy(state)
    state.clear()
    state.update(fresh_project_state())
    output = tmp_path / "output"
    output.mkdir()
    for key, value in {"OUTPUT_DIR": str(output), "IMAGES_DIR": str(output / "images"),
                       "DATA_DIR": str(tmp_path / "data"), "VIDEO_WIDTH": 128,
                       "VIDEO_HEIGHT": 72, "VIDEO_FPS": 24}.items():
        monkeypatch.setattr(config, key, value)
    script = {"title": "A recoverable draft", "description": "Saved source description", "tags": ["energy"],
              "sections": [{"id": 1, "title": "Observe", "narration": "Observe sunlight. Follow the energy."},
                           {"id": "next", "title": "Explain", "narration": "Explain the panel. Follow the electrons."}]}
    research = {"topic": "Energy", "hook_question": "Where does the energy go?"}
    (output / "script.json").write_text(json.dumps(script), encoding="utf-8")
    (output / "research.json").write_text(json.dumps(research), encoding="utf-8")
    audio = output / "narration.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 16000)
    (output / "narration.timings.json").write_text(json.dumps({
        "version": 1, "script_signature": script_signature(script), "duration": 2,
        "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "sections": [{"id": 1, "start": 0, "duration": 1}, {"id": "next", "start": 1, "duration": 1}],
        "words": [],
    }), encoding="utf-8")
    timeline = build_timeline(script, str(audio), str(output))
    (output / "images").mkdir()
    photo = output / "images" / "saved.png"
    Image.new("RGB", (64, 64), "navy").save(photo)
    images = {1: [str(photo)], "next": [str(photo)]}
    (output / "image_map.json").write_text(json.dumps({str(key): ["images/saved.png"] for key in images}), encoding="utf-8")
    plan = plan_scenes(script, timeline, output_dir=output, image_map=images, use_ai=False)
    (output / "render_audio.json").write_text(json.dumps({
        "path": "narration.wav", "audio_sha256": timeline["audio_sha256"],
    }), encoding="utf-8")
    motion = output / "motion"
    motion.mkdir()
    (motion / "index.html").write_text("<!doctype html><main>A saved local preview</main>", encoding="utf-8")
    (motion / "project.json").write_text(json.dumps(plan), encoding="utf-8")
    draft = output / "motion_preview.mp4"
    draft.write_bytes(encoded_video)
    yield {"output": output, "script": script, "research": research, "timeline": timeline,
           "plan": plan, "audio": audio, "draft": draft, "images": images, "photo": photo}
    state.clear()
    state.update(previous)


def _empty_editor():
    state.clear()
    state.update(fresh_project_state())


def _open_project(project):
    state.update(script=deepcopy(project["script"]), research=deepcopy(project["research"]),
                 timeline=deepcopy(project["timeline"]), visual_plan=deepcopy(project["plan"]),
                 audio_path=str(project["audio"]), render_audio_path=str(project["audio"]),
                 preview_path=str(project["draft"]), image_map=deepcopy(project["images"]),
                 visual_mode="motion", video_type="normal", profile_id="default", preview_only=True)


def _collage(project):
    output = project["output"]
    second = output / "images" / "second-layer.png"
    Image.new("RGB", (64, 64), "green").save(second)
    images = {"1": [str(project["photo"]), str(second)], "next": [str(project["photo"]), str(second)]}
    project["images"] = images
    project["plan"] = plan_scenes(project["script"], project["timeline"], output_dir=output,
                                  image_map=images, use_ai=False, use_project_photos=True)
    (output / "image_map.json").write_text(json.dumps({key: [str(Path(path).relative_to(output)) for path in values]
                                                    for key, values in images.items()}), encoding="utf-8")
    from studio.visual_assets import save_binding
    save_binding(output, images, project["script"], "default")
    return second


def test_every_collage_layer_is_part_of_the_completed_render_identity(project):
    second = _collage(project)
    _open_project(project)
    assert workspace.checkpoint_workspace(rendered_kind="draft")
    checkpoint = json.loads((project["output"] / "workspace.json").read_text(encoding="utf-8"))
    assert "images/second-layer.png" in checkpoint["renders"]["draft"]["binding"]["assets"]
    _empty_editor()
    assert workspace.restore_workspace() and state["preview_path"] == str(project["draft"])
    Image.new("RGB", (64, 64), "orange").save(second)
    _empty_editor()
    assert workspace.restore_workspace()
    assert state["preview_path"] is None and state["video_path"] is None


def test_legacy_workspace_recovery_includes_all_collage_layers(project):
    second = _collage(project)
    (project["output"] / "image_map.json").unlink()
    (project["output"] / "image_binding.json").unlink()
    _empty_editor()
    assert workspace.restore_workspace()
    assert state["image_map"]["1"] == [str(project["photo"]), str(second)]
    assert state["image_map"]["next"] == [str(project["photo"]), str(second)]


def test_create_app_recovers_precheckpoint_draft_and_serves_it_without_generation(project, monkeypatch):
    from agents import visual_director
    from video import narrator

    def forbidden(*args, **kwargs):
        pytest.fail("Restart recovery must not call a provider or regenerate narration")

    monkeypatch.setattr(visual_director, "plan_scenes", forbidden)
    monkeypatch.setattr(narrator, "generate_narration", forbidden)
    before = {name: (project["output"] / name).read_bytes() for name in (
        "script.json", "scene_plan.json", "timeline.json", "narration.wav", "motion_preview.mp4")}
    client = create_app().test_client()
    status = client.get("/api/status").json
    assert status["workspace_restored"] and status["has_preview"] and status["review_ready"]
    assert status["has_storyboard"] and status["has_audio"] and status["has_html_preview"]
    assert status["workspace_warning"] is None and not status["has_video"]
    assert client.get("/api/output").json["title"] == project["script"]["title"]
    response = client.get("/preview-video")
    assert response.status_code == 200 and response.data == project["draft"].read_bytes()
    response.close()
    assert state["stages"]["video"] == "done"
    assert all((project["output"] / name).read_bytes() == value for name, value in before.items())
    assert (project["output"] / "workspace.json").is_file()
    _empty_editor()
    assert workspace.restore_workspace()
    assert state["preview_path"] == str(project["draft"])


def test_recovery_preserves_saved_channel_instead_of_adopting_active_profile(project):
    from studio.channels import active_profile_id, create_profile
    from studio.project_identity import write_project_identity
    owner = create_profile("News", preset="news")
    other = create_profile("Quiet", preset="asmr")
    write_project_identity(project["output"], owner["id"], owner["name"])
    assert active_profile_id() == other["id"]
    assert workspace.restore_workspace()
    assert state["profile_id"] == owner["id"] and state["profile_name"] == "News"
    assert active_profile_id() == other["id"]


@pytest.mark.parametrize("live", [{"running": True}, {"visual_editing": True}, {"script": {"title": "Already open"}}])
def test_recovery_does_not_replace_live_work(project, live):
    state.update(live)
    before = deepcopy(state)
    assert not workspace.restore_workspace()
    assert state == before and not (project["output"] / "workspace.json").exists()


def test_storyboard_recovers_when_no_render_exists_and_ignores_unowned_old_final(project):
    old_final = project["output"] / "final_video.mp4"
    project["draft"].rename(old_final)
    assert workspace.restore_workspace()
    assert state["visual_plan"] == project["plan"] and state["audio_path"] == str(project["audio"])
    assert not state["preview_path"] and not state["video_path"]
    assert "Render draft MP4" in state["workspace_warning"]
    assert state["stages"]["video"] == "pending" and old_final.is_file()


def test_precheckpoint_draft_older_than_scene_edit_is_not_revived(project):
    plan_file = project["output"] / "scene_plan.json"
    newer = project["draft"].stat().st_mtime_ns + 1_000_000_000
    os.utime(plan_file, ns=(newer, newer))
    assert workspace.restore_workspace()
    assert state["visual_plan"] and not state["preview_path"]
    assert "Render draft MP4" in state["workspace_warning"]
    assert project["draft"].is_file()


def test_recovery_accepts_valid_frame_timing_with_rounded_scene_duration(project):
    with wave.open(str(project["audio"]), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 16333)
    duration = 16333 / 8000
    timing = {"version": 1, "script_signature": script_signature(project["script"]), "duration": duration,
              "audio_sha256": hashlib.sha256(project["audio"].read_bytes()).hexdigest(),
              "sections": [{"id": 1, "start": 0, "duration": 1},
                           {"id": "next", "start": 1, "duration": duration - 1}], "words": []}
    (project["output"] / "narration.timings.json").write_text(json.dumps(timing), encoding="utf-8")
    timeline = build_timeline(project["script"], str(project["audio"]), str(project["output"]))
    plan = plan_scenes(project["script"], timeline, output_dir=project["output"], use_ai=False)
    assert plan["duration"] != timeline["duration"]
    assert abs(plan["duration"] - timeline["duration"]) < .001
    assert workspace.restore_workspace()
    assert state["visual_plan"] == plan and state["audio_path"] == str(project["audio"])


@pytest.mark.parametrize("payload", [b"", b"not an encoded video"])
def test_unusable_precheckpoint_mp4_never_becomes_ready(project, payload):
    project["draft"].write_bytes(payload)
    assert workspace.restore_workspace()
    assert state["preview_path"] is None and state["visual_plan"]
    assert "Render draft MP4" in state["workspace_warning"]


def test_checkpoint_preserves_metadata_and_contains_only_relative_media_paths(project):
    _open_project(project)
    state.update(description="", thumb_text="Custom title", thumb_style="minimal",
                 thumb_bg=str(project["photo"]), thumb_path=str(project["photo"]), thumb_bg_index=2)
    assert workspace.checkpoint_workspace(rendered_kind="draft")
    checkpoint = (project["output"] / "workspace.json").read_text(encoding="utf-8")
    assert str(project["output"]) not in checkpoint
    assert json.loads(checkpoint)["renders"]["draft"]["artifact"]["path"] == "motion_preview.mp4"
    _empty_editor()
    assert workspace.restore_workspace()
    assert state["preview_path"] == str(project["draft"])
    assert state["description"] == "" and state["thumb_text"] == "Custom title"
    assert state["thumb_bg_index"] == 2 and state["thumb_path"] == str(project["photo"])


def test_completed_final_requires_and_recovers_its_positive_render_receipt(project):
    _open_project(project)
    final = project["output"] / "final_video.mp4"
    project["draft"].rename(final)
    state.update(video_path=str(final), preview_path=None, preview_only=False,
                 library_id="saved-project", yt_url="https://www.youtube.com/watch?v=example")
    assert workspace.checkpoint_workspace(rendered_kind="final")
    _empty_editor()
    assert workspace.restore_workspace()
    assert state["video_path"] == str(final) and state["preview_path"] is None
    assert not state["preview_only"] and state["library_id"] == "saved-project"


@pytest.mark.parametrize("kind", ["draft", "final"])
def test_checkpoint_restores_open_library_video_and_thumbnail_inside_output(project, kind):
    _open_project(project)
    archive = project["output"] / "library" / "saved-project"
    archive.mkdir(parents=True)
    video, thumbnail = archive / "video.mp4", archive / "thumbnail.jpg"
    shutil.copy2(project["draft"], video)
    shutil.copy2(project["photo"], thumbnail)
    state.update(video_path=str(video) if kind == "final" else None,
                 preview_path=str(video) if kind == "draft" else None, preview_only=kind == "draft",
                 thumb_path=str(thumbnail), library_id="saved-project")
    assert workspace.checkpoint_workspace(rendered_kind=kind)
    checkpoint = json.loads((project["output"] / "workspace.json").read_text(encoding="utf-8"))
    assert checkpoint["renders"][kind]["artifact"]["path"] == "library/saved-project/video.mp4"
    assert checkpoint["paths"]["thumb_path"] == "library/saved-project/thumbnail.jpg"
    _empty_editor()
    assert workspace.restore_workspace()
    assert state["video_path" if kind == "final" else "preview_path"] == str(video)
    assert state["thumb_path"] == str(thumbnail)
    assert state["library_id"] == "saved-project"
    assert state["preview_only"] == (kind == "draft")


def test_legacy_candidate_must_match_probed_dimensions_even_if_timestamps_look_fresh(project):
    cutoff = project["draft"].stat().st_mtime_ns - 1_000_000
    for name in ("timeline.json", "scene_plan.json"):
        path = project["output"] / name
        value = json.loads(path.read_text(encoding="utf-8"))
        value["width"] = 256
        path.write_text(json.dumps(value), encoding="utf-8")
        os.utime(path, ns=(cutoff, cutoff))
    assert workspace.restore_workspace()
    assert state["visual_plan"]["width"] == 256
    assert state["preview_path"] is None


def test_invalidation_durably_marks_old_render_unusable_even_if_its_clock_is_newer(project):
    _open_project(project)
    assert workspace.checkpoint_workspace(rendered_kind="draft")
    runner._invalidate(project["output"])
    receipt = json.loads((project["output"] / "workspace.json").read_text(encoding="utf-8"))
    assert receipt["renders"] == {}
    newer = (project["output"] / "workspace.json").stat().st_mtime_ns + 1_000_000_000
    os.utime(project["draft"], ns=(newer, newer))
    _empty_editor()
    assert workspace.restore_workspace()
    assert state["visual_plan"] and state["preview_path"] is None
    assert project["draft"].is_file()


def test_render_receipt_rejects_changed_artifact_without_touching_file(project):
    _open_project(project)
    assert workspace.checkpoint_workspace(rendered_kind="draft")
    before = project["draft"].stat()
    changed = b"x" * before.st_size
    project["draft"].write_bytes(changed)
    os.utime(project["draft"], ns=(before.st_atime_ns, before.st_mtime_ns))
    _empty_editor()
    assert workspace.restore_workspace()
    assert state["preview_path"] is None and project["draft"].read_bytes() == changed


def test_render_receipt_rejects_changed_narration_and_invalid_storyboard(project):
    _open_project(project)
    assert workspace.checkpoint_workspace(rendered_kind="draft")
    data = bytearray(project["audio"].read_bytes())
    data[-2:] = b"\x01\x00"
    project["audio"].write_bytes(data)
    _empty_editor()
    assert workspace.restore_workspace()
    assert state["audio_path"] is None and state["visual_plan"] is None and state["preview_path"] is None
    assert "no longer matches" in state["workspace_warning"]


def test_empty_or_unreadable_plan_is_not_restored_as_a_storyboard(project):
    (project["output"] / "scene_plan.json").write_text('{"scenes":[]}', encoding="utf-8")
    assert workspace.restore_workspace()
    assert state["visual_plan"] is None and not state["preview_path"]
    assert state["visual_mode"] == "motion"
    assert "Visuals step" in state["workspace_warning"]


def test_partial_script_after_interrupted_run_never_claims_a_ready_draft(project):
    for name in ("scene_plan.json", "timeline.json", "narration.timings.json", "narration.wav",
                 "render_audio.json", "motion_preview.mp4"):
        (project["output"] / name).unlink()
    assert workspace.restore_workspace()
    assert state["script"] and not state["visual_plan"] and not state["preview_path"]
    assert state["stages"]["video"] == "pending" and state["stages"]["narration"] == "pending"
    assert "no video is ready" in state["workspace_warning"]


def test_corrupt_checkpoint_does_not_fall_back_to_legacy_file_detection(project):
    (project["output"] / "workspace.json").write_text("{broken", encoding="utf-8")
    assert workspace.restore_workspace()
    assert state["visual_plan"] and state["preview_path"] is None
    assert "video information is unreadable" in state["workspace_warning"]
    assert (project["output"] / "workspace.json").read_text(encoding="utf-8") == "{broken"


def test_recovery_rejects_escaping_render_receipt_and_does_not_read_external_media(project, tmp_path, monkeypatch):
    _open_project(project)
    assert workspace.checkpoint_workspace(rendered_kind="draft")
    receipt_file = project["output"] / "workspace.json"
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    outside = tmp_path / "private.mp4"
    outside.write_bytes(project["draft"].read_bytes())
    receipt["renders"]["draft"]["artifact"]["path"] = "../private.mp4"
    receipt_file.write_text(json.dumps(receipt), encoding="utf-8")
    fingerprint = workspace._fingerprint

    def inside(root, path):
        assert path.is_relative_to(root)
        return fingerprint(root, path)

    monkeypatch.setattr(workspace, "_fingerprint", inside)
    _empty_editor()
    assert workspace.restore_workspace()
    assert state["preview_path"] is None


def test_invalid_saved_profile_identity_is_reported_without_adopting_project(project):
    (project["output"] / "project_profile.json").write_text('{"profile_id":"unknown"}', encoding="utf-8")
    assert not workspace.restore_workspace()
    assert state["script"] is None and state["profile_id"] is None
    assert "could not be recovered" in state["workspace_warning"]


def test_checkpoints_cache_unchanged_media_fingerprints_but_detect_content_changes(project, monkeypatch):
    _open_project(project)
    original_open = Path.open
    reads = []

    def observed(path, mode="r", *args, **kwargs):
        if mode == "rb" and path.suffix in {".wav", ".mp4", ".png"}:
            reads.append(path)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observed)
    assert workspace.checkpoint_workspace(rendered_kind="draft")
    assert project["audio"] in reads and project["draft"] in reads
    reads.clear()
    state["description"] = "A metadata-only edit"
    assert workspace.checkpoint_workspace()
    assert reads == []
    before = project["audio"].stat()
    audio = bytearray(project["audio"].read_bytes())
    audio[-2:] = b"\x01\x00"
    project["audio"].write_bytes(audio)
    os.utime(project["audio"], ns=(before.st_atime_ns, before.st_mtime_ns))
    reads.clear()
    assert workspace.checkpoint_workspace()
    assert project["audio"] in reads
    assert json.loads((project["output"] / "workspace.json").read_text(encoding="utf-8"))["renders"] == {}


def test_checkpoint_accepts_original_pipeline_paths_relative_to_repository(project, monkeypatch):
    monkeypatch.chdir(project["output"].parent)
    monkeypatch.setattr(config, "OUTPUT_DIR", "output")
    _open_project(project)
    state.update(audio_path="output/narration.wav", render_audio_path="output/narration.wav",
                 preview_path="output/motion_preview.mp4")
    assert workspace.checkpoint_workspace(rendered_kind="draft")
    _empty_editor()
    assert workspace.restore_workspace()
    assert state["preview_path"] == str(project["draft"])
