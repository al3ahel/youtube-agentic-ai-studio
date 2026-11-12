"""Imported sound belongs to its explicit channel and is validated before saving."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import wave

import pytest

import config
from studio import audio_library, channels, create_app, library
from studio.state import fresh_project_state, state


@pytest.fixture
def client(tmp_path, monkeypatch):
    previous = deepcopy(state)
    state.clear()
    state.update(fresh_project_state())
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(config, "IMAGES_DIR", str(output / "images"))
    monkeypatch.setattr(config, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(library, "LIB_DIR", str(output / "library"))
    for key in config.USER_SETTINGS:
        monkeypatch.setattr(config, key, deepcopy(vars(config)[key]))
    app = create_app()
    app.config["TESTING"] = True
    yield app.test_client()
    state.clear()
    state.update(previous)


def _wav():
    data = io.BytesIO()
    with wave.open(data, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 800)
    return data.getvalue()


def _body(**changes):
    return {"filename": "calm.wav", "kind": "music",
            "data_base64": base64.b64encode(_wav()).decode("ascii"), **changes}


def _import(client, profile="default", **changes):
    if not shutil.which("ffprobe"):
        pytest.skip("FFmpeg ffprobe is needed for real imported-audio verification")
    response = client.post("/api/audio-library/import", query_string={"profile_id": profile}, json=_body(**changes))
    assert response.status_code == 201, response.get_json()
    assert response.json["ok"] is True
    return response.json["asset"]


def _stored_files():
    return sorted(path for path in (Path(config.DATA_DIR) / "audio-library").rglob("*") if path.is_file())


def test_real_wav_import_preview_and_registry_are_owned_and_do_not_change_preferences(client):
    profile = channels.create_profile("Calm recordings", "asmr")
    before_defaults = deepcopy(profile["defaults"])
    before_state = deepcopy(state)
    asset = _import(client, profile["id"], filename="Quiet ocean.WAV", title="  Ocean ambience  ",
                    creator="  Example creator  ", source_url=" https://pixabay.com/sound-effects/example/ ",
                    license_note="Saved license reference\nDownloaded by the channel owner", content_id="registered")
    assert re.fullmatch(r"[a-f0-9]{32}", asset["id"])
    assert asset["title"] == "Ocean ambience" and asset["creator"] == "Example creator"
    assert asset["filename"] == "Quiet ocean.WAV"
    assert asset["duration"] == pytest.approx(.1) and asset["bytes"] == len(_wav())
    assert asset["content_id"] == "registered"
    assert "path" not in asset and str(Path(config.DATA_DIR)) not in json.dumps(asset)
    assert parse_qs(urlsplit(asset["preview_url"]).query) == {"profile_id": [profile["id"]]}
    listing = client.get("/api/audio-library", query_string={"profile_id": profile["id"]})
    assert listing.json == {"ok": True, "assets": [asset]}
    assert listing.headers["Cache-Control"] == "no-store"
    preview = client.get(asset["preview_url"])
    assert preview.status_code == 200 and preview.data == _wav()
    assert preview.mimetype == "audio/wav" and preview.headers["Cache-Control"] == "no-store"
    assert preview.headers["X-Content-Type-Options"] == "nosniff"
    resolved = audio_library.resolve_audio_asset(profile["id"], asset["id"], kind="music")
    path = Path(resolved.pop("path"))
    assert resolved == asset
    assert path == Path(config.DATA_DIR).resolve() / "audio-library" / profile["id"] / "music" / (asset["id"] + ".wav")
    registry = path.parent.parent / "registry.json"
    assert str(Path(config.DATA_DIR)) not in registry.read_text(encoding="utf-8")
    assert list(json.loads(registry.read_text(encoding="utf-8"))["assets"]) == [asset["id"]]
    assert channels.get_profile(profile["id"])["defaults"] == before_defaults
    assert state == before_state


def test_explicit_profile_survives_active_channel_switch_and_never_exposes_foreign_audio(client, monkeypatch):
    news = channels.create_profile("News recordings", "news")
    calm = channels.create_profile("Calm recordings", "asmr")
    real_probe = audio_library._probe_audio
    def probe_and_switch(path):
        duration = real_probe(path)
        channels.activate_profile(calm["id"])
        return duration
    monkeypatch.setattr(audio_library, "_probe_audio", probe_and_switch)
    asset = _import(client, news["id"], kind="sfx")
    assert channels.active_profile_id() == calm["id"]
    assert client.get("/api/audio-library").json["assets"] == []
    assert client.get("/api/audio-library", query_string={"profile_id": news["id"]}).json["assets"] == [asset]
    foreign = client.get(f"/api/audio-library/{asset['id']}/preview", query_string={"profile_id": calm["id"]})
    assert foreign.status_code == 404 and foreign.json["ok"] is False
    assert client.get(asset["preview_url"]).data == _wav()
    for owner, kind in ((calm["id"], "sfx"), (news["id"], "music")):
        with pytest.raises(audio_library.AudioLibraryError) as error:
            audio_library.resolve_audio_asset(owner, asset["id"], kind=kind)
        assert error.value.status == 404


@pytest.mark.parametrize("kind,setting,enable", [("music", "music_track", "music_enabled"), ("sfx", "sfx_track", "sfx_enabled")])
def test_settings_accept_owned_imports_and_refuse_foreign_or_wrong_kind_without_mutation(client, kind, setting, enable):
    owner = channels.create_profile("Owner")
    other = channels.create_profile("Other")
    asset = _import(client, owner["id"], kind=kind)
    response = client.post("/api/save-settings", query_string={"profile_id": owner["id"]},
                           json={setting: asset["id"], enable: True})
    assert response.status_code == 200 and response.json["ok"]
    own_defaults = channels.get_profile(owner["id"])["defaults"]
    assert own_defaults[setting] == asset["id"] and own_defaults[enable] is True
    other_before = deepcopy(channels.get_profile(other["id"])["defaults"])
    response = client.post("/api/save-settings", query_string={"profile_id": other["id"]},
                           json={setting: asset["id"], enable: True})
    assert response.status_code == 400 and response.json["ok"] is False
    assert channels.get_profile(other["id"])["defaults"] == other_before
    wrong_setting = "sfx_track" if kind == "music" else "music_track"
    response = client.post("/api/save-settings", query_string={"profile_id": owner["id"]}, json={wrong_setting: asset["id"]})
    assert response.status_code == 400
    assert channels.get_profile(owner["id"])["defaults"] == own_defaults


@pytest.mark.parametrize("profile_kind", ["default", "separate"])
@pytest.mark.parametrize("mode", ["auto", "off"])
@pytest.mark.parametrize("kind,setting,enable", [("music", "music_track", "music_enabled"), ("sfx", "sfx_track", "sfx_enabled")])
def test_unavailable_retained_manual_track_does_not_block_automatic_or_off(client, profile_kind, mode, kind, setting, enable):
    profile_id = "default" if profile_kind == "default" else channels.create_profile("Saved soundtrack")["id"]
    asset = _import(client, profile_id, kind=kind)
    selected = {"sound_mode": "manual", setting: asset["id"], enable: True}
    response = client.post("/api/save-settings", query_string={"profile_id": profile_id}, json=selected)
    assert response.status_code == 200
    Path(audio_library.resolve_audio_asset(profile_id, asset["id"], kind=kind)["path"]).unlink()

    # A full Settings save still contains the now-hidden manual selection.
    response = client.post("/api/save-settings", query_string={"profile_id": profile_id},
                           json={**selected, "sound_mode": mode})
    assert response.status_code == 200, response.json
    saved = client.get("/api/settings", query_string={"profile_id": profile_id}).json
    assert saved["sound_mode"] == mode and saved[setting] == asset["id"]

    # Re-enabling a used manual layer must validate even a retained selection.
    response = client.post("/api/save-settings", query_string={"profile_id": profile_id},
                           json={"sound_mode": "manual"})
    assert response.status_code == 400
    assert client.get("/api/settings", query_string={"profile_id": profile_id}).json["sound_mode"] == mode


@pytest.mark.parametrize("changes", [
    {"filename": "../outside.wav"}, {"filename": "folder/file.wav"},
    {"filename": "C:\\private\\file.wav"}, {"filename": "clip.wav:stream"},
    {"filename": "clip\n.wav"}, {"filename": "clip.exe"}, {"filename": "a" * 252 + ".wav"},
    {"filename": None}, {"kind": "video"}, {"kind": []}, {"path": "/private/file.wav"},
    {"title": "x" * 201}, {"title": "bad\x00title"}, {"creator": []},
    {"source_url": "file:///private/audio.wav"}, {"source_url": "javascript:alert(1)"},
    {"source_url": "https://user:password@example.com/audio"},
    {"source_url": "https://example.com:invalid/audio"}, {"source_url": "https://exam ple.com/audio"},
    {"license_note": "x" * 2001}, {"content_id": True}, {"content_id": "yes"},
    {"data_base64": ""}, {"data_base64": None}, {"data_base64": "!!!"},
    {"data_base64": "data:audio/wav;base64,AAAA"},
])
def test_invalid_metadata_or_bytes_are_rejected_before_file_or_probe(client, monkeypatch, changes):
    monkeypatch.setattr(audio_library, "_probe_audio", lambda path: pytest.fail("Invalid upload reached ffprobe"))
    response = client.post("/api/audio-library/import", json=_body(**changes))
    assert response.status_code == 400 and response.json["ok"] is False
    assert _stored_files() == []


@pytest.mark.parametrize("raw", [b"{broken", b"[]", b"null", b'"not an object"'])
def test_import_requires_a_json_object(client, raw):
    response = client.post("/api/audio-library/import", data=raw, content_type="application/json")
    assert response.status_code == 400 and response.json["ok"] is False
    assert _stored_files() == []


def test_import_retains_same_origin_json_guard(client):
    response = client.post("/api/audio-library/import", data={"filename": "clip.wav"})
    assert response.status_code == 415
    response = client.post("/api/audio-library/import", json=_body(), headers={"Origin": "https://unrelated.example"})
    assert response.status_code == 403
    assert _stored_files() == []


@pytest.mark.parametrize("limit", ["MAX_AUDIO_BYTES", "MAX_BASE64_CHARS"])
def test_encoded_and_decoded_size_limits_reject_before_probing(client, monkeypatch, limit):
    monkeypatch.setattr(audio_library, limit, 32)
    monkeypatch.setattr(audio_library, "_probe_audio", lambda path: pytest.fail("Oversized upload reached ffprobe"))
    response = client.post("/api/audio-library/import", json=_body())
    assert response.status_code == 413 and "20 MiB" in response.json["error"]
    assert _stored_files() == []


@pytest.mark.parametrize("known_length", [True, False])
def test_request_read_is_bounded_even_without_content_length(client, monkeypatch, known_length):
    monkeypatch.setattr(audio_library, "MAX_REQUEST_BYTES", 128)
    monkeypatch.setattr(audio_library, "import_audio_asset", lambda *args: pytest.fail("Oversized request reached importer"))
    overrides = {} if known_length else {"CONTENT_LENGTH": "", "wsgi.input_terminated": True}
    response = client.post("/api/audio-library/import", data=b" " * 512, content_type="application/json", environ_overrides=overrides)
    assert response.status_code == 413
    assert _stored_files() == []


def test_unreadable_audio_rejects_and_cleans_the_temporary_upload(client):
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe is needed to reject a real malformed file")
    response = client.post("/api/audio-library/import", json=_body(data_base64=base64.b64encode(b"not audio").decode()))
    assert response.status_code == 400 and "not readable audio" in response.json["error"]
    assert _stored_files() == []


def test_missing_ffprobe_returns_actionable_error_without_saving_asset(client, monkeypatch):
    monkeypatch.setattr(audio_library.shutil, "which", lambda executable: None)
    response = client.post("/api/audio-library/import", json=_body())
    assert response.status_code == 503 and "ffprobe" in response.json["error"] and "restart" in response.json["error"]
    assert _stored_files() == []


@pytest.mark.parametrize("info", [
    {"streams": []},
    *({"streams": [{"codec_type": "audio"}], "format": {"duration": value}}
      for value in (0, -1, "nan", "inf", 7200.1, True, None)),
])
def test_probe_rejects_missing_audio_or_invalid_duration(client, monkeypatch, info):
    monkeypatch.setattr(audio_library.shutil, "which", lambda executable: "/local/ffprobe")
    monkeypatch.setattr(audio_library.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(info).encode()))
    response = client.post("/api/audio-library/import", json=_body())
    assert response.status_code == 400
    assert _stored_files() == []


def test_probe_uses_bounded_local_container_validation(client, monkeypatch):
    calls = []
    monkeypatch.setattr(audio_library.shutil, "which", lambda executable: "/local/ffprobe")
    def run(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(audio_library.subprocess, "run", run)
    response = client.post("/api/audio-library/import", json=_body())
    assert response.status_code == 400 and "too long" in response.json["error"]
    command, options = calls[0]
    assert options["timeout"] == 10 and options["check"] is True
    assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
    assert command[command.index("-f") + 1] == "wav"
    assert _stored_files() == []


def test_deleted_profile_files_cannot_be_resolved_or_previewed(client):
    owner = channels.create_profile("Disposable imports")
    asset = _import(client, owner["id"])
    channels.delete_profile(owner["id"])
    with pytest.raises(audio_library.AudioLibraryError) as error:
        audio_library.resolve_audio_asset(owner["id"], asset["id"])
    assert error.value.status == 404
    assert client.get(asset["preview_url"]).status_code == 400  # The shared profile middleware rejects unknown profiles.
    assert client.get("/api/audio-library").json == {"ok": True, "assets": []}


def test_profile_deletion_during_validation_cannot_commit_or_redirect_import(client, monkeypatch):
    owner = channels.create_profile("Deleted during import")
    def delete_during_probe(path):
        channels.delete_profile(owner["id"])
        return .1
    monkeypatch.setattr(audio_library, "_probe_audio", delete_during_probe)
    response = client.post("/api/audio-library/import", query_string={"profile_id": owner["id"]}, json=_body())
    assert response.status_code == 404
    assert _stored_files() == []
    assert client.get("/api/audio-library").json["assets"] == []


@pytest.mark.parametrize("identifier", [None, "../default", "a" * 31, "A" * 32, "a" * 32 + ".wav"])
def test_resolver_never_accepts_a_client_path_or_unrecognized_identifier(client, identifier):
    with pytest.raises(audio_library.AudioLibraryError) as error:
        audio_library.resolve_audio_asset("default", identifier)
    assert error.value.status == 404


@pytest.mark.parametrize("owner", [None, "../../private", "a" * 32])
def test_resolver_requires_an_existing_explicit_profile(client, owner):
    with pytest.raises(audio_library.AudioLibraryError) as error:
        audio_library.list_audio_assets(owner)
    assert error.value.status == 404


def test_missing_asset_and_symlinked_media_cannot_be_listed_or_streamed(client, tmp_path):
    asset = _import(client)
    path = Path(audio_library.resolve_audio_asset("default", asset["id"])["path"])
    path.unlink()
    assert client.get("/api/audio-library").json["assets"] == []
    assert client.get(asset["preview_url"]).status_code == 404
    outside = tmp_path / "private.wav"
    outside.write_bytes(_wav())
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks unavailable on this platform")
    assert client.get("/api/audio-library").json["assets"] == []
    assert client.get(asset["preview_url"]).status_code == 404
    assert outside.read_bytes() == _wav()


def test_failed_registry_write_keeps_prior_asset_without_orphaning_new_import(client, monkeypatch):
    original = _import(client)
    before = {path: path.read_bytes() for path in _stored_files()}
    monkeypatch.setattr(audio_library, "_probe_audio", lambda path: .1)
    def fail_write(*args):
        raise OSError("Simulated failed atomic registry replacement")
    monkeypatch.setattr(audio_library, "_write_registry", fail_write)
    response = client.post("/api/audio-library/import", json=_body(filename="another.wav"))
    assert response.status_code == 500 and response.json["ok"] is False
    assert {path: path.read_bytes() for path in _stored_files()} == before
    assert client.get("/api/audio-library").json["assets"] == [original]


def test_corrupt_registry_is_not_overwritten_by_an_import(client, monkeypatch):
    original = _import(client)
    path = Path(audio_library.resolve_audio_asset("default", original["id"])["path"])
    registry = path.parent.parent / "registry.json"
    registry.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(audio_library, "_probe_audio", lambda path: .1)
    response = client.post("/api/audio-library/import", json=_body())
    assert response.status_code == 409 and "kept" in response.json["error"]
    assert registry.read_text(encoding="utf-8") == "{broken" and path.read_bytes() == _wav()
    assert _stored_files() == sorted([path, registry])


def test_overlapping_imports_keep_both_assets_and_separate_files(client, monkeypatch):
    barrier = threading.Barrier(2)
    def simultaneous_probe(path):
        barrier.wait(timeout=5)
        return .1
    monkeypatch.setattr(audio_library, "_probe_audio", simultaneous_probe)
    with ThreadPoolExecutor(max_workers=2) as executor:
        imports = list(executor.map(lambda kind: audio_library.import_audio_asset("default", _body(kind=kind)), ["music", "sfx"]))
    assert len({asset["id"] for asset in imports}) == 2
    assert {asset["id"] for asset in audio_library.list_audio_assets("default")} == {asset["id"] for asset in imports}
    assert all(Path(audio_library.resolve_audio_asset("default", asset["id"])["path"]).read_bytes() == _wav() for asset in imports)
    assert not list((Path(config.DATA_DIR) / "audio-library").rglob(".import-*"))
