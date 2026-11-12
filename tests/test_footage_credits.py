"""Publishing credits follow used clips while creator prose survives rebuilds."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
import pytest

import config
from studio import description as descriptions
from studio.footage_assets import credits_for_plan


def plan_with_clip(identifier=1):
    return {"scenes": [{"id": "one", "template": "footage", "footage": {
        "path": f"/project/clip-{identifier}.mp4", "in_point": 0,
        "source_url": f"https://pixabay.com/videos/water-filter-{identifier}/",
        "credit": f"Video by Camera Artist {identifier} on Pixabay",
    }}]}


def write_audio(output):
    asset = {"id": "a" * 32, "kind": "music", "title": "Quiet documentary",
             "creator": "Composer", "source_url": "https://commons.wikimedia.org/wiki/File:Quiet.ogg",
             "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
             "attribution": "Quiet documentary by Composer"}
    path = output / "soundtrack.wav"
    path.write_bytes(b"exact soundtrack fixture")
    manifest = {"version": 2, "path": path.name, "audio_sha256": "voice-hash",
                "soundtrack_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "credits": [asset]}
    (output / "render_audio.json").write_text(json.dumps(manifest), encoding="utf-8")
    return asset


@pytest.fixture
def output(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CREDIT_YAAS", False)
    return tmp_path


def test_managed_footage_refresh_preserves_prose_and_applied_audio(output):
    sound = write_audio(output)
    prose = "My edited opening.\n\nThe same final paragraph stays."
    original = descriptions.ensure_media_credits(prose, plan=plan_with_clip(1))
    changed = descriptions.ensure_media_credits(original, plan=plan_with_clip(2))
    assert changed.startswith(prose + "\n\n")
    assert "water-filter-1/" not in changed and "water-filter-2/" in changed
    assert sound["attribution"] in changed and sound["license_url"] in changed
    assert changed.count(descriptions.FOOTAGE_CREDIT_START) == 1
    assert changed.count(descriptions.AUDIO_CREDIT_START) == 1
    assert descriptions.ensure_media_credits(changed, plan=plan_with_clip(2)) == changed
    graphics = descriptions.ensure_media_credits(changed, plan={"scenes": []})
    assert descriptions.FOOTAGE_CREDIT_START not in graphics and "water-filter" not in graphics
    assert graphics == descriptions.ensure_audio_credits(prose)


def test_explicit_no_plan_ignores_stale_plan_file_but_omitted_plan_loads_it(output):
    (output / "scene_plan.json").write_text(json.dumps(plan_with_clip()), encoding="utf-8")
    text = descriptions.ensure_media_credits("User prose")
    assert "water-filter-1/" in text
    assert descriptions.ensure_media_credits(text, plan=None) == "User prose"


@pytest.mark.parametrize("with_license", [False, True])
def test_only_known_exact_legacy_generated_lines_migrate(output, with_license):
    old = credits_for_plan(plan_with_clip(1))
    if with_license:
        old[0].update(license="Pixabay Content License",
                      license_url="https://pixabay.com/service/license-summary/")
    photo = {"source": "NASA"}
    old.append(photo)
    generated = "\n".join(descriptions._credit_lines(old))
    prose = "My own notes mentioning https://pixabay.com/videos/water-filter-1/.\n• My own unrelated video note."
    changed = descriptions.ensure_media_credits(prose + "\n\n" + generated,
                                                 plan=plan_with_clip(2), previous_credits=old)
    assert changed.startswith(prose + "\n\n")
    assert "• Video by Camera Artist 1 on Pixabay" not in changed
    assert "Visuals: NASA\n" in changed
    assert "Space imagery courtesy of NASA." in changed
    assert "water-filter-2/" in changed
    # Without the prior record, an unknown old line is user text and stays.
    unknown = "• Video by An unknown photographer on Pixabay — https://pixabay.com/videos/unknown-99/"
    assert descriptions.ensure_media_credits(unknown, plan=None) == unknown


def test_same_used_clip_is_credited_once_even_when_copied_for_multiple_scenes(output):
    plan = plan_with_clip()
    second = deepcopy(plan["scenes"][0])
    second["id"] = "two"
    second["footage"]["path"] = "/project/different-cache-path.mp4"
    plan["scenes"].append(second)
    text = descriptions.ensure_media_credits("", plan=plan)
    assert text.count("https://pixabay.com/videos/water-filter-1/") == 1


def test_generated_description_reserves_complete_audio_and_footage_blocks(output):
    sound = write_audio(output)
    plan = {"scenes": [plan_with_clip(index)["scenes"][0] for index in range(1, 9)]}
    (output / "scene_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    text = descriptions.build_description({"description": "Generated prose. " * 700, "sections": []}, {})
    assert len(text) <= 5000 and text.endswith(descriptions.AUDIO_CREDIT_END)
    assert text.count(descriptions.FOOTAGE_CREDIT_START) == 1
    assert sound["attribution"] in text and sound["license_url"] in text
    for index in range(1, 9):
        assert f"https://pixabay.com/videos/water-filter-{index}/" in text
    assert "https://pixabay.com/service/license-summary/" in text


def test_combined_credit_overflow_never_truncates_creator_prose_or_attribution(output):
    write_audio(output)
    with pytest.raises(ValueError, match="Shorten your description"):
        descriptions.ensure_media_credits("A" * 4900, plan=plan_with_clip())
    many = {"scenes": [plan_with_clip(index)["scenes"][0] for index in range(1, 40)]}
    with pytest.raises(ValueError, match="credits alone exceed"):
        descriptions.ensure_media_credits("", plan=many)


@pytest.mark.parametrize("value", ["javascript:alert(1)", "https://wrong-license.example/", ["untrusted"]])
def test_inconsistent_license_cannot_be_published(output, value):
    plan = plan_with_clip()
    plan["scenes"][0]["footage"]["selection"] = {"license_url": value}
    with pytest.raises(ValueError, match="license metadata"):
        descriptions.ensure_media_credits("Creator's prose", plan=plan)


@pytest.fixture
def client(output, monkeypatch):
    from studio import channels, review, upload, visuals
    from studio.state import state
    previous = deepcopy(state)
    audio, video = output / "voice.wav", output / "final_video.mp4"
    audio.write_bytes(b"voice")
    video.write_bytes(b"final video")
    state.update(running=False, uploading=False, visual_editing=False, preview_only=False,
                 visual_plan=plan_with_clip(), visual_mode="hybrid", preview_path=None,
                 video_path=str(video), audio_path=str(audio), render_audio_path=str(audio),
                 profile_id="default", timeline=None, script={"title": "Video", "sections": [], "tags": []},
                 research={}, image_map={}, description="Creator's own prose.", stages={"video": "done"})
    (output / "scene_plan.json").write_text(json.dumps(state["visual_plan"]), encoding="utf-8")
    monkeypatch.setattr(review, "sync_library", lambda: None)
    monkeypatch.setattr(upload, "sync_library", lambda: None)
    monkeypatch.setattr(visuals, "checkpoint_workspace", lambda *args: None)
    monkeypatch.setattr(channels, "is_connecting", lambda: False)
    monkeypatch.setattr(channels, "get_profile", lambda identifier: {"id": identifier, "name": "Channel"})
    monkeypatch.setattr(channels, "upload_options", lambda identifier: {})
    monkeypatch.setattr(channels, "config_overrides", lambda identifier: {})
    monkeypatch.setattr(upload, "secret_ok", lambda identifier: True)
    monkeypatch.setattr(upload.threading, "Thread", lambda target, daemon: SimpleNamespace(start=target))
    sent = []
    monkeypatch.setattr(upload.yt_uploader, "upload_video", lambda path, **options:
                        sent.append(options) or {"url": "https://youtube.example/test", "thumbnail_error": None})
    app = Flask(__name__)
    app.register_blueprint(review.bp)
    app.register_blueprint(upload.bp)
    app.register_blueprint(visuals.bp)
    yield app.test_client(), sent
    state.clear()
    state.update(previous)


def test_review_save_and_upload_reinsert_current_credits_before_publishing(client):
    from studio.state import state
    browser, sent = client
    saved = browser.post("/api/save-output", json={"description": "My new opening."})
    assert saved.json == {"ok": True}
    expected = state["description"]
    assert expected.startswith("My new opening.\n\n") and "water-filter-1/" in expected
    assert browser.get("/api/output").json["description"] == expected
    # Publishing also repairs a missing block even if a stale client supplied
    # a description outside the save endpoint.
    state["description"] = "My new opening."
    assert browser.post("/api/upload", json={"privacy": "private"}).json == {"ok": True}
    assert sent[0]["description"] == expected == state["description"]


def test_overlong_save_and_upload_fail_before_metadata_or_upload_changes(client):
    from studio.state import state
    browser, sent = client
    previous = deepcopy(state)
    response = browser.post("/api/save-output", json={"title": "Changed", "description": "A" * 4999})
    assert response.status_code == 400 and "Shorten your description" in response.json["error"]
    assert state == previous
    state["description"] = "A" * 4999
    response = browser.post("/api/upload", json={"privacy": "private"})
    assert response.status_code == 400 and not sent and not state["uploading"]
    assert state["description"] == "A" * 4999


@pytest.mark.parametrize("overflow", [False, True])
def test_scene_edit_updates_description_only_with_successful_preview_commit(output, client, monkeypatch, overflow):
    from agents import visual_director
    from studio.state import state
    from video.renderers import html
    browser, _ = client
    old_credits = credits_for_plan(state["visual_plan"])
    state["description"] = ("A" * 4999 if overflow else "My prose.\n\n" + "\n".join(descriptions._credit_lines(old_credits)))
    previous = deepcopy(state)
    monkeypatch.setattr(visual_director, "regenerate_scene", lambda *args, **kwargs: plan_with_clip(2))
    prepared = []
    def prepare(plan, audio, directory):
        prepared.append(True)
        path = Path(directory) / "motion" / "index.html"
        path.parent.mkdir(parents=True)
        path.write_text("new preview", encoding="utf-8")
        return path
    monkeypatch.setattr(html, "prepare_motion_project", prepare)
    response = browser.post("/api/visuals/scenes/one/regenerate", json={"instructions": "Use another clip"})
    if overflow:
        assert response.status_code == 422 and "Shorten your description" in response.json["error"]
        assert state == previous and not prepared
        assert json.loads((output / "scene_plan.json").read_text(encoding="utf-8")) == previous["visual_plan"]
    else:
        assert response.json["ok"] and prepared
        assert state["description"].startswith("My prose.\n\n")
        assert "water-filter-1/" not in state["description"] and "water-filter-2/" in state["description"]
        assert state["video_path"] is None and state["stages"]["video"] == "pending"
