"""User settings live in settings.json; config.py only holds defaults."""
import json
import os
import shutil
import subprocess
import sys

import pytest

import config
import gui
from studio import settings as studio_settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = {"Host": "localhost:7070"}


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_PATH", str(path))
    return path


@pytest.fixture
def restore_config():
    """Endpoints change config attributes in memory; put them back afterwards."""
    saved = {k: getattr(config, k) for k in config.USER_SETTINGS}
    yield
    for k, v in saved.items():
        setattr(config, k, v)


# ── load / save ───────────────────────────────────────────────────────────────

def test_missing_file_means_no_overrides(settings_file):
    assert config.load_settings() == {}


def test_unknown_keys_wrong_types_and_secrets_are_ignored(settings_file):
    settings_file.write_text(json.dumps({
        "CHANNEL_NAME": "Mine", "MUSIC_VOLUME": 0.5,
        "MUSIC_ENABLED": "yes",          # wrong type
        "GEMINI_API_KEY": "secret",      # secrets never come from here
        "OUTPUT_DIR": "/tmp/x",          # not a user setting
        "NOPE": 1,
    }), encoding="utf-8")
    assert config.load_settings() == {"CHANNEL_NAME": "Mine", "MUSIC_VOLUME": 0.5}


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_broken_file_is_ignored_not_fatal(settings_file, content):
    settings_file.write_text(content, encoding="utf-8")
    assert config.load_settings() == {}


def test_save_keeps_only_values_that_differ_from_defaults(settings_file):
    config.save_settings({"CHANNEL_NAME": "Mine", "VOICE_ID": config.DEFAULTS["VOICE_ID"]})
    config.save_settings({"MUSIC_VOLUME": 0.3})                       # merges
    assert json.loads(settings_file.read_text(encoding="utf-8")) == {"CHANNEL_NAME": "Mine", "MUSIC_VOLUME": 0.3}
    config.save_settings({"CHANNEL_NAME": config.DEFAULTS["CHANNEL_NAME"]})   # back to default
    assert json.loads(settings_file.read_text(encoding="utf-8")) == {"MUSIC_VOLUME": 0.3}
    assert not os.path.exists(str(settings_file) + ".tmp")


def test_save_rejects_unknown_or_mistyped_values(settings_file):
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        config.save_settings({"GEMINI_API_KEY": "x"})
    with pytest.raises(ValueError, match="MUSIC_VOLUME"):
        config.save_settings({"MUSIC_VOLUME": "loud"})
    assert not settings_file.exists()


def test_saved_settings_override_defaults_at_import(tmp_path):
    shutil.copy(os.path.join(ROOT, "config.py"), tmp_path / "config.py")
    (tmp_path / "settings.json").write_text(json.dumps(
        {"CHANNEL_NAME": "From JSON", "IMAGE_SOURCES": ["nasa"], "KB_ZOOM_END": 1.2}), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, "-c",
         "import json, config; print(json.dumps([config.CHANNEL_NAME, config.IMAGE_SOURCES,"
         " config.KB_ZOOM_END, config.DEFAULTS['CHANNEL_NAME']]))"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONPATH": str(tmp_path)})
    assert json.loads(out.stdout) == ["From JSON", ["nasa"], 1.2, "My AI Channel"]


# ── the Settings endpoint ─────────────────────────────────────────────────────

def test_settings_page_writes_settings_json_not_config_py(settings_file, monkeypatch, restore_config):
    monkeypatch.setattr(config, "save_env", lambda values: None)
    cfg = os.path.join(ROOT, "config.py")
    before = open(cfg, encoding="utf-8").read()

    r = gui.app.test_client().post("/api/save-settings", headers=LOCAL, json={
        "channel_name": "Space Facts", "music_volume": "0.25", "show_brand": False,
        "image_sources": "nasa, openverse",
        "channel_desc": '\n  Space videos with """quotes""" and a # hash\n',
    })
    assert r.get_json() == {"ok": True, "profile_id": "default"}
    assert open(cfg, encoding="utf-8").read() == before
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved == {
        "CHANNEL_NAME": "Space Facts", "MUSIC_VOLUME": 0.25, "SHOW_CHANNEL_BRAND": False,
        "IMAGE_SOURCES": ["nasa", "openverse"],
        "CHANNEL_DESCRIPTION": 'Space videos with """quotes""" and a # hash',
    }
    assert config.CHANNEL_NAME == "Space Facts"
    got = gui.app.test_client().get("/api/settings", headers=LOCAL).get_json()
    assert got["channel_name"] == "Space Facts" and got["music_volume"] == 0.25


def test_api_keys_still_go_to_env_only(settings_file, monkeypatch, restore_config):
    env = {}
    monkeypatch.setattr(config, "save_env", env.update)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "", raising=False)
    r = gui.app.test_client().post("/api/save-settings", headers=LOCAL,
                                   json={"gemini_key": "AIza-test"})
    assert r.get_json() == {"ok": True, "profile_id": "default"}
    assert env == {"GEMINI_API_KEY": "AIza-test"}
    assert not settings_file.exists()


def test_settings_save_keeps_the_profile_loaded_before_another_tab_switches(settings_file, monkeypatch):
    from copy import deepcopy
    from studio import channels
    from studio.state import state

    for key in ("running", "uploading", "visual_editing"):
        monkeypatch.setitem(state, key, False)
    monkeypatch.setattr(config, "YOUTUBE_CHANNEL_ID", "@legacy-channel")
    written_env = {}
    monkeypatch.setattr(config, "save_env", written_env.update)
    news = channels.create_profile("Morning news", "news")
    calm = channels.create_profile("Calm evenings", "asmr")
    client = gui.app.test_client()
    loaded = client.get(f"/api/settings?profile_id={news['id']}", headers=LOCAL)
    assert loaded.status_code == 200 and loaded.json["profile_id"] == news["id"]
    calm_before = deepcopy(channels.get_profile(calm["id"])["defaults"])
    assert channels.active_profile_id() == calm["id"]

    response = client.post(f"/api/save-settings?profile_id={loaded.json['profile_id']}", headers=LOCAL, json={
        "voice_rate": "+5%", "channel_name": "Updated news branding",
        "youtube_channel": "@unverified-news-channel",
    })
    assert response.status_code == 200
    assert response.json == {"ok": True, "profile_id": news["id"]}
    saved_news = channels.get_profile(news["id"])["defaults"]
    assert saved_news["voice_rate"] == "+5%"
    assert saved_news["channel_name"] == "Updated news branding"
    assert channels.get_profile(calm["id"])["defaults"] == calm_before
    assert channels.active_profile_id() == calm["id"]
    assert config.YOUTUBE_CHANNEL_ID == "@legacy-channel"
    assert not written_env and not settings_file.exists()


# ── importing an old config.py ────────────────────────────────────────────────

OLD_CONFIG = '''
open("pwned.txt", "w").write("ran")      # must never execute
CHANNEL_NAME = 'Old Channel'
CHANNEL_DESCRIPTION = """
My old description.
"""
MUSIC_VOLUME = 0.2
VOICE_ID = "en-US-AndrewNeural"          # same as the default → not imported
IMAGE_SOURCES = ['nasa', 'pollinations']
OVERLAY_OPACITY = 0.5 + 0.1              # not a plain literal → skipped
GEMINI_API_KEY = "never-imported"
SHOW_CHANNEL_BRAND = "nope"              # wrong type → skipped
'''


def test_import_legacy_config_reads_values_without_running_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old = tmp_path / "old_config.py"
    old.write_text(OLD_CONFIG, encoding="utf-8")
    assert config.import_legacy_config(str(old)) == {
        "CHANNEL_NAME": "Old Channel",
        "CHANNEL_DESCRIPTION": "My old description.",
        "MUSIC_VOLUME": 0.2,
        "IMAGE_SOURCES": ["nasa", "pollinations"],
    }
    assert not (tmp_path / "pwned.txt").exists()


def test_import_settings_command(tmp_path, settings_file, capsys):
    old = tmp_path / "old_config.py"
    old.write_text(OLD_CONFIG, encoding="utf-8")
    studio_settings.import_settings(str(old))
    assert json.loads(settings_file.read_text(encoding="utf-8"))["CHANNEL_NAME"] == "Old Channel"
    assert "Imported 4 setting(s)" in capsys.readouterr().out
