"""Sound intent is automatic, channel-owned, bounded and tied to real timing."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest

import config
from agents import audio_director as director
from apis import audio_sources
from studio import audio_library, channels


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SOUND_INTENSITY", "low")
    monkeypatch.setattr(config, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    profile = channels.create_profile("Automatic sound", "general")
    sections = [{"id": str(index + 1), "start": index * 10, "duration": 10} for index in range(6)]
    timeline = {"duration": 60, "audio_sha256": "voice-hash", "script_signature": "script-hash", "sections": sections}
    script = {"title": "A calm journey", "sections": [{"id": row["id"], "title": "Next idea", "narration": "Explain this clearly."} for row in sections]}
    monkeypatch.setattr(audio_library, "list_audio_assets", lambda profile_id: [])
    monkeypatch.setattr(director.gemini_client, "last_provider", "offline fixture")
    calls = []
    def acquire(query, **kwargs):
        calls.append((query, kwargs))
        path = tmp_path / ("music.wav" if kwargs["kind"] == "music" else "effect.wav")
        path.write_bytes(b"owned fixture")
        return {"id": "a" * 32 if kwargs["kind"] == "music" else "b" * 32,
                "kind": kwargs["kind"], "title": query, "path": str(path), "filename": path.name,
                "duration": 8, "bytes": path.stat().st_size, "creator": "Fixture creator",
                "provider": "wikimedia", "source_url": "https://commons.wikimedia.org/wiki/File:Fixture.wav",
                "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "attribution": "Fixture creator, CC BY 4.0", "content_id": "unknown"}
    monkeypatch.setattr(audio_sources, "acquire_audio", acquire)
    return profile, script, timeline, calls


def intent():
    return {"summary": "Quiet music and a purposeful chapter change.",
            "music": {"use": True, "query": "gentle instrumental piano", "asset_id": "", "reason": "Support the explanation."},
            "ambience": {"use": False},
            "events": [{"section_id": "2", "query": "soft paper turn", "label": "Next chapter", "reason": "Mark the new subject."}]}


def test_ai_chooses_and_acquires_without_manual_tracks_for_the_project_owner(project, monkeypatch):
    owner, script, timeline, calls = project
    channels.create_profile("Other Home channel", "asmr")
    prompts = []
    monkeypatch.setattr(director.gemini_client, "generate", lambda prompt, **kwargs: prompts.append(prompt) or json.dumps(intent()))
    plan, files = director.direct_audio(script, timeline, owner["id"])
    assert plan["planner"]["status"] == "ai"
    assert plan["profile_id"] == owner["id"]
    assert [kwargs["profile_id"] for _, kwargs in calls] == [owner["id"], owner["id"]]
    assert len(plan["events"]) == 1 and plan["events"][0]["start"] == 10
    assert plan["events"][0]["duration"] == 1.2
    assert files["events"][0]["start"] == 10 and Path(files["music"]["path"]).is_file()
    assert plan["music"]["asset"]["license"] == "CC BY 4.0"
    assert "path" not in plan["music"]["asset"]
    assert "Automatic sound" in prompts[0] and "Other Home channel" not in prompts[0]
    assert channels.get_profile(owner["id"])["defaults"]["music_track"] == ""


@pytest.mark.parametrize("preset", ["news", "asmr"])
def test_calm_and_news_do_not_receive_surprise_transition_hits(project, monkeypatch, preset):
    _, script, timeline, calls = project
    owner = channels.create_profile("Restrained channel", preset)
    proposed = intent()
    proposed["ambience"] = {"use": True, "query": "gentle rain", "reason": "Background recording"}
    monkeypatch.setattr(director.gemini_client, "generate", lambda *args, **kwargs: json.dumps(proposed))
    plan, files = director.direct_audio(script, timeline, owner["id"])
    assert plan["events"] == files["events"] == []
    assert not (plan["music"] and plan["ambience"])
    if preset == "asmr":
        assert plan["ambience"] and not plan["music"]


@pytest.mark.parametrize("change", [
    lambda value: value.update(path="/private/file.wav"),
    lambda value: value["events"][0].update(start=999),
    lambda value: value["events"][0].update(section_id="missing"),
    lambda value: value["music"].update(query="https://evil.invalid/audio.mp3"),
    lambda value: value["music"].update(asset_id="../../secret"),
    lambda value: value["music"].update(use="true"),
])
def test_model_cannot_control_paths_times_or_unknown_sections(project, change):
    _, _, timeline, _ = project
    proposed = intent()
    change(proposed)
    with pytest.raises(ValueError):
        director.validate_intent(proposed, timeline, "general")


def test_absent_ai_or_licensed_audio_is_honest_voice_only_fallback(project, monkeypatch):
    owner, script, timeline, calls = project
    def unavailable(*args, **kwargs):
        raise RuntimeError("No free model available")
    monkeypatch.setattr(director.gemini_client, "generate", unavailable)
    monkeypatch.setattr(audio_sources, "acquire_audio", lambda *args, **kwargs: None)
    plan, files = director.direct_audio(script, timeline, owner["id"])
    director.summarize_applied(plan)
    assert plan["planner"]["status"] == "local"
    assert not files["music"] and not files["ambience"] and files["events"] == []
    assert "Narration only" in plan["summary"]
    assert any("No suitable licensed recording" in note for note in plan["warnings"])


def test_cues_have_spacing_and_do_not_fire_on_opening_or_every_cut(project, monkeypatch):
    owner, script, timeline, calls = project
    proposed = intent()
    proposed["music"] = {"use": False}
    proposed["events"] = [{"section_id": str(i), "query": "soft paper turn", "label": "Chapter"} for i in range(1, 7)]
    monkeypatch.setattr(director.gemini_client, "generate", lambda *args, **kwargs: json.dumps(proposed))
    plan, _ = director.direct_audio(script, timeline, owner["id"])
    assert len(plan["events"]) == 4
    assert all(row["start"] > 0 for row in plan["events"])
    assert len(calls) == 1, "One suitable recording is cached within this plan"


def test_cancel_never_becomes_a_silent_fallback(project):
    owner, script, timeline, calls = project
    stop = threading.Event()
    stop.set()
    with pytest.raises(InterruptedError):
        director.direct_audio(script, timeline, owner["id"], stop_event=stop)
    assert not calls


def test_continuous_ambience_requests_a_real_bed_not_a_tiny_effect(project, monkeypatch):
    _, script, timeline, calls = project
    owner = channels.create_profile("Rain channel", "asmr")
    proposed = {"music": {"use": False},
                "ambience": {"use": True, "query": "rain recording"}, "events": []}
    monkeypatch.setattr(director.gemini_client, "generate", lambda *args, **kwargs: json.dumps(proposed))
    plan, files = director.direct_audio(script, timeline, owner["id"])
    assert plan["ambience"] and files["ambience"]
    assert calls[0][1]["min_duration"] == 8
    assert plan["ambience"]["duration"] == timeline["duration"]


def test_unpublishable_credit_is_omitted_before_a_recording_can_be_mixed(project, monkeypatch):
    owner, script, timeline, _ = project
    proposed = intent()
    proposed["events"] = []
    monkeypatch.setattr(director.gemini_client, "generate", lambda *args, **kwargs: json.dumps(proposed))
    acquire = audio_sources.acquire_audio
    def with_long_credit(*args, **kwargs):
        asset = acquire(*args, **kwargs)
        asset["attribution"] = "Required complete attribution " * 150
        return asset
    monkeypatch.setattr(audio_sources, "acquire_audio", with_long_credit)
    plan, files = director.direct_audio(script, timeline, owner["id"])
    assert plan["music"] is files["music"] is None
    assert any("all required audio credits complete" in warning for warning in plan["warnings"])


def test_queued_sound_uses_captured_preset_and_description_after_profile_edit(project, monkeypatch):
    from studio import schedules
    _, script, timeline, calls = project
    owner = channels.create_profile("Queued calm channel", "asmr", defaults={
        "channel_desc": "The captured quiet channel description.", "sound_intensity": "low"})
    item = schedules.create_schedule({"profile_id": owner["id"], "name": "Quiet scheduled video",
        "cron": "0 9 * * *", "timezone": "UTC", "visual_mode": "motion"})
    schedules.enqueue_now(item["id"])
    with schedules._db() as db:
        payload = json.loads(db.execute("SELECT payload FROM schedule_jobs WHERE schedule_id=?", (item["id"],)).fetchone()[0])
    channels.update_profile(owner["id"], {"preset": "general", "defaults": {
        "channel_name": "New brand after enqueue", "channel_desc": "A newly edited energetic channel.",
        "sound_intensity": "balanced"}})
    proposed = intent()
    proposed["ambience"] = {"use": True, "query": "gentle rain", "reason": "Quiet background"}
    prompts = []
    monkeypatch.setattr(director.gemini_client, "generate", lambda prompt, **kwargs: prompts.append(prompt) or json.dumps(proposed))

    with config.profile_context(payload["profile_values"]):
        plan, files = director.direct_audio(script, timeline, owner["id"])

    data = json.loads(prompts[0].split("PROJECT DATA:\n", 1)[1])
    assert data["preset"] == plan["preset"] == "asmr"
    assert data["channel"] == "Queued calm channel"
    assert data["channel_description"] == "The captured quiet channel description."
    assert data["intensity"] == plan["intensity"] == "low"
    assert plan["events"] == files["events"] == []
    assert plan["music"] is None and plan["ambience"]
    assert all(options["mood"] == "calm" and options["profile_id"] == owner["id"] for _, options in calls)
    assert channels.get_profile(owner["id"])["preset"] == "general"


def test_planner_provenance_uses_this_response_even_when_another_request_changes_global_provider(project, monkeypatch):
    owner, script, timeline, _ = project
    barrier, lock = threading.Barrier(2), threading.Lock()
    serial = 0
    def generate(prompt, *, provider_info, **kwargs):
        nonlocal serial
        with lock:
            serial += 1
            number = serial
        provider_info.update(provider="groq" if number == 1 else "gemini", model=f"free-model-{number}")
        barrier.wait(timeout=5)
        director.gemini_client.last_provider = "Another worker's unrelated model"
        return json.dumps({"music": {"use": False}, "ambience": {"use": False}, "events": []})
    monkeypatch.setattr(director.gemini_client, "generate", generate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        plans = list(pool.map(lambda _: director.direct_audio(script, timeline, owner["id"])[0], range(2)))
    assert {plan["planner"]["provider"] for plan in plans} == {"Groq · free-model-1", "Gemini · free-model-2"}


@pytest.mark.parametrize("intensity,music,cue", [("low", .14, .22), ("balanced", .20, .30)])
def test_automatic_plan_records_ratios_for_the_gain_staged_mix(project, monkeypatch, intensity, music, cue):
    owner, script, timeline, _ = project
    channels.update_profile(owner["id"], {"defaults": {"sound_intensity": intensity}})
    monkeypatch.setattr(director.gemini_client, "generate", lambda *args, **kwargs: json.dumps(intent()))
    plan, files = director.direct_audio(script, timeline, owner["id"])
    assert plan["music"]["volume"] == music
    assert plan["events"][0]["volume"] == files["events"][0]["volume"] == cue
