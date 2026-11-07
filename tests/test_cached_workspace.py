"""Restart-level API ownership and persistent custom-photo workspace checks."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest

import config
from studio import channels, create_app, pipeline, runner
from studio.project_identity import write_project_identity
from studio.state import fresh_project_state, state, stop_event


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    previous = deepcopy(state)
    previously_stopped = stop_event.is_set()
    state.clear()
    state.update(fresh_project_state())
    stop_event.clear()
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(config, "IMAGES_DIR", str(output / "images"))
    monkeypatch.setattr(config, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    try:
        yield output
    finally:
        state.clear()
        state.update(previous)
        if previously_stopped:
            stop_event.set()
        else:
            stop_event.clear()


def photo(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 16), "#587b39").save(path)
    return path


def test_partial_run_after_restart_checks_saved_owner_before_starting_worker(workspace, monkeypatch):
    owner = channels.create_profile("News owner", "news")
    other = channels.create_profile("Calm active channel", "asmr")
    assert channels.active_profile_id() == other["id"]
    write_project_identity(workspace, owner["id"], owner["name"])
    (workspace / "script.json").write_text(json.dumps({
        "title": "Saved news project",
        "sections": [{"id": 8, "narration": "This recording belongs to the news channel."}],
    }), encoding="utf-8")

    # Restart discards process memory, but keeps files and the other active profile.
    state.clear()
    state.update(fresh_project_state())
    assert state["profile_id"] is None and state["script"] is None
    empty_state = deepcopy(state)
    saved_identity = (workspace / "project_profile.json").read_bytes()
    worker = Mock(name="pipeline_worker")
    thread_factory = Mock(name="Thread", return_value=worker)
    monkeypatch.setattr(pipeline.threading, "Thread", thread_factory)
    app = create_app()
    app.testing = True
    client = app.test_client()
    body = {"steps": ["visuals", "video"], "visual_mode": "motion", "preview_only": True}

    # Omitting the profile uses the active channel; an explicit wrong owner is
    # rejected too. Neither case may reserve a run or even construct a worker.
    for request_body in (body, {**body, "profile_id": other["id"]}):
        response = client.post("/api/run", json=request_body)
        assert response.status_code == 409
        assert response.json["ok"] is False
        assert "another channel profile" in response.json["error"]
        thread_factory.assert_not_called()
        worker.start.assert_not_called()
        assert state == empty_state
        assert (workspace / "project_profile.json").read_bytes() == saved_identity

    # An explicit matching owner can reuse this project even while Home remains
    # on the other profile. Capture the dispatch without executing any provider.
    response = client.post("/api/run", json={**body, "profile_id": owner["id"]})
    assert response.status_code == 200
    assert response.json == {"ok": True}
    thread_factory.assert_called_once()
    worker.start.assert_called_once_with()
    options = thread_factory.call_args.kwargs
    assert options["target"] is pipeline.run_pipeline_thread
    assert options["args"][0] == ["visuals", "video"]
    assert options["kwargs"]["profile_id"] == owner["id"]
    assert options["kwargs"]["profile_values"]["VOICE_RATE"] == "+0%"
    assert options["kwargs"]["preview_only"] is True
    assert state["job_profile_id"] == owner["id"] and state["running"] is True
    assert channels.active_profile_id() == other["id"]
    assert (workspace / "project_profile.json").read_bytes() == saved_identity


def test_arbitrary_png_section_mapping_survives_loss_of_process_state(workspace):
    image = photo(workspace / "images" / "a custom garden photo (copy 7).PNG")
    original = image.read_bytes()
    state["image_map"] = {8: [str(image)], "quiet-garden": [str(image)]}
    runner._save_image_map(workspace, state["image_map"])

    saved = json.loads((workspace / "image_map.json").read_text(encoding="utf-8"))
    relative = "images/a custom garden photo (copy 7).PNG"
    assert saved == {"8": [relative], "quiet-garden": [relative]}
    assert all(not Path(value).is_absolute() for paths in saved.values() for value in paths)

    state.clear()
    state.update(fresh_project_state())
    loaded = runner._load_image_map(workspace)
    assert loaded == {"8": [str(image.resolve())], "quiet-garden": [str(image.resolve())]}
    assert Path(loaded["8"][0]).read_bytes() == original


def test_image_map_drops_missing_and_escaping_paths_on_save_and_load(workspace):
    valid = photo(workspace / "images" / "custom.png")
    outside = photo(workspace.parent / "outside.png")
    missing = workspace / "images" / "missing.png"
    runner._save_image_map(workspace, {
        "scene": [str(valid), str(outside), str(missing)],
    })
    manifest = workspace / "image_map.json"
    assert json.loads(manifest.read_text(encoding="utf-8")) == {"scene": ["images/custom.png"]}

    # Treat a persisted map as untrusted: neither parent traversal nor an
    # absolute path may introduce an existing image from outside the project.
    non_image = workspace / "images" / "notes.txt"
    non_image.write_text("Not a picture", encoding="utf-8")
    manifest.write_text(json.dumps({
        "scene": [
            "images/custom.png", "../outside.png", "images/../../outside.png",
            str(outside.resolve()), "images/missing.png", "images/notes.txt", None, 42,
        ],
        "invalid-section": "images/custom.png",
    }), encoding="utf-8")
    assert runner._load_image_map(workspace) == {"scene": [str(valid.resolve())]}
    assert outside.is_file() and not missing.exists()


@pytest.mark.parametrize("changed_source", ["script", "audio"])
def test_source_invalidation_removes_persisted_photo_map(workspace, changed_source):
    image = photo(workspace / "images" / "custom cached photo.png")
    state["image_map"] = {"scene": [str(image)]}
    runner._save_image_map(workspace, state["image_map"])
    assert (workspace / "image_map.json").is_file()

    runner._invalidate(workspace, **{changed_source: True})

    assert state["image_map"] == {}
    assert not (workspace / "image_map.json").exists()
    assert runner._load_image_map(workspace) == {}
