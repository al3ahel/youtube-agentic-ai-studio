"""An upgrade preserves deliberate old selections without trapping Automatic."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import wave

import pytest

import config
from studio import channels


@pytest.fixture
def legacy_channel(tmp_path, monkeypatch):
    from studio.state import state, stop_event
    previous = deepcopy(state)
    state.update(running=False, uploading=False, visual_editing=False)
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "IMAGES_DIR", str(tmp_path / "images"))
    monkeypatch.setattr(config, "SOUND_MODE", "auto")
    profile = channels.create_profile("Imported soundtrack channel")
    registry = channels._read_registry()
    old = next(row for row in registry["profiles"] if row["id"] == profile["id"])
    old["defaults"].pop("sound_mode")
    old["defaults"].update(music_enabled=True, music_track="a" * 32, audio_mastering=False)
    channels._write_registry(registry)
    before = deepcopy(old)
    stop_event.clear()
    try:
        yield profile["id"], before
    finally:
        state.clear()
        state.update(previous)
        stop_event.clear()


def test_selected_legacy_music_is_manual_until_automatic_is_explicit(tmp_path):
    path = str(tmp_path / "settings.json")
    values = {"MUSIC_ENABLED": True, "MUSIC_TRACK": "a" * 32}
    with open(path, "w") as handle:
        json.dump(values, handle)
    assert config.load_settings(path)["SOUND_MODE"] == "manual"
    config.save_settings({"SOUND_MODE": "auto"}, path)
    assert config.load_settings(path)["SOUND_MODE"] == "auto"
    assert config.load_settings(path)["MUSIC_TRACK"] == "a" * 32


def test_music_switch_without_a_track_does_not_migrate_to_empty_manual_folder(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"MUSIC_ENABLED": true}')
    assert "SOUND_MODE" not in config.load_settings(str(path))


def test_legacy_channel_selected_asset_is_preserved_and_new_profiles_are_automatic():
    profile = channels.create_profile("Old channel")
    registry = channels._read_registry()
    old = next(row for row in registry["profiles"] if row["id"] == profile["id"])
    old["defaults"].pop("sound_mode")
    old["defaults"].update(music_enabled=True, music_track="a" * 32)
    channels._write_registry(registry)
    assert channels.get_profile(profile["id"])["defaults"]["sound_mode"] == "manual"
    channels.update_profile_defaults(profile["id"], {"sound_mode": "auto"})
    assert channels.get_profile(profile["id"])["defaults"]["sound_mode"] == "auto"
    assert channels.create_profile("New channel")["defaults"]["sound_mode"] == "auto"


def test_legacy_manual_choice_reaches_captured_runtime_without_rewriting_preferences(legacy_channel):
    from studio.profile_context import capture_profile_settings
    profile_id, stored = legacy_channel
    channels.create_profile("Another active channel")
    assert channels.get_profile(profile_id)["defaults"]["sound_mode"] == "manual"
    with config.profile_context(capture_profile_settings(profile_id)):
        assert config.SOUND_MODE == "manual"
        assert config.MUSIC_TRACK == "a" * 32
    current = next(row for row in channels._read_registry()["profiles"] if row["id"] == profile_id)
    assert current == stored


def test_settings_api_uses_legacy_manual_choice_before_any_save(legacy_channel):
    from flask import Flask
    from studio import profile_context, settings
    profile_id, stored = legacy_channel
    app = Flask(__name__)
    profile_context.init_app(app)
    app.register_blueprint(settings.bp)
    response = app.test_client().get("/api/settings", query_string={"profile_id": profile_id})
    assert response.status_code == 200
    assert response.json["profile_id"] == profile_id
    assert response.json["sound_mode"] == "manual"
    assert response.json["music_track"] == "a" * 32
    assert next(row for row in channels._read_registry()["profiles"] if row["id"] == profile_id) == stored


def test_worker_uses_legacy_selected_recording_without_saving_settings(legacy_channel, tmp_path, monkeypatch):
    from agents import audio_director
    from studio import audio_library, preflight, runner
    from studio.state import state
    from video import music
    from video.timeline import script_signature
    profile_id, stored = legacy_channel
    channels.create_profile("Another active channel")
    script = {"title": "A calm river", "sections": [{"id": 1, "title": "Flow",
              "narration": "Water moves through the landscape."}]}
    voice = tmp_path / "narration.wav"
    with wave.open(str(voice), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(b"\x00\x00" * 48000)
    track = tmp_path / "selected.wav"
    shutil.copyfile(voice, track)
    (tmp_path / "script.json").write_text(json.dumps(script))
    (tmp_path / "narration.timings.json").write_text(json.dumps({
        "version": 1, "script_signature": script_signature(script), "duration": 1,
        "sections": [{"id": 1, "start": 0, "duration": 1, "caption_timing": "estimated"}], "words": []}))
    state.update(profile_id=profile_id, script=script, research={}, visual_plan=None, timeline=None,
                 audio_path=str(voice), video_path=None, preview_path=None, image_map={},
                 error=None, library_id=None, description="My publishing text")
    monkeypatch.setattr(preflight, "check_environment", lambda *args: {"ok": True, "checks": []})
    monkeypatch.setattr(audio_director, "direct_audio", lambda *a, **kw: pytest.fail("Legacy manual choice invoked Automatic"))
    seen = []

    def selected(owner, identifier, kind=None):
        seen.append((owner, identifier, kind))
        return {"id": identifier, "kind": kind, "path": str(track), "title": "My chosen music"}

    def mix(narration_path, output_path, **kwargs):
        assert kwargs["track_path"] == str(track)
        shutil.copyfile(narration_path, output_path)
        return output_path

    monkeypatch.setattr(audio_library, "resolve_audio_asset", selected)
    monkeypatch.setattr(music, "mix_music_with_narration", mix)
    runner.run_pipeline_thread(["music"], visual_mode="classic", profile_id=profile_id)

    assert state["error"] is None
    assert seen == [(profile_id, "a" * 32, "music")]
    manifest = json.loads((tmp_path / "render_audio.json").read_text())
    assert manifest["sound_design"]["mode"] == "manual"
    assert manifest["sound_design"]["profile_id"] == profile_id
    assert manifest["credits"][0]["id"] == "a" * 32
    assert (tmp_path / manifest["path"]).is_file()
    assert Path(state["audio_path"]) == voice
    assert next(row for row in channels._read_registry()["profiles"] if row["id"] == profile_id) == stored
