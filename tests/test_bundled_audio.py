"""Shipped licensed recordings produce real, attributed, audible sound offline."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import wave

import numpy as np
import pytest

import config
from agents import audio_director, gemini_client
from apis import audio_sources
from studio import channels, library, runner
from studio.description import ensure_audio_credits, load_audio_credits
from studio.profile_context import capture_profile_settings
from studio.state import fresh_project_state, state, stop_event
from video.timeline import audio_signature, script_signature


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="Real FFmpeg/ffprobe required")
RATE = 48000
REPORTED_QUERY = "calm ambient instrumental music without vocals, low tempo, suitable for documentary narration"
BUNDLED = Path(__file__).resolve().parents[1] / "assets" / "audio" / "bundled"


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    assert (BUNDLED / "catalog.json").is_file(), "The automatic licensed collection must ship with the application"
    isolated = tmp_path / "bundled"
    shutil.copytree(BUNDLED, isolated)
    monkeypatch.setattr(audio_sources, "_BUNDLED_ROOT", isolated)
    monkeypatch.setattr(audio_sources, "_response", lambda *a, **k: pytest.fail("Matching shipped recordings need no external request"))
    return isolated


def _samples(path):
    data = subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-i", str(path),
                           "-f", "f32le", "-ac", "1", "-ar", str(RATE), "-"],
                          capture_output=True, check=True, timeout=30).stdout
    return np.frombuffer(data, dtype="<f4")


@pytest.mark.parametrize("query,kind,minimum", [(REPORTED_QUERY, "music", 3),
    ("soft paper turn", "sfx", .05), ("small quiet whoosh", "sfx", .05)])
def test_real_licensed_family_is_available_without_search_and_has_audible_samples(catalog, query, kind, minimum):
    asset = audio_sources.acquire_audio(query, kind=kind, profile_id="default", mood="subtle", min_duration=minimum, max_duration=600)
    assert asset and asset["provider"] == "bundled" and asset["selection_source"] == "bundled_catalog"
    assert asset["license"] in {"CC0 1.0", "CC BY 4.0"}
    assert all(asset[field] in asset["attribution"] for field in ("creator", "license_url"))
    assert asset["source_url"].startswith("https://")  # Added separately to the complete description block below.
    assert hashlib.sha256(Path(asset["path"]).read_bytes()).hexdigest() == asset["sha256"]
    samples = _samples(asset["path"])
    assert np.isfinite(samples).all() and np.max(np.abs(samples)) > .01
    assert np.sqrt(np.mean(samples.astype(float) ** 2)) > .001, "A declared applied recording must contain actual sound"
    assert audio_sources.get_status()["reason"] == "bundled"
    assert audio_sources.acquire_audio(query, kind=kind, profile_id="default", mood="subtle", min_duration=minimum, max_duration=600) == asset
    assert audio_sources.get_status()["reason"] == "cached"


@pytest.mark.parametrize("query,kind,minimum", [
    ("energetic heavy metal guitar music", "music", 3), ("solo banjo instrumental music", "music", 3),
    ("rain recording", "sfx", 8), ("metal page turn", "sfx", .05),
    ("long dramatic whoosh", "sfx", .05), ("no paper turn", "sfx", .05),
    ("paper turn", "sfx", 8), ("whoosh", "sfx", 8),
])
def test_curated_collection_does_not_replace_specific_unrelated_or_continuous_requests(catalog, monkeypatch, query, kind, minimum):
    searches = []
    monkeypatch.setattr(audio_sources, "_search", lambda term, *args: searches.append(term) or [])
    assert audio_sources.acquire_audio(query, kind=kind, profile_id="default", min_duration=minimum, max_duration=600) is None
    assert searches, "Unmatched requests retain the normal bounded discovery path"
    assert not list((Path(config.DATA_DIR) / "audio-auto").rglob("*.ogg"))


@pytest.mark.parametrize("query,kind", [("ambient instrumental music", "music"), ("whoosh", "sfx")])
def test_separate_mood_constraint_cannot_select_a_calm_recording_for_aggressive_audio(catalog, monkeypatch, query, kind):
    monkeypatch.setattr(audio_sources, "_search", lambda *args: [])
    assert audio_sources.acquire_audio(query, kind=kind, profile_id="default", mood="aggressive energetic") is None


@pytest.mark.parametrize("direction,required_tag", [("slow tempo", "slow"), ("low tempo", "slow"),
    ("slow-tempo", "slow"), ("low-tempo", "slow"), ("slow  tempo", "slow"), ("calming", "calm")])
def test_raw_tempo_and_calming_constraints_select_the_verified_matching_recording(catalog, direction, required_tag):
    candidate = audio_sources._bundled_candidate(f"ambient instrumental music, {direction}", "music", 3, 600, "subtle")
    assert candidate and "Cylinder One" in candidate["title"]
    assets = json.loads((catalog / "catalog.json").read_text())["assets"]
    entry = next(asset for asset in assets if asset["provider_id"] == candidate["provider_id"])
    assert required_tag in entry["tags"]


def test_a_tempo_without_verified_collection_evidence_remains_a_live_search(catalog):
    assert audio_sources._bundled_candidate("ambient instrumental music, moderate tempo", "music", 3, 600, "subtle") is None


def test_bundled_copies_are_channel_owned_and_cache_cannot_relabel_their_source(catalog, monkeypatch):
    first = channels.create_profile("The video owner")
    second = channels.create_profile("Another active channel")
    a = audio_sources.acquire_audio("paper turn", kind="sfx", profile_id=first["id"])
    b = audio_sources.acquire_audio("paper turn", kind="sfx", profile_id=second["id"])
    assert a["path"] != b["path"] and a["sha256"] == b["sha256"]
    metadata = next(Path(a["path"]).parent.glob("*.json"))
    payload = json.loads(metadata.read_text())
    payload["asset"]["creator"] = "Incorrect cached attribution"
    metadata.write_text(json.dumps(payload))
    restored = audio_sources.acquire_audio("paper turn", kind="sfx", profile_id=first["id"])
    assert restored["creator"] == a["creator"] and audio_sources.get_status()["reason"] == "bundled"
    channels.delete_profile(first["id"])
    assert audio_sources.acquire_audio("paper turn", kind="sfx", profile_id=first["id"]) is None
    assert audio_sources.get_status()["reason"] == "profile_unavailable"


def test_corrupt_shipped_bytes_are_never_used_or_returned_from_an_old_cache(catalog, monkeypatch):
    asset = audio_sources.acquire_audio("paper turn", kind="sfx", profile_id="default")
    manifest = json.loads((catalog / "catalog.json").read_text())
    entry = next(row for row in manifest["assets"] if row["family"] == "paper_turn")
    path = catalog / entry["filename"]
    corrupted = bytearray(path.read_bytes())
    corrupted[len(corrupted) // 2] ^= 1
    path.write_bytes(corrupted)
    monkeypatch.setattr(audio_sources, "_search", lambda *args: [])
    assert audio_sources.acquire_audio("paper turn", kind="sfx", profile_id="default") is None
    assert Path(asset["path"]).is_file(), "A failed source check must not delete previously committed project audio"


def test_cancel_during_bundled_validation_does_not_commit_a_partial_cache(catalog, monkeypatch):
    from studio import audio_library
    actual_probe = audio_library._probe_audio
    stopped = threading.Event()
    def probe(path):
        result = actual_probe(path)
        stopped.set()
        return result
    monkeypatch.setattr(audio_library, "_probe_audio", probe)
    assert audio_sources.acquire_audio("paper turn", kind="sfx", profile_id="default", stop_event=stopped) is None
    assert audio_sources.get_status()["reason"] == "cancelled"
    assert not [path for path in (Path(config.DATA_DIR) / "audio-auto").rglob("*") if path.is_file()]


def test_reported_intent_mixes_actual_music_and_both_effects_and_retains_complete_description_credits(catalog, tmp_path, monkeypatch):
    previous = deepcopy(state)
    state.clear()
    state.update(fresh_project_state())
    stop_event.clear()
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(library, "LIB_DIR", str(output / "library"))
    owner = channels.create_profile("The requested film", "general", {"sound_mode": "auto", "sound_intensity": "balanced",
        "audio_mastering": True, "credit_yaas": False})
    other = channels.create_profile("A different active channel", "asmr")
    script = {"title": "A documentary explanation", "sections": [
        {"id": "opening", "narration": "A clear introduction."},
        {"id": "paper", "narration": "We turn to the evidence."},
        {"id": "next", "narration": "We move to the next point."}]}
    voice = output / "narration.wav"
    times = np.arange(24 * RATE) / RATE
    spoken = .12 * np.sin(2 * np.pi * 400 * times)
    spoken[(times > 3) & (times < 5)] = 0  # A deterministic pause exposes the real bed in the mixed output.
    with wave.open(str(voice), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes((spoken * 32767).astype("<i2").tobytes())
    timeline = {"duration": 24, "audio_sha256": audio_signature(voice), "script_signature": script_signature(script),
        "sections": [{"id": row["id"], "start": index * 8, "duration": 8} for index, row in enumerate(script["sections"])]}
    prompts = []
    def plan(prompt, *, provider_info, **kwargs):
        prompts.append(prompt)
        provider_info.update(provider="ollama", model="controlled-test-model")
        return json.dumps({"summary": "A calm instrumental bed, paper turn and restrained whoosh.",
            "music": {"use": True, "query": REPORTED_QUERY}, "ambience": {"use": False},
            "events": [{"section_id": "paper", "query": "soft paper turn", "label": "Evidence"},
                       {"section_id": "next", "query": "small quiet whoosh", "label": "Next point"}]})
    monkeypatch.setattr(gemini_client, "generate", plan)
    original = voice.read_bytes()
    state.update(script=script, timeline=timeline, audio_path=str(voice), profile_id=owner["id"], description="My edited introduction.")
    try:
        with config.profile_context(capture_profile_settings(owner["id"])):
            soundtrack = Path(runner._mix_project_audio(voice, timeline, output, owner["id"]))
        manifest = json.loads((output / "render_audio.json").read_text())
        design = manifest["sound_design"]
        assert manifest["version"] == 2 and manifest["soundtrack_sha256"] == audio_signature(soundtrack)
        assert design["music"] and [event["start"] for event in design["events"]] == [8, 16]
        assert "music bed" in design["summary"] and "2 timed effects" in design["summary"]
        assert design["profile_id"] == owner["id"] and not design["warnings"]
        assert len(manifest["credits"]) == len(load_audio_credits(timeline)) == 3
        assert all(item["provider"] == "bundled" and item["selection_source"] == "bundled_catalog" for item in manifest["credits"])
        assert "The requested film" in prompts[0] and "A different active channel" not in prompts[0]
        assert channels.active_profile_id() == other["id"]
        samples = _samples(soundtrack)
        assert len(samples) == 24 * RATE and np.isfinite(samples).all() and np.max(np.abs(samples)) < 1
        assert np.sqrt(np.mean(samples[3 * RATE:5 * RATE].astype(float) ** 2)) > .001, "The instrumental bed must actually be audible in the finished output"
        # Removing the constant test narration isolates the music/effect energy.
        # Each real effect must add energy inside its own measured window.
        def residual_rms(start, length):
            segment = samples[round(start * RATE):round((start + length) * RATE)].astype(float)
            ts = np.arange(len(segment)) / RATE + start
            basis = np.column_stack((np.sin(2 * np.pi * 400 * ts), np.cos(2 * np.pi * 400 * ts)))
            residual = segment - basis @ np.linalg.lstsq(basis, segment, rcond=None)[0]
            return np.sqrt(np.mean(residual ** 2))
        assert residual_rms(8, .8) > .002 and residual_rms(16, .18) > .002
        description = ensure_audio_credits(state["description"], timeline)
        assert description.startswith("My edited introduction.") and len(description) <= 5000
        assert all(item["source_url"] in description and item["license_url"] in description and item["creator"] in description
                   for item in manifest["credits"])
        assert voice.read_bytes() == original and not list(output.glob(".soundwork-*"))
    finally:
        state.clear()
        state.update(previous)
        stop_event.clear()
