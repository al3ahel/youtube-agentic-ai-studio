"""Cached media cannot change publishing profiles when process memory resets."""
import json

import pytest

from studio import project_identity as identity


PROFILE_A = "a" * 32
PROFILE_B = "b" * 32


@pytest.mark.parametrize("cache_name", ["script.json", "research.json"])
def test_restart_uses_manifest_and_ignores_empty_stale_state(tmp_path, cache_name):
    (tmp_path / cache_name).write_text("{}", encoding="utf-8")
    identity.write_project_identity(tmp_path, PROFILE_A, "News")
    restarted = {"profile_id": PROFILE_B, "script": None, "research": {}}
    identity.validate_cached_profile(PROFILE_A, ["video"], tmp_path, restarted)
    with pytest.raises(ValueError, match="another channel"):
        identity.validate_cached_profile(PROFILE_B, ["video"], tmp_path, restarted)
    identity.validate_cached_profile(PROFILE_B, ["research", "video"], tmp_path, restarted)


@pytest.mark.parametrize("cache_name", ["script.json", "research.json"])
def test_legacy_cache_without_manifest_belongs_only_to_default(tmp_path, cache_name):
    (tmp_path / cache_name).write_text("{}", encoding="utf-8")
    identity.validate_cached_profile("default", ["script", "video"], tmp_path, {})
    with pytest.raises(ValueError, match="another channel"):
        identity.validate_cached_profile(PROFILE_A, ["script", "video"], tmp_path, {})


@pytest.mark.parametrize("live_key", ["script", "research"])
def test_live_project_owner_takes_precedence_over_an_older_valid_manifest(tmp_path, live_key):
    identity.write_project_identity(tmp_path, PROFILE_B, "Older project")
    live = {"profile_id": PROFILE_A, live_key: {"title": "Open project"}}
    identity.validate_cached_profile(PROFILE_A, ["narration"], tmp_path, live)
    with pytest.raises(ValueError, match="another channel"):
        identity.validate_cached_profile(PROFILE_B, ["narration"], tmp_path, live)


def test_live_project_without_owner_uses_saved_identity(tmp_path):
    identity.write_project_identity(tmp_path, PROFILE_A, "News")
    live = {"profile_id": None, "script": {"title": "Loaded"}}
    identity.validate_cached_profile(PROFILE_A, ["video"], tmp_path, live)
    with pytest.raises(ValueError, match="another channel"):
        identity.validate_cached_profile("default", ["video"], tmp_path, live)


def test_empty_workspace_does_not_adopt_an_unrelated_stale_state_owner(tmp_path):
    identity.validate_cached_profile(PROFILE_A, ["video"], tmp_path, {"profile_id": PROFILE_B})
    assert list(tmp_path.iterdir()) == []


def test_manifest_alone_still_protects_orphaned_cached_media(tmp_path):
    identity.write_project_identity(tmp_path, PROFILE_A, "News")
    with pytest.raises(ValueError, match="another channel"):
        identity.validate_cached_profile(PROFILE_B, ["video"], tmp_path, {})


@pytest.mark.parametrize("content", [
    "{broken", "[]", '"default"', "{}",
    json.dumps({"profile_id": "../other", "profile_name": "Wrong"}),
    json.dumps({"profile_id": PROFILE_A, "profile_name": []}),
    " " * 8193,
])
def test_corrupt_manifest_blocks_partial_runs_but_allows_fresh_research(tmp_path, content):
    manifest = tmp_path / identity.MANIFEST_NAME
    manifest.write_text(content, encoding="utf-8")
    for live in ({}, {"profile_id": PROFILE_A, "script": {"title": "Live"}}):
        with pytest.raises(ValueError, match="identity is unreadable"):
            identity.validate_cached_profile(PROFILE_A, ["video"], tmp_path, live)
        identity.validate_cached_profile(PROFILE_A, ["research", "script"], tmp_path, live)
    assert manifest.read_text(encoding="utf-8") == content


def test_broken_manifest_symlink_does_not_become_a_legacy_project(tmp_path):
    manifest = tmp_path / identity.MANIFEST_NAME
    try:
        manifest.symlink_to(tmp_path / "missing.json")
    except (OSError, NotImplementedError):
        pytest.skip("This platform does not allow creating symlinks.")
    with pytest.raises(ValueError, match="identity is unreadable"):
        identity.validate_cached_profile("default", ["video"], tmp_path, {})


def test_atomic_writer_preserves_previous_owner_on_replace_failure(tmp_path, monkeypatch):
    identity.write_project_identity(tmp_path, PROFILE_A, "News · اليوم")
    manifest = tmp_path / identity.MANIFEST_NAME
    before = manifest.read_bytes()
    assert json.loads(before) == {"profile_id": PROFILE_A, "profile_name": "News · اليوم"}
    def fail_replace(source, target):
        raise OSError("Disk unavailable")
    monkeypatch.setattr(identity.os, "replace", fail_replace)
    with pytest.raises(OSError, match="Disk unavailable"):
        identity.write_project_identity(tmp_path, PROFILE_B, "Calm")
    assert manifest.read_bytes() == before
    assert list(tmp_path.glob(".project-profile-*")) == []


@pytest.mark.parametrize("profile_id", ["", "other", "../../escape", None, 5])
def test_writer_rejects_invalid_owner_before_creating_any_file(tmp_path, profile_id):
    with pytest.raises(ValueError, match="invalid channel profile identity"):
        identity.write_project_identity(tmp_path / "output", profile_id, "Wrong")
    assert not (tmp_path / "output").exists()
