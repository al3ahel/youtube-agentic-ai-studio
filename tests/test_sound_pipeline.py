"""Real sound preparation, transactional reuse and portable project ownership."""
from contextlib import contextmanager
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit
import wave

import numpy as np
from PIL import Image
import pytest

import config
from agents import audio_director, gemini_client, visual_director
from apis import audio_sources
from studio import channels, create_app, library, runner
from studio.description import ensure_audio_credits, load_audio_credits, load_sound_report
from studio.profile_context import capture_profile_settings
from studio.state import fresh_project_state, state, stop_event
from video.timeline import audio_signature, build_timeline, probe_duration, script_signature


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="Actual FFmpeg audio required")
RATE = 48000


def wave_bytes(frequency, seconds=4, gain=.5):
    output = io.BytesIO()
    times = np.arange(round(RATE * seconds)) / RATE
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes((gain * np.sin(2 * np.pi * frequency * times) * 32767).astype("<i2").tobytes())
    return output.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    previous = deepcopy(state)
    state.clear()
    state.update(fresh_project_state())
    stop_event.clear()
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(output))
    # This fixture verifies the Commons transport path; bundled recordings have
    # their own real-media regression, without fabricated transport responses.
    monkeypatch.setattr(audio_sources, "_BUNDLED_ROOT", tmp_path / "no-bundled-catalog")
    monkeypatch.setattr(config, "IMAGES_DIR", str(output / "images"))
    monkeypatch.setattr(library, "LIB_DIR", str(output / "library"))
    monkeypatch.setattr(config, "VIDEO_WIDTH", 320)
    monkeypatch.setattr(config, "VIDEO_HEIGHT", 180)
    monkeypatch.setattr(config, "VIDEO_FPS", 2)
    monkeypatch.setattr(config, "VISUAL_REVIEW_ENABLED", False)
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", False)
    monkeypatch.setattr(config, "MOTION_WORKERS", 1)
    owner = channels.create_profile("The project owner", "general", {
        "sound_mode": "auto", "sound_intensity": "low", "audio_mastering": True,
        "visual_mode": "motion", "visual_ai_enabled": False, "visual_review_enabled": False,
        "motion_workers": 1, "credit_yaas": False})
    other = channels.create_profile("Another active channel", "asmr")
    assert channels.active_profile_id() == other["id"]
    script = {"title": "Evidence and measurement", "sections": [
        {"id": "opening", "title": "A question", "narration": "We begin with a clear question about evidence."},
        {"id": "detail", "title": "A measurement", "narration": "We measure the evidence and compare the result."}]}
    voice = output / "narration.wav"
    voice.write_bytes(wave_bytes(400, 16, .12))
    (output / "script.json").write_text(json.dumps(script), encoding="utf-8")
    (output / "narration.timings.json").write_text(json.dumps({
        "version": 1, "script_signature": script_signature(script), "audio_sha256": audio_signature(voice),
        "duration": 16, "sample_rate": RATE, "sections": [
            {"id": row["id"], "start": index * 8, "duration": 8, "caption_timing": "estimated"}
            for index, row in enumerate(script["sections"])], "words": []}), encoding="utf-8")
    timeline = build_timeline(script, str(voice), str(output))
    thumbnail = output / "custom.jpg"
    Image.new("RGB", (32, 18), "navy").save(thumbnail)
    state.update(script=script, research={}, timeline=timeline, audio_path=str(voice),
                 profile_id=owner["id"], profile_name=owner["name"], description="Owner's edited prose.",
                 visual_mode="motion", image_map={}, thumb_path=str(thumbnail))
    from studio.project_identity import write_project_identity
    write_project_identity(output, owner["id"], owner["name"])
    model_requests, network_requests = [], []
    intent = {"summary": "Quiet music and a purposeful measurement cue.",
              "music": {"use": True, "query": "quiet instrumental", "reason": "Supports the explanation."},
              "ambience": {"use": False}, "events": [
                  {"section_id": "detail", "query": "paper movement", "label": "Measurement", "reason": "A new detail."}]}
    def answer(prompt, **kwargs):
        model_requests.append(prompt)
        return json.dumps(intent)
    monkeypatch.setattr(gemini_client, "generate", answer)
    sounds = {"music.wav": wave_bytes(700), "effect.wav": wave_bytes(1300)}

    class Response:
        status = 200
        def __init__(self, data, mime):
            self.stream, self.data, self.mime = io.BytesIO(data), data, mime
        def getheader(self, name):
            return {"Content-Length": str(len(self.data)), "Content-Type": self.mime}.get(name)
        def read(self, size):
            return self.stream.read(size)

    @contextmanager
    def transport(url, **kwargs):
        network_requests.append(url)
        if urlsplit(url).hostname == "commons.wikimedia.org":
            query = parse_qs(urlsplit(url).query)["gsrsearch"][0]
            filename = "music.wav" if "instrumental" in query else "effect.wav"
            page = {"pageid": 101 if filename == "music.wav" else 102, "title": "File:" + filename,
                    "index": 1, "imageinfo": [{"mime": "audio/wav", "mediatype": "AUDIO",
                    "size": len(sounds[filename]), "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/" + filename,
                    "extmetadata": {"Artist": {"value": "Fixture composer"},
                        "ImageDescription": {"value": "Instrumental piano music" if filename == "music.wav" else "Paper movement sound effect"},
                        "LicenseShortName": {"value": "CC BY 4.0"},
                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
                        "Attribution": {"value": "Additional fixture attribution."}}}]}
            yield Response(json.dumps({"query": {"pages": [page]}}).encode(), "application/json")
        else:
            yield Response(sounds[Path(urlsplit(url).path).name], "audio/wav")
    monkeypatch.setattr(audio_sources, "_response", transport)
    yield {"output": output, "voice": voice, "script": script, "timeline": timeline, "owner": owner,
           "other": other, "model_requests": model_requests, "network_requests": network_requests}
    state.clear()
    state.update(previous)
    stop_event.clear()


def mix(project):
    with config.profile_context(capture_profile_settings(project["owner"]["id"])):
        return runner._mix_project_audio(project["voice"], project["timeline"], project["output"], project["owner"]["id"])


def test_automatic_sound_is_owned_measured_and_committed_with_actual_credits(project):
    output, voice = project["output"], project["voice"]
    original = voice.read_bytes()
    final = Path(mix(project))
    manifest = json.loads((output / "render_audio.json").read_text())
    assert final.parent == output / "soundtracks" and final.is_file()
    assert manifest["version"] == 2 and manifest["path"] == final.relative_to(output).as_posix()
    assert manifest["audio_sha256"] == audio_signature(voice)
    assert manifest["soundtrack_sha256"] == audio_signature(final)
    assert manifest["sound_design"]["profile_id"] == project["owner"]["id"]
    assert manifest["sound_design"]["events"][0]["start"] == 8
    assert [item["kind"] for item in manifest["credits"]] == ["music", "sfx"]
    assert len(load_audio_credits(project["timeline"])) == 2
    assert load_sound_report("audio_master.json")["status"] in {"applied", "target_shortfall"}
    assert all("Additional fixture attribution." in item["attribution"] for item in manifest["credits"])
    assert "The project owner" in project["model_requests"][0]
    assert "Another active channel" not in project["model_requests"][0]
    assert channels.active_profile_id() == project["other"]["id"]
    assert (Path(config.DATA_DIR) / "audio-auto" / project["owner"]["id"]).is_dir()
    assert not (Path(config.DATA_DIR) / "audio-auto" / project["other"]["id"]).exists()
    raw = subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-i", str(final),
                          "-f", "f32le", "-ac", "2", "-ar", str(RATE), "-"], capture_output=True, check=True, timeout=30).stdout
    samples = np.frombuffer(raw, dtype="<f4").reshape(-1, 2)[:, 0]
    assert len(samples) == 16 * RATE and np.isfinite(samples).all()
    def energy(frequency, start):
        part = samples[round(start * RATE):round((start + .2) * RATE)]
        return abs(np.mean(part * np.exp(-2j * np.pi * frequency * np.arange(len(part)) / RATE)))
    assert energy(700, 4) > .001 and energy(1300, 8.4) > .001
    assert energy(1300, 6) < .0001 and energy(1300, 10) < .0001
    assert voice.read_bytes() == original and not list(output.glob(".soundwork-*"))


def test_cancelled_regeneration_preserves_previous_audio_and_atomic_receipt(project, monkeypatch):
    from video import audio_master
    final = Path(mix(project))
    output = project["output"]
    retained = {path: path.read_bytes() for path in (final, project["voice"], output / "render_audio.json",
                output / "audio_credits.json", output / "sound_design.json", output / "audio_master.json")}
    actual = audio_master.master_soundtrack
    def stop_after_real_master(*args, **kwargs):
        result = actual(*args, **kwargs)
        stop_event.set()
        return result
    monkeypatch.setattr(audio_master, "master_soundtrack", stop_after_real_master)
    with pytest.raises(InterruptedError):
        mix(project)
    assert all(path.read_bytes() == data for path, data in retained.items())
    assert list((output / "soundtracks").iterdir()) == [final]
    assert not list(output.glob(".soundwork-*"))


def test_unavailable_catalogue_has_honest_voice_only_design(project, monkeypatch):
    @contextmanager
    def unavailable(*args, **kwargs):
        raise audio_sources.AudioSourceError("catalogue_unavailable", "Fixture catalogue is unavailable.")
        yield
    monkeypatch.setattr(audio_sources, "_response", unavailable)
    final = Path(mix(project))
    manifest = json.loads((project["output"] / "render_audio.json").read_text())
    assert manifest["credits"] == [] and load_audio_credits() == []
    design = manifest["sound_design"]
    assert design["music"] is None and design["ambience"] is None and design["events"] == []
    assert design["summary"].startswith("Narration only") and design["warnings"]
    assert probe_duration(str(final)) == 16 and project["voice"].is_file()


@pytest.mark.skipif(not shutil.which("node") or not os.environ.get("HYPERFRAMES_BROWSER_PATH")
                    or not Path(os.environ.get("HYPERFRAMES_BROWSER_PATH", "")).is_file()
                    or not (Path(config.ROOT_DIR) / "renderers/hyperframes/node_modules").is_dir(),
                    reason="Explicit local HYPERFRAMES_BROWSER_PATH required; never download a test browser")
def test_video_only_reuses_committed_audio_and_archive_reopens_matching_reports(project, monkeypatch):
    from video import narrator
    output = project["output"]
    final = Path(mix(project))
    committed = (output / "render_audio.json").read_bytes()
    checksum = audio_signature(final)
    plan = visual_director.plan_scenes(project["script"], project["timeline"], output_dir=str(output), use_ai=False,
                                        use_project_photos=False)
    state.update(visual_plan=plan, render_audio_path=str(final))
    state["description"] = ensure_audio_credits(state["description"], project["timeline"])
    original_prose = state["description"]
    def forbidden(*args, **kwargs):
        pytest.fail("Video-only reuse must not call a model, download audio or synthesize narration")
    monkeypatch.setattr(gemini_client, "generate", forbidden)
    monkeypatch.setattr(audio_sources, "_response", forbidden)
    monkeypatch.setattr(audio_director, "direct_audio", forbidden)
    monkeypatch.setattr(narrator, "generate_narration", forbidden)
    runner.run_pipeline_thread(["video"], visual_mode="motion", preview_only=True,
                               profile_id=project["owner"]["id"], use_project_photos=False)
    assert state["error"] is None, state["error"]
    assert Path(state["preview_path"]).stat().st_size > 1000
    assert state["visual_plan"] == plan and state["render_audio_path"] == str(final)
    assert (output / "render_audio.json").read_bytes() == committed
    assert audio_signature(final) == checksum and state["description"] == original_prose
    assert abs(probe_duration(state["preview_path"]) - 16) < .1
    project_id = library.archive_project(allow_draft=True)
    assert project_id
    client = create_app().test_client()
    response = client.post(f"/api/library/{project_id}/load", json={})
    assert response.status_code == 200 and response.json["ok"], response.json
    restored = json.loads((output / "render_audio.json").read_text())
    assert restored["version"] == 2 and restored["path"] == "render_audio.wav"
    assert restored["soundtrack_sha256"] == checksum == audio_signature(state["render_audio_path"])
    assert all(restored[key]["path"] == restored["path"] for key in ("sound_design", "audio_master"))
    assert len(load_audio_credits(state["timeline"])) == 2
    assert load_sound_report("sound_design.json")["profile_id"] == project["owner"]["id"]
    assert state["profile_id"] == project["owner"]["id"] and state["description"] == original_prose
