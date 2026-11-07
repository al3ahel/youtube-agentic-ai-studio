"""Channel isolation, identity verification, profile defaults and OAuth lifecycle."""
import json
from copy import deepcopy
from pathlib import Path

from flask import Flask
import pytest

import config
from studio import channels, security
from studio.state import state
from uploader import youtube

CHANNEL_A = "UC" + "a" * 22
CHANNEL_B = "UC" + "b" * 22
HEADERS = {"Host": "localhost:7070"}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"), raising=False)
    monkeypatch.setattr(config, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "output"))
    # Default-profile edits intentionally update legacy settings in memory.
    for key in config.USER_SETTINGS:
        monkeypatch.setattr(config, key, deepcopy(vars(config)[key]))
    monkeypatch.setattr(youtube, "TOKEN_FILE", str(tmp_path / "legacy-token.json"))
    monkeypatch.setattr(config, "YOUTUBE_CLIENT_SECRET", str(tmp_path / "client_secret.json"))
    (tmp_path / "client_secret.json").write_text('{"installed": {}}')
    for key in ("running", "uploading", "visual_editing", "channel_connecting"):
        monkeypatch.setitem(state, key, False)
    channels._connections.clear()
    yield tmp_path
    channels._connections.clear()


@pytest.fixture
def client(isolated):
    app = Flask(__name__)
    app.testing = True
    security.init_app(app)
    app.register_blueprint(channels.bp)
    return app.test_client()


def _channel(ident=CHANNEL_A):
    return {"id": ident, "title": "Verified channel", "thumbnail": "",
            "url": "https://www.youtube.com/channel/" + ident, "fetched_at": "2026-01-01T00:00:00+00:00"}


def _token(profile_id, value="saved-secret"):
    path = channels.token_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def test_default_preserves_legacy_settings_without_freezing_them(isolated, monkeypatch):
    monkeypatch.setattr(config, "CHANNEL_NAME", "Existing channel")
    first = channels.get_profile()
    assert first["id"] == "default" and first["defaults"]["channel_name"] == "Existing channel"
    assert channels.config_overrides() == {"CHANNEL_PRESET": "general"}
    monkeypatch.setattr(config, "VOICE_RATE", "+7%")
    assert channels.get_profile("default")["defaults"]["voice_rate"] == "+7%"
    saved = json.loads((Path(config.DATA_DIR) / "channels.json").read_text())
    assert saved["profiles"][0]["defaults"] == {}


def test_profiles_keep_independent_defaults_and_activation_never_mutates_config(isolated):
    original = config.VOICE_RATE
    news = channels.create_profile("Daily news", "news")
    calm = channels.create_profile("Quiet evenings", "asmr")
    channels.update_profile_defaults(news["id"], {"voice_rate": "+8%", "music_library": "/my/news/music"})
    channels.activate_profile(calm["id"])
    assert config.VOICE_RATE == original
    assert channels.active_profile_id() == calm["id"]
    assert channels.get_profile(news["id"])["defaults"]["voice_rate"] == "+8%"
    assert channels.get_profile(calm["id"])["defaults"]["voice_rate"] == "-15%"
    assert channels.get_profile(calm["id"])["defaults"]["music_enabled"] is False
    assert channels.config_overrides(news["id"])["MUSIC_LIBRARY_DIR"] == "/my/news/music"
    assert channels.config_overrides(calm["id"])["YOUTUBE_CHANNEL_ID"] == ""


def test_default_profile_edits_share_legacy_settings_without_duplicate_overrides(isolated, monkeypatch):
    original_name = config.CHANNEL_NAME
    channels.update_profile("default", {
        "name": "Local label", "preset": "asmr",
        "defaults": {"voice_rate": "-10%", "duration_mins": 12,
                     "music_library": "/my/calm/recordings", "video_type": "shorts"},
    })
    saved = config.load_settings()
    assert saved["VOICE_RATE"] == "-10%" and saved["TARGET_DURATION_MINUTES"] == 12
    assert saved["CHANNEL_PRESET"] == "asmr"
    assert config.CHANNEL_NAME == original_name
    raw = json.loads((Path(config.DATA_DIR) / "channels.json").read_text())["profiles"][0]
    assert raw["name"] == "Local label"
    assert raw["defaults"] == {"music_library": "/my/calm/recordings", "video_type": "shorts"}
    assert channels.config_overrides("default") == {
        "MUSIC_LIBRARY_DIR": "/my/calm/recordings", "CHANNEL_PRESET": "asmr",
    }
    monkeypatch.setattr(config, "VOICE_RATE", "+4%")
    monkeypatch.setattr(config, "CHANNEL_PRESET", "news")
    profile = channels.get_profile("default")
    assert profile["defaults"]["voice_rate"] == "+4%" and profile["preset"] == "news"


def test_default_profile_validates_combined_settings_before_any_write(isolated, monkeypatch):
    monkeypatch.setattr(config, "BROLL_INTERVAL", 10)
    monkeypatch.setattr(config, "BROLL_XFADE_DUR", 3)
    channels.get_profile("default")
    before = (Path(config.DATA_DIR) / "channels.json").read_bytes()
    with pytest.raises(channels.ChannelProfileError, match="crossfade"):
        channels.update_profile("default", {"name": "Rejected", "preset": "news",
                                            "defaults": {"broll_interval": 2, "voice_rate": "+9%"}})
    assert not Path(config.SETTINGS_PATH).exists()
    assert config.BROLL_INTERVAL == 10 and config.CHANNEL_PRESET == "general"
    assert (Path(config.DATA_DIR) / "channels.json").read_bytes() == before


def test_legacy_token_is_never_borrowed_by_another_profile(isolated):
    legacy = _token("default", "existing-private-token")
    profile = channels.create_profile("New channel")
    assert channels.public_profile(channels.get_profile("default"))["token_present"]
    shown = channels.public_profile(profile)
    assert not shown["connected"] and not shown["token_present"]
    assert channels.token_path(profile["id"]) != legacy
    with pytest.raises(channels.ChannelProfileError, match="Connect and verify"):
        channels.upload_options(profile["id"])
    assert "existing-private-token" not in json.dumps(channels.list_profiles())


def test_profile_identity_cannot_be_supplied_or_changed_by_api(client):
    response = client.post("/api/channels", json={"name": "Bad", "channel": _channel()}, headers=HEADERS)
    assert response.status_code == 400
    created = client.post("/api/channels", json={"name": "News", "preset": "news"}, headers=HEADERS)
    profile = created.get_json()["profile"]
    assert created.status_code == 201 and created.get_json()["active_profile_id"] == profile["id"]
    response = client.patch("/api/channels/" + profile["id"], json={"channel": _channel()}, headers=HEADERS)
    assert response.status_code == 400
    assert channels.get_profile(profile["id"])["channel"] is None


@pytest.mark.parametrize("defaults", [[], {"token_file": "/tmp/borrow.json"}, {"music_volume": float("nan")},
                                     {"voice_rate": "extremely-fast"}, {"visual_mode": []},
                                     {"duration_mins": True}, {"music_library": "https://example.com/music"},
                                     {"image_sources": [[]]}, {"image_sources": [{"source": "nasa"}]},
                                     {"image_sources": [None]},
                                     {"broll_interval": 2, "broll_xfade": 3}])
def test_invalid_defaults_fail_without_saving(client, defaults):
    response = client.post("/api/channels", json={"name": "Invalid", "defaults": defaults}, headers=HEADERS)
    assert response.status_code == 400
    assert len(channels.list_profiles()["profiles"]) == 1


def test_corrupt_registry_is_not_overwritten(isolated):
    path = Path(config.DATA_DIR) / "channels.json"
    path.parent.mkdir()
    path.write_text('{"profiles": "damaged"}')
    with pytest.raises(channels.ChannelProfileError, match="not been overwritten"):
        channels.list_profiles()
    assert path.read_text() == '{"profiles": "damaged"}'


def test_profile_id_rejects_traversal(isolated):
    for value in ("../../token", "../default", "default/../../", "", None):
        with pytest.raises(channels.ChannelProfileError):
            channels.token_path(value)


def test_active_job_blocks_profile_mutation(client, monkeypatch):
    profile = channels.create_profile("Another")
    monkeypatch.setitem(state, "running", True)
    for endpoint in ("activate", "connect", "refresh", "disconnect"):
        response = client.post(f"/api/channels/{profile['id']}/{endpoint}", json={}, headers=HEADERS)
        assert response.status_code == 409
    response = client.patch(f"/api/channels/{profile['id']}", json={"name": "Changed"}, headers=HEADERS)
    assert response.status_code == 409


class _NowThread:
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def test_connect_fetches_real_identity_and_saves_only_selected_profile_token(client, monkeypatch):
    legacy = _token("default", "do-not-replace")
    profile = channels.create_profile("New news")
    seen = []

    def credentials(**kwargs):
        seen.append(kwargs)
        temporary = Path(kwargs["token_file"])
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text("new-profile-only")
        return object()

    monkeypatch.setattr(youtube, "get_credentials", credentials)
    monkeypatch.setattr(youtube, "fetch_authenticated_channel", lambda **kwargs: _channel())
    monkeypatch.setattr(channels.threading, "Thread", _NowThread)
    response = client.post(f"/api/channels/{profile['id']}/connect", json={}, headers=HEADERS)
    shown = response.get_json()["profile"]
    assert response.status_code == 202 and shown["connected"]
    assert shown["channel"]["id"] == CHANNEL_A and shown["connection"]["status"] == "connected"
    assert channels.token_path(profile["id"]).read_text() == "new-profile-only"
    assert legacy.read_text() == "do-not-replace"
    assert seen[0]["interactive"] and seen[0]["force"]
    assert set(seen[0]["scopes"]) == set(youtube.CHANNEL_SCOPES)
    assert "new-profile-only" not in json.dumps(shown)
    assert not list(channels.token_path(profile["id"]).parent.glob(".connect-*"))


def test_wrong_channel_reconnect_keeps_previous_token_and_identity(client, monkeypatch):
    profile = channels.create_profile("Pinned channel")
    original = _token(profile["id"], "original-token")
    channels._save_channel(profile["id"], _channel())

    def credentials(**kwargs):
        Path(kwargs["token_file"]).write_text("wrong-channel-token")
        return object()

    monkeypatch.setattr(youtube, "get_credentials", credentials)
    monkeypatch.setattr(youtube, "fetch_authenticated_channel", lambda **kwargs: _channel(CHANNEL_B))
    monkeypatch.setattr(channels.threading, "Thread", _NowThread)
    response = client.post(f"/api/channels/{profile['id']}/connect", json={}, headers=HEADERS)
    assert response.get_json()["profile"]["connection"]["status"] == "error"
    assert original.read_text() == "original-token"
    assert channels.get_profile(profile["id"])["channel"]["id"] == CHANNEL_A
    assert not list(original.parent.glob(".connect-*"))


def test_parallel_oauth_connections_are_rejected(client, monkeypatch):
    first = channels.create_profile("One")
    second = channels.create_profile("Two")

    class _WaitingThread(_NowThread):
        def start(self):
            pass

    monkeypatch.setattr(channels.threading, "Thread", _WaitingThread)
    response = client.post(f"/api/channels/{first['id']}/connect", json={}, headers=HEADERS)
    assert response.status_code == 202
    response = client.post(f"/api/channels/{second['id']}/connect", json={}, headers=HEADERS)
    assert response.status_code == 409
    assert channels.is_connecting(first["id"])


def test_refresh_uses_existing_identity_and_rejects_rebinding(client, monkeypatch):
    profile = channels.create_profile("Verified")
    token = _token(profile["id"])
    channels._save_channel(profile["id"], _channel())
    called = []

    def wrong(**kwargs):
        called.append(kwargs)
        return _channel(CHANNEL_B)

    monkeypatch.setattr(youtube, "fetch_authenticated_channel", wrong)
    response = client.post(f"/api/channels/{profile['id']}/refresh", json={}, headers=HEADERS)
    assert response.status_code == 409
    assert called[0]["expected_channel_id"] == CHANNEL_A
    assert called[0]["token_file"] == str(token)
    assert channels.get_profile(profile["id"])["channel"]["id"] == CHANNEL_A


def test_disconnect_is_isolated_and_keeps_channel_binding(client):
    first = channels.create_profile("One")
    second = channels.create_profile("Two")
    token_one = _token(first["id"])
    token_two = _token(second["id"])
    channels._save_channel(first["id"], _channel())
    response = client.post(f"/api/channels/{first['id']}/disconnect", json={}, headers=HEADERS)
    assert response.status_code == 200 and not response.get_json()["profile"]["connected"]
    assert not token_one.exists() and token_two.exists()
    assert channels.get_profile(first["id"])["channel"]["id"] == CHANNEL_A
    with pytest.raises(channels.ChannelProfileError):
        channels.upload_options(first["id"])


def test_connection_errors_do_not_leak_oauth_details(client, monkeypatch):
    profile = channels.create_profile("Private")

    def failure(**kwargs):
        raise RuntimeError("refresh_token=very-secret-value&code=secret-oauth-code")

    monkeypatch.setattr(youtube, "get_credentials", failure)
    monkeypatch.setattr(channels.threading, "Thread", _NowThread)
    response = client.post(f"/api/channels/{profile['id']}/connect", json={}, headers=HEADERS)
    assert response.get_json()["profile"]["connection"]["status"] == "error"
    assert "very-secret" not in response.get_data(as_text=True)
    assert "secret-oauth" not in response.get_data(as_text=True)


def test_public_stats_cache_is_separate_for_every_channel_and_api_key(isolated, monkeypatch):
    from apis import youtube_stats
    calls = []

    def fake_response(url, params):
        calls.append(dict(params))
        return {"items": [{"id": params["id"], "snippet": {"title": params["id"]}, "statistics": {}}]}

    monkeypatch.setattr(youtube_stats, "get_json", fake_response)
    youtube_stats.channel_stats.cache_clear()
    with config.profile_context({"YOUTUBE_API_KEY": "key-one", "YOUTUBE_CHANNEL_ID": CHANNEL_A}):
        assert youtube_stats.channel_stats()["title"] == CHANNEL_A
        assert youtube_stats.channel_stats()["title"] == CHANNEL_A
    with config.profile_context({"YOUTUBE_API_KEY": "key-one", "YOUTUBE_CHANNEL_ID": CHANNEL_B}):
        assert youtube_stats.channel_stats()["title"] == CHANNEL_B
    with config.profile_context({"YOUTUBE_API_KEY": "key-two", "YOUTUBE_CHANNEL_ID": CHANNEL_A}):
        assert youtube_stats.channel_stats()["title"] == CHANNEL_A
    assert len(calls) == 3
    youtube_stats.channel_stats.cache_clear()


def test_unused_active_profile_can_be_deleted_and_returns_to_default(client, monkeypatch):
    from studio import library, schedules
    monkeypatch.setattr(schedules, "list_schedules", lambda profile_id: [])
    monkeypatch.setattr(library, "LIB_DIR", str(Path(config.DATA_DIR) / "empty-library"))
    profile = channels.create_profile("Unused profile")
    response = client.delete(f"/api/channels/{profile['id']}", headers=HEADERS)
    assert response.status_code == 200 and response.get_json() == {"ok": True, "active_profile_id": "default"}
    assert len(channels.list_profiles()["profiles"]) == 1


@pytest.mark.parametrize("usage", ["default", "token", "channel", "schedule", "project", "research",
                                  "workspace", "broken_workspace", "archive", "running"])
def test_used_profile_cannot_be_deleted(client, isolated, monkeypatch, usage):
    from studio import library, schedules
    monkeypatch.setattr(schedules, "list_schedules", lambda profile_id: [{"id": "schedule"}] if usage == "schedule" else [])
    monkeypatch.setattr(library, "LIB_DIR", str(isolated / "library"))
    profile = channels.get_profile("default") if usage == "default" else channels.create_profile("Used profile")
    ident = profile["id"]
    if usage == "token":
        _token(ident)
    elif usage == "channel":
        channels._save_channel(ident, _channel())
    elif usage == "project":
        monkeypatch.setitem(state, "profile_id", ident)
        monkeypatch.setitem(state, "script", {"title": "In progress"})
    elif usage == "research":
        monkeypatch.setitem(state, "profile_id", ident)
        monkeypatch.setitem(state, "script", None)
        monkeypatch.setitem(state, "research", {"video_title": "Research without a script"})
    elif usage in {"workspace", "broken_workspace"}:
        from studio.project_identity import MANIFEST_NAME, write_project_identity
        monkeypatch.setitem(state, "profile_id", None)
        monkeypatch.setitem(state, "script", None)
        monkeypatch.setitem(state, "research", None)
        write_project_identity(config.OUTPUT_DIR, ident, profile["name"])
        if usage == "broken_workspace":
            (Path(config.OUTPUT_DIR) / MANIFEST_NAME).write_text("{broken")
    elif usage == "archive":
        folder = Path(library.LIB_DIR) / "saved-video"
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps({"profile_id": ident}))
    elif usage == "running":
        monkeypatch.setitem(state, "running", True)
    response = client.delete(f"/api/channels/{ident}", headers=HEADERS)
    assert response.status_code == 409
    assert channels.get_profile(ident)["id"] == ident


def test_another_profiles_cached_project_does_not_block_deleting_an_unused_profile(client, monkeypatch):
    from studio import library
    from studio.project_identity import write_project_identity
    monkeypatch.setattr(library, "LIB_DIR", str(Path(config.DATA_DIR) / "empty-library"))
    owner = channels.create_profile("Owner")
    unused = channels.create_profile("Unused")
    write_project_identity(config.OUTPUT_DIR, owner["id"], owner["name"])
    response = client.delete(f"/api/channels/{unused['id']}", headers=HEADERS)
    assert response.status_code == 200
    assert channels.get_profile(owner["id"])["name"] == "Owner"
