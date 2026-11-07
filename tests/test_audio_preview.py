from copy import deepcopy
from pathlib import Path

import pytest

import config
from studio import audio, create_app
from studio.state import state, fresh_project_state


@pytest.fixture
def client():
    previous = deepcopy(state)
    state.clear()
    state.update(fresh_project_state())
    yield create_app().test_client()
    state.clear()
    state.update(previous)


def test_audition_uses_unsaved_voice_and_returns_only_local_file(client, monkeypatch):
    calls = []
    async def record(path, settings):
        assert state["visual_editing"]
        calls.append(settings)
        path.write_bytes(b"sample audio" * 200)
    monkeypatch.setattr(audio, "_record", record)
    response = client.post("/api/voice-preview", json={"voice_id": "en-US-BrianNeural", "voice_rate": "-15%", "voice_pitch": "+0Hz"})
    assert response.status_code == 200
    assert calls == [{"voice_id": "en-US-BrianNeural", "voice_rate": "-15%", "voice_pitch": "+0Hz"}]
    assert not state["visual_editing"]
    assert response.json["url"].startswith("/api/voice-preview/")
    clip = client.get(response.json["url"])
    assert clip.status_code == 200 and clip.mimetype == "audio/mpeg"
    clip.close()


def test_provider_failure_removes_partial_file_and_releases_job(client, monkeypatch):
    async def fail(path, settings):
        path.write_bytes(b"partial")
        raise RuntimeError("certificate verify failed")
    monkeypatch.setattr(audio, "_record", fail)
    response = client.post("/api/voice-preview", json={})
    assert response.status_code == 502 and "secure connection" in response.json["error"]
    assert not state["visual_editing"]
    assert not list((Path(config.DATA_DIR) / "voice-previews").glob("*.mp3"))


def test_busy_or_invalid_auditions_do_not_contact_provider(client, monkeypatch):
    async def forbidden(*args):
        pytest.fail("No voice request should be made")
    monkeypatch.setattr(audio, "_record", forbidden)
    assert client.post("/api/voice-preview", json={"voice_pitch": "-100Hz"}).status_code == 400
    state["running"] = True
    assert client.post("/api/voice-preview", json={}).status_code == 409
    assert client.get("/api/voice-preview/not-a-preview").status_code == 404
