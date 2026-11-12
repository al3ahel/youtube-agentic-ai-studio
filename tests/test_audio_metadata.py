"""Only audio actually applied to the current recording receives project credits."""
import json
import hashlib
from copy import deepcopy
from types import SimpleNamespace

from flask import Flask
import pytest

import config
from studio.description import (AUDIO_CREDIT_START, AUDIO_CREDIT_END, build_description,
                                ensure_audio_credits, load_audio_credits, load_sound_report, audio_credit_text)


def asset(index=1):
    return {"id": f"{index:032x}", "kind": "music" if index == 1 else "sfx", "title": f"Recording {index}",
            "creator": f"Artist {index}", "source_url": f"https://commons.wikimedia.org/wiki/File:Sound_{index}.ogg",
            "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": f"Additional credit required by recording {index}: Nature archive.",
            "mode": "bed" if index == 1 else "transitions", "volume": .08}


def write_commit(output, assets):
    media = output / "soundtracks" / "current.wav"
    media.parent.mkdir(exist_ok=True)
    media.write_bytes(b"the exact committed soundtrack")
    binding = {"version": 1, "path": "soundtracks/current.wav", "audio_sha256": "current-recording"}
    manifest = {"version": 2, "path": binding["path"], "audio_sha256": binding["audio_sha256"],
                "soundtrack_sha256": hashlib.sha256(media.read_bytes()).hexdigest(), "credits": assets,
                "sound_design": {**binding, "summary": "Current design"},
                "audio_master": {**binding, "status": "applied", "output_lufs": -16}}
    (output / "render_audio.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_audio_credits_follow_the_recording_and_preserve_imported_source(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    manifest = {"version": 1, "audio_sha256": "current-recording", "path": "narration_mixed.wav", "assets": [{
        "id": "a" * 32, "kind": "music", "title": "Calm piano", "filename": "calm.mp3",
        "creator": "Example musician", "source_url": "https://pixabay.com/music/example/",
        "license_note": "Download reference kept locally", "content_id": "registered",
        "mode": "bed", "volume": .08,
    }]}
    path = tmp_path / "audio_credits.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "narration_mixed.wav").write_bytes(b"prepared soundtrack")
    (tmp_path / "render_audio.json").write_text(json.dumps({
        "path": "narration_mixed.wav", "audio_sha256": "current-recording",
    }), encoding="utf-8")
    timeline = {"audio_sha256": "current-recording"}
    assets = load_audio_credits(timeline)
    assert assets == manifest["assets"]
    text = build_description({"title": "A quiet topic", "sections": [], "tags": []}, {}, timeline=timeline)
    assert "Music: Calm piano · Example musician · https://pixabay.com/music/example/" in text
    assert load_audio_credits({"audio_sha256": "different-recording"}) == []
    stale_text = build_description({"sections": []}, {}, timeline={"audio_sha256": "different-recording"})
    assert "Calm piano" not in stale_text
    # Losing the mix or falling back to clean voice cannot claim the old music.
    (tmp_path / "narration_mixed.wav").unlink()
    assert load_audio_credits(timeline) == []
    (tmp_path / "narration_mixed.wav").write_bytes(b"old soundtrack still present")
    (tmp_path / "narration.wav").write_bytes(b"clean narration")
    (tmp_path / "render_audio.json").write_text(json.dumps({
        "path": "narration.wav", "audio_sha256": "current-recording",
    }), encoding="utf-8")
    assert load_audio_credits(timeline) == []


def test_audio_credit_metadata_does_not_expose_paths_or_unsafe_links(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    path = tmp_path / "audio_credits.json"
    (tmp_path / "narration_effects.wav").write_bytes(b"prepared soundtrack")
    (tmp_path / "render_audio.json").write_text(json.dumps({
        "path": "narration_effects.wav", "audio_sha256": "current-recording",
    }), encoding="utf-8")
    path.write_text(json.dumps({"version": 1, "path": "narration_effects.wav", "audio_sha256": "current-recording", "assets": [
        {"kind": "sfx", "title": "Wind", "path": "/private/source.wav",
         "source_url": "javascript:alert(1)", "volume": 3},
        {"kind": "music", "source_url": "https://user:password@example.com/private"},
        {"kind": "other", "title": "Ignore me"},
    ]}), encoding="utf-8")
    assets = load_audio_credits()
    assert len(assets) == 2
    assert all(row["source_url"] == "" and "path" not in row for row in assets)
    assert "volume" not in assets[0]
    path.write_text("{broken", encoding="utf-8")
    assert load_audio_credits() == []


def test_all_thirteen_applied_assets_have_complete_untruncated_credits(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    assets = [asset(index) for index in range(1, 14)]
    write_commit(tmp_path, assets)
    assert len(load_audio_credits()) == 13
    text = build_description({"description": "Generated prose. " * 600, "sections": []}, {})
    assert len(text) <= 5000 and text.endswith(AUDIO_CREDIT_END)
    for row in assets:
        assert row["source_url"] in text and row["attribution"] in text
        assert row["creator"] in text


def test_managed_block_refresh_preserves_user_prose_and_clears_old_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    first = asset(1)
    write_commit(tmp_path, [first])
    prose = "My edited opening stays exactly as written.\n\nMy final paragraph."
    before = ensure_audio_credits(prose)
    assert before.startswith(prose + "\n\n")
    assert ensure_audio_credits(before) == before
    second = asset(2)
    write_commit(tmp_path, [second])
    after = ensure_audio_credits(before)
    assert after.startswith(prose + "\n\n")
    assert first["source_url"] not in after and second["source_url"] in after
    assert after.count(AUDIO_CREDIT_START) == 1
    write_commit(tmp_path, [])
    assert ensure_audio_credits(after) == prose


def test_custom_description_or_attribution_overflow_returns_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    write_commit(tmp_path, [asset()])
    prose = "Creator's words. " * 400
    with pytest.raises(ValueError, match="Shorten your description"):
        ensure_audio_credits(prose)
    oversized = asset()
    oversized["attribution"] = "Required additional attribution. " * 200
    write_commit(tmp_path, [oversized])
    with pytest.raises(ValueError, match="credits alone exceed"):
        ensure_audio_credits("")
    with pytest.raises(ValueError, match="credits alone exceed"):
        build_description({"sections": []}, {})


def test_rebuilding_generated_description_removes_saved_block_before_shortening(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    old = asset(99)
    old["attribution"] = "Older attribution text. " * 70
    write_commit(tmp_path, [old])
    saved = ensure_audio_credits("Previously saved prose. " * 20)
    write_commit(tmp_path, [asset(index) for index in range(1, 14)])
    result = build_description({"description": saved, "sections": []}, {})
    assert result.count(AUDIO_CREDIT_START) == 1 and old["source_url"] not in result
    assert "Older attribution" not in result
    assert len(result) <= 5000 and result.endswith(AUDIO_CREDIT_END)


def test_unsafe_license_links_cannot_satisfy_required_attribution(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    unsafe = asset()
    unsafe["license_url"] = "javascript:alert(1)"
    write_commit(tmp_path, [unsafe])
    assert load_audio_credits()[0]["license_url"] == ""
    with pytest.raises(ValueError, match="license link"):
        ensure_audio_credits("Original prose")


def test_v2_receipt_is_authoritative_and_binds_actual_soundtrack_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    write_commit(tmp_path, [asset()])
    # Failed compatibility-copy writes cannot revive an earlier sound design.
    (tmp_path / "audio_credits.json").write_text(json.dumps({"version": 1, "assets": [asset(99)]}), encoding="utf-8")
    (tmp_path / "sound_design.json").write_text(json.dumps({"version": 1, "summary": "Old design"}), encoding="utf-8")
    assert load_audio_credits()[0]["id"] == asset()["id"]
    assert load_sound_report("sound_design.json")["summary"] == "Current design"
    assert load_sound_report("audio_master.json")["status"] == "applied"
    (tmp_path / "soundtracks" / "current.wav").write_bytes(b"a different soundtrack")
    assert load_audio_credits() == [] and load_sound_report("sound_design.json") is None
    with pytest.raises(ValueError, match="could not be verified"):
        ensure_audio_credits("Original prose")


def test_inline_reports_must_match_the_committed_path_and_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    manifest = write_commit(tmp_path, [asset()])
    manifest["sound_design"]["path"] = "a-different-soundtrack.wav"
    (tmp_path / "render_audio.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert load_sound_report("sound_design.json") is None
    assert load_sound_report("audio_master.json", {"audio_sha256": "other-voice"}) is None


def test_downloadable_credits_match_description_and_keep_bundled_source_details(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    recording = {**asset(), "provider": "bundled", "selection_source": "bundled_catalog",
                 "changes": "Excerpted and converted to Ogg."}
    write_commit(tmp_path, [recording])
    timeline = {"audio_sha256": "current-recording"}
    text = audio_credit_text(timeline)
    assert ensure_audio_credits("My own description", timeline) == "My own description\n\n" + text
    assert all(recording[key] in text for key in ("title", "creator", "source_url", "license", "license_url", "attribution"))
    assert load_audio_credits(timeline)[0]["selection_source"] == "bundled_catalog"
    assert load_audio_credits(timeline)[0]["changes"] == recording["changes"]
    assert audio_credit_text({"audio_sha256": "another-voice"}) == ""
    (tmp_path / "soundtracks" / "current.wav").write_bytes(b"different bytes")
    with pytest.raises(ValueError, match="could not be verified"):
        audio_credit_text(timeline)


@pytest.fixture
def upload_context(tmp_path, monkeypatch):
    from studio import channels, upload
    from studio.state import state
    previous = deepcopy(state)
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    video = tmp_path / "final.mp4"
    video.write_bytes(b"finished video fixture")
    state.update(running=False, uploading=False, visual_editing=False, preview_only=False,
                 visual_plan=None, preview_path=None, video_path=str(video), profile_id="default",
                 timeline={"audio_sha256": "current-recording"}, script={"title": "A video", "tags": []},
                 description="Creator's own description.", stages={"video": "done"})
    monkeypatch.setattr(channels, "is_connecting", lambda: False)
    monkeypatch.setattr(channels, "get_profile", lambda ident: {"id": ident, "name": "Test profile"})
    monkeypatch.setattr(channels, "upload_options", lambda ident: {})
    monkeypatch.setattr(channels, "config_overrides", lambda ident: {})
    monkeypatch.setattr(upload, "secret_ok", lambda ident: True)
    monkeypatch.setattr(upload, "sync_library", lambda: None)
    monkeypatch.setattr(upload.threading, "Thread", lambda target, daemon: SimpleNamespace(start=target))
    published = []
    def fake_publish(path, **options):
        published.append(options)
        return {"url": "https://youtube.example/fixture", "thumbnail_error": None}
    monkeypatch.setattr(upload.yt_uploader, "upload_video", fake_publish)
    app = Flask(__name__)
    app.register_blueprint(upload.bp)
    yield app.test_client(), published
    state.clear()
    state.update(previous)


def test_upload_adds_applied_attribution_without_replacing_user_prose(tmp_path, upload_context):
    from studio.state import state
    client, published = upload_context
    write_commit(tmp_path, [asset()])
    response = client.post("/api/upload", json={"privacy": "private"})
    assert response.json == {"ok": True} and len(published) == 1
    description = published[0]["description"]
    assert description.startswith("Creator's own description.\n\n")
    assert asset()["attribution"] in description and asset()["license_url"] in description
    assert state["description"] == description


def test_upload_refuses_overflow_before_starting_publishing(tmp_path, upload_context):
    from studio.state import state
    client, published = upload_context
    write_commit(tmp_path, [asset()])
    state["description"] = "A" * 4999
    response = client.post("/api/upload", json={"privacy": "private"})
    assert response.status_code == 400 and "5,000-character" in response.json["error"]
    assert not published and not state["uploading"]
    assert state["description"] == "A" * 4999
