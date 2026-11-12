"""Channel archives retain draft state, portable photos and reusable soundtracks."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import wave

from PIL import Image
import pytest

import config
from studio import channels, create_app, library
from studio.state import fresh_project_state, state, stop_event
from video.timeline import script_signature


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    previous = deepcopy(state)
    state.clear()
    state.update(fresh_project_state())
    stop_event.clear()
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(config, "IMAGES_DIR", str(output / "images"))
    monkeypatch.setattr(library, "LIB_DIR", str(output / "library"))
    app = create_app()
    app.testing = True
    yield output, app.test_client()
    state.clear()
    state.update(previous)
    stop_event.clear()


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _photo(path, color):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 16), color).save(path)
    return str(path)


def _wave(path):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x01\x00" * 8000)


def _completed_project(output, profile, *, draft=False):
    script = {"title": "How a river changes", "tags": ["rivers"], "sections": [
        {"id": 1, "title": "Flow", "narration": "Water moves along the river."},
        {"id": 2, "title": "Banks", "narration": "Its banks change with time."},
    ]}
    first = _photo(output / "images" / "original-river.jpg", "#356b8a")
    second = _photo(output / "images" / "original-bank.png", "#587b39")
    _wave(output / "narration.wav")
    rendered = output / ("motion_preview.mp4" if draft else "final_video.mp4")
    rendered.write_bytes(b"fixture-render")
    timeline = {"duration": 1.0, "script_signature": script_signature(script), "sections": []}
    scene_plan = {"scenes": [{"id": "scene-001", "section_id": "1", "template": "photo_story",
                              "image": {"path": first, "source_url": "https://example.org/river"}}]}
    credits = [
        {"file": "original-river.jpg", "source": "Openverse", "author": "River photographer",
         "license": "CC BY 4.0", "link": "https://example.org/river"},
        {"file": "original-bank.png", "source": "Openverse", "author": "Bank photographer",
         "license": "CC BY-SA 4.0", "link": "https://example.org/bank"},
    ]
    _json(output / "credits.json", credits)
    state.update(profile_id=profile["id"], profile_name=profile["name"],
                 script=script, research={"topic": "Rivers"}, description="A creator-written explanation.",
                 visual_mode="hybrid", visual_plan=scene_plan, timeline=timeline,
                 image_map={1: [first, second], 2: [first]}, audio_path=str(output / "narration.wav"),
                 render_audio_path=str(output / "narration.wav"), preview_only=draft,
                 preview_path=str(rendered) if draft else None, video_path=None if draft else str(rendered))
    state["stages"]["video"] = "done"
    return script, credits, {first: Path(first).read_bytes(), second: Path(second).read_bytes()}


def test_profile_draft_archive_load_remains_a_draft_and_activates_its_owner(workspace):
    output, client = workspace
    news = channels.create_profile("Daily news", "news", {"credit_yaas": False})
    calm = channels.create_profile("Quiet evenings", "asmr")
    _completed_project(output, news, draft=True)
    state["schedule_id"] = "morning-schedule"
    assert channels.active_profile_id() == calm["id"]
    pid = library.archive_project(allow_draft=True)
    meta = library.read_meta(pid)
    assert meta["profile_id"] == news["id"] and meta["profile_name"] == "Daily news"
    assert meta["schedule_id"] == "morning-schedule" and meta["preview_only"] is True

    state.clear()
    state.update(fresh_project_state())
    response = client.post(f"/api/library/{pid}/load", json={})
    assert response.json == {"ok": True}
    assert channels.active_profile_id() == news["id"]
    assert state["profile_id"] == news["id"] and state["schedule_id"] == "morning-schedule"
    assert state["preview_only"] is True and state["video_path"] is None
    assert Path(state["preview_path"]).read_bytes() == b"fixture-render"
    assert client.get("/preview-video").status_code == 200
    assert client.post("/api/upload", json={}).status_code == 409
    assert client.get("/api/settings").json["voice_rate"] == "+0%"
    assert state["description"] == "A creator-written explanation."
    identity = json.loads((output / "project_profile.json").read_text(encoding="utf-8"))
    assert identity["profile_id"] == news["id"]


def test_portable_photo_paths_and_individual_credits_survive_archive_roundtrip(workspace):
    output, client = workspace
    news = channels.create_profile("River news", "news")
    _, credits, originals = _completed_project(output, news)
    pid = library.archive_project()
    archive = Path(library.lib_path(pid))
    image_map = json.loads((archive / "image_map.json").read_text(encoding="utf-8"))
    assert {key: len(paths) for key, paths in image_map.items()} == {"1": 2, "2": 1}
    for paths in image_map.values():
        for relative in paths:
            assert not Path(relative).is_absolute() and Path(relative).parts[0] == "images"
            assert ".." not in Path(relative).parts and (archive / relative).is_file()
    photo_credit_map = {item["file"]: item for item in json.loads((archive / "credits.json").read_text(encoding="utf-8"))}
    for relative in (image_map["1"][0], image_map["2"][0]):
        assert {key: value for key, value in photo_credit_map[Path(relative).name].items() if key != "file"} == {
            key: value for key, value in credits[0].items() if key != "file"}
    assert photo_credit_map[Path(image_map["1"][1]).name]["license"] == "CC BY-SA 4.0"
    plan = json.loads((archive / "scene_plan.json").read_text(encoding="utf-8"))
    scene_image = plan["scenes"][0]["image"]["path"]
    assert not Path(scene_image).is_absolute() and (archive / scene_image).is_file()

    shutil.rmtree(output / "images")
    _photo(output / "images" / "unrelated-current-project.jpg", "red")
    _json(output / "credits.json", [{"file": "unrelated-current-project.jpg", "source": "Wrong project"}])
    response = client.post(f"/api/library/{pid}/load", json={})
    assert response.json == {"ok": True}
    restored = state["image_map"]
    assert Path(restored["1"][0]).read_bytes() == originals[next(iter(originals))]
    assert Path(restored["2"][0]).read_bytes() == Path(restored["1"][0]).read_bytes()
    assert all(Path(path).is_relative_to(output / "images") for paths in restored.values() for path in paths)
    assert not (output / "images" / "unrelated-current-project.jpg").exists()
    assert json.loads((output / "credits.json").read_text(encoding="utf-8")) == list(photo_credit_map.values())
    assert Path(state["visual_plan"]["scenes"][0]["image"]["path"]).read_bytes() == Path(restored["1"][0]).read_bytes()
    # The persisted map keeps the same section association when process state
    # is lost and a partial pipeline run reloads the cached project.
    persisted = json.loads((output / "image_map.json").read_text(encoding="utf-8"))
    assert set(persisted) == {"1", "2"}
    assert [len(persisted[key]) for key in ("1", "2")] == [2, 1]


def _collage_project(output, profile):
    script, credits, originals = _completed_project(output, profile)
    scene = state["visual_plan"]["scenes"][0]
    scene["shot"] = {"version": 1, "role": "detail", "framing": "wide", "technique": "collage",
                     "camera": "locked", "transition": "cut", "focus": "River banks", "action": "Compare river pictures"}
    scene["media_layers"] = [{"path": path, "source": "Openverse", "source_url": credit["link"],
                              "credit": credit["author"], "license": credit["license"]}
                             for path, credit in zip(originals, credits)]
    return script, deepcopy(scene["media_layers"]), originals


def test_all_collage_layers_and_their_original_binding_survive_archive_load(workspace):
    from studio.visual_assets import validate_binding
    output, client = workspace
    profile = channels.create_profile("River pictures", "news")
    script, original_layers, originals = _collage_project(output, profile)
    pid = library.archive_project()
    archive = Path(library.lib_path(pid))
    saved = json.loads((archive / "scene_plan.json").read_text(encoding="utf-8"))["scenes"][0]
    for before, layer in zip(original_layers, saved["media_layers"]):
        assert not Path(layer["path"]).is_absolute()
        assert (archive / layer["path"]).read_bytes() == originals[before["path"]]
        assert {key: value for key, value in layer.items() if key != "path"} == {key: value for key, value in before.items() if key != "path"}
    assert saved["image"]["path"] == saved["media_layers"][0]["path"]
    shutil.rmtree(output / "images")
    response = client.post(f"/api/library/{pid}/load", json={})
    assert response.json == {"ok": True}
    for before, layer in zip(original_layers, state["visual_plan"]["scenes"][0]["media_layers"]):
        assert Path(layer["path"]).is_relative_to(output)
        assert Path(layer["path"]).read_bytes() == originals[before["path"]]
        assert layer["license"] == before["license"] and layer["credit"] == before["credit"]
    assert [len(state["image_map"][key]) for key in ("1", "2")] == [2, 1]
    assert validate_binding(output, state["image_map"], script, profile["id"]) is True
    from studio.description import load_credits
    restored_credits = load_credits()
    for layer in original_layers:
        assert any(credit.get("author") == layer["credit"] and credit.get("license") == layer["license"]
                   and credit.get("link") == layer["source_url"] for credit in restored_credits)


def test_legacy_collage_without_a_photo_map_recovers_every_exact_layer(workspace):
    from studio.visual_assets import validate_binding
    output, client = workspace
    profile = channels.create_profile("Older collage", "news")
    script, original_layers, originals = _collage_project(output, profile)
    pid = library.archive_project()
    archive = Path(library.lib_path(pid))
    for name in ("image_map.json", "image_binding.json"):
        (archive / name).unlink()
    meta = library.read_meta(pid)
    meta["files"].pop("image_map")
    meta["files"].pop("image_binding")
    library.write_meta(pid, meta)
    response = client.post(f"/api/library/{pid}/load", json={})
    assert response.json == {"ok": True}
    assert set(state["image_map"]) == {"1"} and len(state["image_map"]["1"]) == 2
    assert [Path(value).read_bytes() for value in state["image_map"]["1"]] == list(originals.values())
    assert validate_binding(output, state["image_map"], script, profile["id"]) is True


def test_escaping_collage_layer_cannot_replace_the_current_project(workspace):
    output, client = workspace
    profile = channels.create_profile("Protected collage", "news")
    _collage_project(output, profile)
    pid = library.archive_project()
    archive = Path(library.lib_path(pid))
    stored = json.loads((archive / "scene_plan.json").read_text(encoding="utf-8"))
    stored["scenes"][0]["media_layers"][1]["path"] = "../../outside.png"
    _json(archive / "scene_plan.json", stored)
    current_state = deepcopy(state)
    _json(output / "script.json", {"title": "Current unsaved work"})
    response = client.post(f"/api/library/{pid}/load", json={})
    assert response.json["ok"] is False
    assert state == current_state
    assert json.loads((output / "script.json").read_text(encoding="utf-8"))["title"] == "Current unsaved work"


def test_legacy_mp4_recovers_soundtrack_and_photos_without_narration_file(workspace):
    output, client = workspace
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is needed to verify real legacy soundtrack extraction.")
    channels.create_profile("Another channel", "news")
    archive = Path(library.lib_path("legacy-video"))
    archive.mkdir(parents=True)
    script = {"title": "A legacy river video", "sections": [
        {"id": 10, "narration": "Water moves."}, {"id": 20, "narration": "Banks change."},
        {"id": 30, "narration": "The river continues."},
    ]}
    _json(archive / "meta.json", {"id": "legacy-video", "title": script["title"],
                                  "description": "Original description.", "files": {"video": "video.mp4"}})
    _json(archive / "script.json", script)
    _json(archive / "research.json", {"topic": "Rivers"})
    first = _photo(archive / "photos" / "01.jpg", "blue")
    second = _photo(archive / "photos" / "02.jpg", "green")
    captions = "1\n00:00:00,000 --> 00:00:00,300\nWater moves.\n"
    (archive / "captions.srt").write_text(captions, encoding="utf-8")
    subprocess.run([ffmpeg, "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=160x90:r=10:d=0.4", "-f", "lavfi", "-i",
                    "sine=frequency=220:sample_rate=48000:duration=0.4", "-c:v", "mpeg4", "-q:v", "5",
                    "-c:a", "aac", "-shortest", str(archive / "video.mp4")],
                   check=True, capture_output=True, timeout=30)
    video_before = hashlib.sha256((archive / "video.mp4").read_bytes()).hexdigest()
    assert not list(archive.glob("narration.*"))

    response = client.post("/api/library/legacy-video/load", json={})
    assert response.json == {"ok": True}
    assert channels.active_profile_id() == "default" and state["profile_id"] == "default"
    assert state["render_audio_path"] == state["audio_path"] == str(output / "narration.wav")
    with wave.open(state["audio_path"], "rb") as recovered:
        assert recovered.getnchannels() == 2 and recovered.getframerate() == 48000
        assert .35 < recovered.getnframes() / recovered.getframerate() < .6
        assert any(recovered.readframes(recovered.getnframes()))
    assert state["stages"]["narration"] == "done"
    assert not (output / "narration.timings.json").exists()
    assert (output / "narration.srt").read_text(encoding="utf-8") == captions
    assert set(state["image_map"]) == {"10", "20", "30"}
    assert Path(state["image_map"]["10"][0]).read_bytes() == Path(first).read_bytes()
    assert Path(state["image_map"]["20"][0]).read_bytes() == Path(second).read_bytes()
    assert Path(state["image_map"]["30"][0]).read_bytes() == Path(first).read_bytes()
    assert "no section map" in state["image_reuse_note"] and "check" in state["image_reuse_note"]
    assert hashlib.sha256((archive / "video.mp4").read_bytes()).hexdigest() == video_before


def test_library_filters_counts_and_legacy_entries_by_channel(workspace):
    output, client = workspace
    news = channels.create_profile("News", "news")
    calm = channels.create_profile("Calm", "asmr")
    today = datetime.now().isoformat(timespec="seconds")
    for pid, owner, duration, draft in (("news-final", news["id"], 60, False),
                                        ("news-draft", news["id"], 30, True),
                                        ("calm-final", calm["id"], 120, False),
                                        ("legacy-default", None, 180, False)):
        meta = {"id": pid, "title": pid, "duration": duration, "created_at": today,
                "video_type": "normal", "preview_only": draft}
        if owner:
            meta["profile_id"] = owner
        _json(Path(library.lib_path(pid)) / "meta.json", meta)
    news_view = client.get(f"/api/library?profile_id={news['id']}").json
    assert {item["id"] for item in news_view["items"]} == {"news-final", "news-draft"}
    assert news_view["totals"]["videos"] == 2 and news_view["totals"]["minutes"] == 1.5
    assert sum(day["count"] for day in news_view["activity"]) == 2
    calm_view = client.get(f"/api/library?profile_id={calm['id']}").json
    assert [item["id"] for item in calm_view["items"]] == ["calm-final"]
    default_view = client.get("/api/library?profile_id=default").json
    assert [item["id"] for item in default_view["items"]] == ["legacy-default"]
    assert client.get("/api/library").json["totals"]["videos"] == 4
    assert client.get("/api/library?profile_id=" + "f" * 32).status_code == 400
