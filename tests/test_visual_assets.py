"""Saved photos must retain their script/channel identity through reuse."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import wave

from PIL import Image
import pytest

import config
from agents.visual_director import plan_scenes
from studio import channels, create_app, library, visual_assets, workspace
from studio.project_identity import write_project_identity
from studio.state import fresh_project_state, state, stop_event
from video.timeline import build_timeline, script_signature


def _json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def project(tmp_path, monkeypatch):
    previous = deepcopy(state)
    state.clear()
    state.update(fresh_project_state())
    stop_event.clear()
    output = tmp_path / "output"
    output.mkdir()
    for name, value in {"OUTPUT_DIR": str(output), "IMAGES_DIR": str(output / "images"),
                        "DATA_DIR": str(tmp_path / "data"), "VIDEO_WIDTH": 128,
                        "VIDEO_HEIGHT": 72, "VIDEO_FPS": 24}.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(library, "LIB_DIR", str(output / "library"))
    app = create_app()
    app.testing = True
    (output / "images").mkdir()
    photos = [output / "images" / "a garden picture.PNG", output / "images" / "river.png"]
    for path, color in zip(photos, ("navy", "green")):
        Image.new("RGB", (32, 24), color).save(path)
    script = {"title": "A garden and a river", "sections": [
        {"id": 1, "title": "Garden", "narration": "Observe a quiet garden."},
        {"id": "river", "title": "River", "narration": "Follow water along the river."},
    ]}
    images = {1: [str(path) for path in photos], "river": [str(photos[1])]}
    yield {"output": output, "script": script, "images": images, "photos": photos,
           "client": app.test_client()}
    state.clear()
    state.update(previous)
    stop_event.clear()


def _save(project):
    return visual_assets.save_binding(project["output"], project["images"], project["script"], "default")


def _valid(project, **changes):
    return visual_assets.validate_binding(changes.get("output", project["output"]),
        changes.get("images", project["images"]), changes.get("script", project["script"]),
        changes.get("profile_id", "default"))


def test_binding_is_portable_and_does_not_change_the_legacy_map_format(project, tmp_path):
    output = project["output"]
    portable = {"1": ["images/a garden picture.PNG", "images/river.png"], "river": ["images/river.png"]}
    _json(output / "image_map.json", portable)
    before = (output / "image_map.json").read_bytes()
    bound = _save(project)
    assert _valid(project) is True
    assert bound["version"] == 1 and bound["profile_id"] == "default"
    assert bound["script_signature"] == script_signature(project["script"])
    assert set(bound["files"]) == {"images/a garden picture.PNG", "images/river.png"}
    assert str(output) not in json.dumps(bound)
    assert (output / "image_map.json").read_bytes() == before
    assert _valid(project, images={"river": ["images/river.png"], 1: portable["1"]}) is True
    assert _valid(project, images={key: [path.replace("/", "\\") for path in paths]
                                   for key, paths in portable.items()}) is True
    moved = tmp_path / "reopened-on-another-computer"
    shutil.copytree(output, moved)
    assert _valid(project, output=moved, images=portable) is True
    assert _valid(project, output=moved) is False  # Absolute old-workspace paths cannot migrate.


@pytest.mark.parametrize("change", ["script", "profile", "section", "order", "path", "removed"])
def test_binding_rejects_context_or_map_changes(project, change):
    _save(project)
    script, images = deepcopy(project["script"]), deepcopy(project["images"])
    profile = "default"
    if change == "script":
        script["sections"][0]["narration"] = "An unrelated new topic."
    elif change == "profile":
        profile = "a" * 32
    elif change == "section":
        images["other"] = images.pop(1)
    elif change == "order":
        images[1].reverse()
    elif change == "path":
        same_bytes = project["output"] / "images" / "copied.png"
        shutil.copy2(project["photos"][0], same_bytes)
        images[1][0] = str(same_bytes)
    else:
        images[1].pop()
    assert _valid(project, script=script, images=images, profile_id=profile) is False


def test_replacing_photo_bytes_at_same_path_size_and_timestamp_invalidates_it(project):
    _save(project)
    photo = project["photos"][0]
    before = photo.stat()
    content = bytearray(photo.read_bytes())
    content[-1] ^= 1
    photo.write_bytes(content)
    os.utime(photo, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert photo.stat().st_size == before.st_size
    assert _valid(project) is False


@pytest.mark.parametrize("payload", ["broken json", "[]", '{"version":1}', '{"version":true}'])
def test_only_a_missing_binding_is_legacy(project, payload):
    assert _valid(project) is None
    (project["output"] / "image_binding.json").write_text(payload, encoding="utf-8")
    assert _valid(project) is False


@pytest.mark.parametrize("path", ["../outside.png", "images/../../outside.png", "images\\..\\..\\outside.png",
                                  "C:\\another-project\\photo.png", "https://example.org/photo.png"])
def test_unsafe_paths_never_become_legacy_or_bound(project, path):
    image_map = {"1": [path]}
    assert _valid(project, images=image_map) is False
    with pytest.raises(ValueError):
        visual_assets.save_binding(project["output"], image_map, project["script"], "default")
    assert not (project["output"] / "image_binding.json").exists()


def test_missing_photos_and_symlinked_files_are_not_reusable(project, tmp_path):
    _save(project)
    photo = project["photos"][0]
    photo.unlink()
    assert _valid(project) is False
    outside = tmp_path / "outside.png"
    outside.write_bytes(project["photos"][1].read_bytes())
    try:
        photo.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    assert _valid(project) is False
    binding = project["output"] / "image_binding.json"
    binding.unlink()
    binding.symlink_to(tmp_path / "missing-binding.json")
    assert _valid(project, images={}) is False


def test_binding_write_failure_keeps_previous_receipt(project, monkeypatch):
    _save(project)
    path = project["output"] / "image_binding.json"
    before = path.read_bytes()
    def fail_replace(*args):
        raise OSError("Simulated disk failure")
    monkeypatch.setattr(visual_assets.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        _save(project)
    assert path.read_bytes() == before
    assert not list(project["output"].glob(".image-binding-*"))


@pytest.mark.parametrize("limit", ["_MAX_FILE_BYTES", "_MAX_TOTAL_BYTES", "_MAX_BINDING_BYTES"])
def test_verification_limits_preserve_the_previous_binding(project, monkeypatch, limit):
    _save(project)
    path = project["output"] / "image_binding.json"
    before = path.read_bytes()
    monkeypatch.setattr(visual_assets, limit, 1)
    with pytest.raises(ValueError, match="size limit"):
        _save(project)
    assert path.read_bytes() == before
    assert not list(project["output"].glob(".image-binding-*"))


def _recorded_project(project, *, photos=True):
    output, script = project["output"], project["script"]
    _json(output / "script.json", script)
    _json(output / "research.json", {"topic": "Gardens and rivers"})
    write_project_identity(output, "default", "Default channel")
    audio = output / "narration.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 16000)
    _json(output / "narration.timings.json", {"version": 1, "script_signature": script_signature(script),
        "duration": 2, "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(), "words": [],
        "sections": [{"id": 1, "start": 0, "duration": 1}, {"id": "river", "start": 1, "duration": 1}]})
    timeline = build_timeline(script, str(audio), str(output))
    plan = plan_scenes(script, timeline, output_dir=output, use_ai=False,
                       image_map=project["images"], use_project_photos=photos)
    _json(output / "image_map.json", {str(key): [Path(path).relative_to(output).as_posix() for path in paths]
                                     for key, paths in project["images"].items()})
    _save(project)
    return audio, timeline, plan


def test_recovery_validates_map_before_using_checkpoint_or_scene_images(project):
    audio, timeline, plan = _recorded_project(project)
    state.update(script=project["script"], research={"topic": "Gardens"}, profile_id="default",
                 audio_path=str(audio), render_audio_path=str(audio), visual_mode="motion",
                 timeline=timeline, visual_plan=plan, image_map=project["images"])
    assert workspace.checkpoint_workspace()
    _json(project["output"] / "image_map.json", {})
    state.clear()
    state.update(fresh_project_state())
    assert workspace.restore_workspace()
    assert state["image_map"] == {}
    assert state["visual_plan"] == plan and state["audio_path"] == str(audio)
    assert "photo map no longer matches" in state["workspace_warning"]


def test_valid_bound_photos_survive_restart_with_owner_intact(project):
    _, _, plan = _recorded_project(project)
    assert workspace.restore_workspace()
    assert state["profile_id"] == "default" and state["visual_plan"] == plan
    assert state["image_map"] == {str(key): paths for key, paths in project["images"].items()}
    assert _valid(project, images=state["image_map"]) is True


def test_legacy_photo_recovery_does_not_silently_create_provenance(project):
    _recorded_project(project)
    (project["output"] / "image_binding.json").unlink()
    assert workspace.restore_workspace()
    assert any(state["image_map"].values())
    assert not (project["output"] / "image_binding.json").exists()
    assert _valid(project, images=state["image_map"]) is None


def test_pure_motion_recovery_does_not_inspect_old_photo_maps(project, monkeypatch):
    _, _, plan = _recorded_project(project, photos=False)
    (project["output"] / "image_map.json").write_text("old corrupt cache", encoding="utf-8")
    (project["output"] / "image_binding.json").write_text("old corrupt binding", encoding="utf-8")
    def forbidden(*args, **kwargs):
        pytest.fail("Graphics-only Motion must not inspect cached photos")
    monkeypatch.setattr(visual_assets, "validate_binding", forbidden)
    monkeypatch.setattr(workspace, "_image_map", forbidden)
    assert workspace.restore_workspace()
    assert state["visual_plan"] == plan and state["image_map"] == {}
    assert "photo map" not in (state["workspace_warning"] or "")


def test_invalid_photo_cache_does_not_break_a_valid_graphics_storyboard(project):
    _, _, plan = _recorded_project(project, photos=False)
    plan["mode"] = "hybrid"
    _json(project["output"] / "scene_plan.json", plan)
    _json(project["output"] / "image_map.json", {})
    assert workspace.restore_workspace()
    assert state["visual_plan"] == plan and state["image_map"] == {}
    assert "photo map no longer matches" in state["workspace_warning"]


def _archive(project):
    audio, _, _ = _recorded_project(project)
    video = project["output"] / "final_video.mp4"
    video.write_bytes(b"completed fixture video")
    state.update(script=project["script"], research={"topic": "Gardens and rivers"}, profile_id="default",
                 audio_path=str(audio), render_audio_path=str(audio), image_map=project["images"],
                 visual_mode="classic", visual_plan=None, timeline=None, video_path=str(video),
                 preview_only=False, description="Saved description.")
    state["stages"]["video"] = "done"
    return library.archive_project()


def test_archive_roundtrip_rebinds_portable_photos_after_remapping(project):
    pid = _archive(project)
    archive = Path(library.lib_path(pid))
    archived_map = json.loads((archive / "image_map.json").read_text())
    assert library.read_meta(pid)["files"]["image_binding"] == "image_binding.json"
    assert _valid(project, output=archive, images=archived_map) is True
    # A bad previous-project binding is replaced together with its map.
    (project["output"] / "image_binding.json").write_text("stale previous project", encoding="utf-8")
    assert project["client"].post(f"/api/library/{pid}/load", json={}).json == {"ok": True}
    assert _valid(project, images=state["image_map"]) is True
    persisted = json.loads((project["output"] / "image_map.json").read_text())
    assert all("\\" not in path and not Path(path).is_absolute() for paths in persisted.values() for path in paths)
    assert persisted == archived_map


@pytest.mark.parametrize("change", ["profile", "script", "photo", "map", "missing_binding"])
def test_invalid_archive_binding_cannot_replace_current_work(project, change):
    pid = _archive(project)
    archive = Path(library.lib_path(pid))
    if change == "profile":
        other = channels.create_profile("Another channel", "news")
        meta = library.read_meta(pid)
        meta["profile_id"] = other["id"]
        library.write_meta(pid, meta)
    elif change == "script":
        script = deepcopy(project["script"])
        script["sections"][0]["narration"] = "A different project's narration."
        _json(archive / "script.json", script)
    elif change == "photo":
        saved = json.loads((archive / "image_map.json").read_text())
        (archive / saved["1"][0]).write_bytes(b"another image")
    elif change == "map":
        _json(archive / "image_map.json", {})
    else:
        (archive / "image_binding.json").unlink()
    previous = deepcopy(state)
    script_before = (project["output"] / "script.json").read_bytes()
    binding_before = (project["output"] / "image_binding.json").read_bytes()
    result = project["client"].post(f"/api/library/{pid}/load", json={}).json
    assert not result["ok"] and "photos" in result["error"]
    assert state == previous
    assert (project["output"] / "script.json").read_bytes() == script_before
    assert (project["output"] / "image_binding.json").read_bytes() == binding_before


def test_explicit_legacy_archive_load_creates_current_binding(project):
    pid = _archive(project)
    archive = Path(library.lib_path(pid))
    (archive / "image_binding.json").unlink()
    meta = library.read_meta(pid)
    meta["files"].pop("image_binding")
    library.write_meta(pid, meta)
    assert project["client"].post(f"/api/library/{pid}/load", json={}).json == {"ok": True}
    assert _valid(project, images=state["image_map"]) is True
    assert not (archive / "image_binding.json").exists()  # The old archive remains unchanged.


@pytest.mark.parametrize("mode", ["motion", "hybrid"])
def test_photo_enabled_archive_without_map_does_not_rediscover_thumbnail_photos(project, mode):
    pid = _archive(project)
    archive = Path(library.lib_path(pid))
    (archive / "image_map.json").unlink()
    (archive / "image_binding.json").unlink()
    assert list((archive / "photos").glob("*.jpg"))
    meta = library.read_meta(pid)
    meta["visual_mode"] = mode
    meta["files"].pop("image_binding")
    meta["files"].pop("image_map")
    library.write_meta(pid, meta)
    assert project["client"].post(f"/api/library/{pid}/load", json={}).json == {"ok": True}
    assert state["image_map"] == {}
    assert _valid(project, images={}) is True


def test_graphics_only_archive_ignores_unused_previous_photo_map(project, monkeypatch):
    audio, timeline, plan = _recorded_project(project, photos=False)
    video = project["output"] / "final_video.mp4"
    video.write_bytes(b"completed graphics render")
    state.update(script=project["script"], research={"topic": "Gardens"}, profile_id="default",
                 audio_path=str(audio), render_audio_path=str(audio), visual_mode="motion",
                 timeline=timeline, visual_plan=plan, image_map=project["images"],
                 video_path=str(video), preview_only=False)
    state["stages"]["video"] = "done"
    def forbidden(*args, **kwargs):
        pytest.fail("Graphics-only archives must not inspect unused photos")
    monkeypatch.setattr(visual_assets, "validate_binding", forbidden)
    pid = library.archive_project()
    archive = Path(library.lib_path(pid))
    assert json.loads((archive / "image_map.json").read_text()) == {}
    assert json.loads((archive / "image_binding.json").read_text())["files"] == {}
    assert not list((archive / "photos").iterdir())


@pytest.mark.parametrize("broken_name", ["image_map.json", "image_binding.json"])
def test_classic_broken_manifests_cannot_enable_loose_filename_discovery(project, monkeypatch, broken_name):
    from studio import preflight, runner
    from video import stock
    _recorded_project(project)
    output = project["output"]
    for name in ("image_map.json", "image_binding.json"):
        (output / name).unlink()
    try:
        (output / broken_name).symlink_to(output / "missing-old-receipt.json")
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    state.update(script=project["script"], profile_id="default", image_map={}, description="Saved description.")
    monkeypatch.setattr(preflight, "check_environment", lambda *args: {"ok": True, "checks": []})
    def forbidden(*args, **kwargs):
        pytest.fail("An invalid manifest must not enable Classic legacy filename discovery")
    monkeypatch.setattr(stock, "section_images", forbidden)
    runner.run_pipeline_thread(["visuals"], visual_mode="classic")
    assert state["error"] is None
    assert not any(state["image_map"].values())
    assert (output / broken_name).is_symlink()


def test_photo_scene_edit_rejects_a_broken_binding_before_mutation(project, monkeypatch):
    from agents import visual_director
    audio, timeline, plan = _recorded_project(project)
    state.update(script=project["script"], profile_id="default", image_map=project["images"],
                 visual_plan=plan, timeline=timeline, audio_path=str(audio), render_audio_path=str(audio))
    binding = project["output"] / "image_binding.json"
    binding.unlink()
    try:
        binding.symlink_to(project["output"] / "missing-old-receipt.json")
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    before = deepcopy(state)
    def forbidden(*args, **kwargs):
        pytest.fail("A broken photo binding must be rejected before scene generation")
    monkeypatch.setattr(visual_director, "regenerate_scene", forbidden)
    scene_id = plan["scenes"][0]["id"]
    response = project["client"].post(f"/api/visuals/scenes/{scene_id}/regenerate",
                                       json={"instructions": "Use a comparison layout"})
    assert response.status_code == 409 and "photos no longer match" in response.json["error"]
    assert state == before


@pytest.mark.parametrize("receipt", ["absent", "declared_map", "declared_binding", "invalid_map", "invalid_binding"])
def test_legacy_archive_recovers_exact_scene_photos_only_without_any_map_receipt(project, receipt):
    audio, timeline, plan = _recorded_project(project)
    expected = {scene["section_id"]: Path(scene["image"]["path"]).read_bytes()
                for scene in plan["scenes"] if scene.get("image")}
    video = project["output"] / "final_video.mp4"
    video.write_bytes(b"completed legacy photo render")
    state.update(script=project["script"], research={"topic": "Gardens and rivers"}, profile_id="default",
                 audio_path=str(audio), render_audio_path=str(audio), visual_mode="motion",
                 timeline=timeline, visual_plan=plan, image_map=project["images"],
                 video_path=str(video), preview_only=False, description="A saved photo explanation.")
    state["stages"]["video"] = "done"
    pid = library.archive_project()
    archive = Path(library.lib_path(pid))
    for name in ("image_map.json", "image_binding.json"):
        (archive / name).unlink()
    meta = library.read_meta(pid)
    meta["files"].pop("image_map")
    meta["files"].pop("image_binding")
    if receipt.startswith("declared_"):
        field = "image_map" if receipt == "declared_map" else "image_binding"
        meta["files"][field] = field + ".json"
    elif receipt.startswith("invalid_"):
        name = "image_map.json" if receipt == "invalid_map" else "image_binding.json"
        (archive / name).write_text("broken old metadata", encoding="utf-8")
    library.write_meta(pid, meta)
    # Thumbnail photos are not evidence of scene association, even inside the
    # selected archive. Only its existing scene paths may rebuild this map.
    for thumbnail in (archive / "photos").glob("*.jpg"):
        Image.new("RGB", (32, 24), "red").save(thumbnail)
    before = deepcopy(state)
    binding_before = (project["output"] / "image_binding.json").read_bytes()
    response = project["client"].post(f"/api/library/{pid}/load", json={}).json
    if receipt != "absent":
        assert not response["ok"] and "photo" in response["error"]
        assert state == before
        assert (project["output"] / "image_binding.json").read_bytes() == binding_before
        return
    assert response == {"ok": True}
    assert set(state["image_map"]) == set(expected)
    for section, paths in state["image_map"].items():
        assert len(paths) == 1 and Path(paths[0]).read_bytes() == expected[section]
        assert Path(paths[0]).is_relative_to(project["output"] / "assets")
    assert _valid(project, images=state["image_map"]) is True
    assert "exact saved scene pictures" in state["image_reuse_note"]
    assert not (archive / "image_map.json").exists()
    assert not (archive / "image_binding.json").exists()
