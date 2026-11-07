"""An upgrade preserves deliberate old selections without trapping Automatic."""
import json

import config
from studio import channels


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
