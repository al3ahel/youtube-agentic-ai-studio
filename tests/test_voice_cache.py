"""Section reuse must keep the exact voice, PCM samples and local speech timing."""
import json
from pathlib import Path
import shutil
import threading
import wave
import zipfile

import pytest

import config
from video import narrator
from video.timeline import read_srt


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required to decode section audio")


@pytest.fixture
def recordings(monkeypatch):
    calls = []
    counts = {"First sentence.": 35011, "Second sentence.": 56117, "Edited second sentence.": 72123}
    monkeypatch.setattr(config, "VOICE_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "VOICE_CACHE_DIR", "")
    monkeypatch.setattr(narrator, "version", lambda package: "test-engine-1")

    async def synthesise(text, audio_path, srt_path, timings_path=None, stop_event=None):
        calls.append((text, config.VOICE_ID, config.VOICE_RATE, config.VOICE_PITCH))
        count = counts.get(text, 48000)
        # A real, nonempty PCM stream with a deterministic waveform. FFmpeg can
        # identify it independently of the .mp3 filename used for provider data.
        amplitude = 200 + sum(ord(char) for char in text) % 1000
        with wave.open(audio_path, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes(amplitude.to_bytes(2, "little", signed=True) * count)
        end = count / 48000 - .05
        narrator._write_srt_blocks([(.075 * 1000, end * 1000, text)], srt_path)
        Path(timings_path).write_text(json.dumps({"version": 1, "source": "word",
            "words": [{"word": text, "start": .075, "end": end}]}), encoding="utf-8")
        return 1

    monkeypatch.setattr(narrator, "_synthesise", synthesise)
    return calls, counts


def script():
    return {"title": "Cache test", "sections": [
        {"id": "intro", "title": "First", "narration": "First sentence."},
        {"id": "body", "title": "Second", "narration": "Second sentence."},
    ]}


def manifest(directory):
    return json.loads((directory / "narration.timings.json").read_text(encoding="utf-8"))


def test_repeated_script_reuses_audio_and_real_timing_without_synthesis(tmp_path, recordings):
    calls, _ = recordings
    first, second = tmp_path / "first", tmp_path / "second"
    narrator.generate_narration(script(), first)
    narrator.generate_narration(script(), second)
    assert len(calls) == 2
    assert (second / "narration.wav").read_bytes() == (first / "narration.wav").read_bytes()
    assert (second / "narration.srt").read_bytes() == (first / "narration.srt").read_bytes()
    before, after = manifest(first), manifest(second)
    assert before["sections"] == after["sections"] and before["words"] == after["words"]
    assert before["audio_sha256"] == after["audio_sha256"]
    assert after["voice_cache"] == {"enabled": True, "reused_sections": 2}
    assert len(list((Path(config.DATA_DIR) / "voice-cache").glob("*.zip"))) == 2


def test_cached_middle_section_is_reported_and_assembled_with_all_audio_and_timing(tmp_path, recordings, capsys):
    calls, counts = recordings
    full_script = script()
    full_script["sections"].append({"id": "closing", "title": "Third", "narration": "Third sentence."})
    narrator.generate_narration({"sections": [full_script["sections"][1]]}, tmp_path / "prewarmed")
    calls.clear()
    capsys.readouterr()

    destination = tmp_path / "three-sections"
    narrator.generate_narration(full_script, destination)

    assert [call[0] for call in calls] == ["First sentence.", "Third sentence."]
    report = capsys.readouterr().out
    first = report.index("Recording section 1/3: First")
    middle = report.index("Reusing section 2/3: Second (validated voice cache and speech timing).")
    third = report.index("Recording section 3/3: Third")
    assert first < middle < third
    assert "Recording section 2/3" not in report

    sample_counts = [counts.get(section["narration"], 48000) for section in full_script["sections"]]
    total_samples = sum(sample_counts)
    with wave.open(str(destination / "narration.wav"), "rb") as assembled:
        assert assembled.getnframes() == total_samples
        expected_pcm = b"".join(
            (200 + sum(ord(char) for char in section["narration"]) % 1000).to_bytes(
                2, "little", signed=True) * sample_count
            for section, sample_count in zip(full_script["sections"], sample_counts)
        )
        assert assembled.readframes(total_samples) == expected_pcm

    timed = manifest(destination)
    assert timed["voice_cache"] == {"enabled": True, "reused_sections": 1}
    assert timed["duration"] == total_samples / 48000
    assert [section["id"] for section in timed["sections"]] == ["intro", "body", "closing"]
    assert [word["section_id"] for word in timed["words"]] == ["intro", "body", "closing"]
    captions = read_srt(str(destination / "narration.srt"))
    assert [cue["text"] for cue in captions] == [section["narration"] for section in full_script["sections"]]
    for index, sample_count in enumerate(sample_counts):
        start = sum(sample_counts[:index]) / 48000
        assert timed["sections"][index]["start"] == start
        assert timed["sections"][index]["duration"] == sample_count / 48000
        assert timed["words"][index]["start"] == pytest.approx(start + .075)
        assert captions[index]["start"] == pytest.approx(start + .075, abs=.0005)
    assert (f"Narration assembled: 3/3 sections, {total_samples / 48000:.2f}s measured audio "
            "(1 reused, 2 recorded).") in report


def test_only_changed_text_records_again_and_reordered_sections_get_new_offsets(tmp_path, recordings):
    calls, counts = recordings
    original, edited, reordered = (tmp_path / name for name in ("original", "edited", "reordered"))
    narrator.generate_narration(script(), original)
    changed = script()
    changed["sections"][1]["narration"] = "Edited second sentence."
    narrator.generate_narration(changed, edited)
    assert [call[0] for call in calls] == ["First sentence.", "Second sentence.", "Edited second sentence."]
    assert manifest(edited)["voice_cache"]["reused_sections"] == 1
    # Section identity and order belong to the current script, not the cache.
    changed["sections"] = list(reversed(changed["sections"]))
    changed["sections"][0]["id"] = "new-first-id"
    narrator.generate_narration(changed, reordered)
    assert len(calls) == 3
    timed = manifest(reordered)
    expected_start = counts["Edited second sentence."] / 48000
    assert timed["sections"][0]["id"] == "new-first-id"
    assert timed["sections"][1]["start"] == expected_start
    assert timed["words"][1]["start"] == pytest.approx(expected_start + .075)
    assert timed["words"][0]["section_id"] == "new-first-id"
    assert timed["duration"] == (counts["First sentence."] + counts["Edited second sentence."]) / 48000
    captions = read_srt(str(reordered / "narration.srt"))
    assert captions[1]["start"] == pytest.approx(expected_start + .075, abs=.0005)
    assert captions[0]["text"] == "Edited second sentence."


@pytest.mark.parametrize("setting,value", [("VOICE_ID", "different-voice"), ("VOICE_RATE", "+12%"), ("VOICE_PITCH", "+5Hz")])
def test_voice_setting_change_never_uses_old_voice(tmp_path, recordings, monkeypatch, setting, value):
    calls, _ = recordings
    narrator.generate_narration(script(), tmp_path / "before")
    monkeypatch.setattr(config, setting, value)
    narrator.generate_narration(script(), tmp_path / "after")
    assert len(calls) == 4
    assert manifest(tmp_path / "after")["voice_cache"]["reused_sections"] == 0


def test_engine_version_or_cache_version_change_invalidates_old_recordings(tmp_path, recordings, monkeypatch):
    calls, _ = recordings
    narrator.generate_narration(script(), tmp_path / "before")
    monkeypatch.setattr(narrator, "version", lambda package: "test-engine-2")
    narrator.generate_narration(script(), tmp_path / "engine-update")
    monkeypatch.setattr(narrator, "_VOICE_CACHE_VERSION", 2)
    narrator.generate_narration(script(), tmp_path / "format-update")
    assert len(calls) == 6


@pytest.mark.parametrize("damage", ["audio", "timing", "archive"])
def test_corrupt_cache_regenerates_section_and_repairs_it(tmp_path, recordings, damage):
    calls, _ = recordings
    narrator.generate_narration(script(), tmp_path / "before")
    cache = Path(config.DATA_DIR) / "voice-cache"
    key = narrator._voice_cache_key("First sentence.")
    entry = cache / f"v1_{key}.zip"
    if damage == "archive":
        entry.write_bytes(b"incomplete cache")
    else:
        with zipfile.ZipFile(entry) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        name = "audio.wav" if damage == "audio" else "timings.json"
        files[name] = b"changed audio" if damage == "audio" else b'{"source":"word","words":[]}'
        with zipfile.ZipFile(entry, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
    narrator.generate_narration(script(), tmp_path / "repaired")
    assert [call[0] for call in calls] == ["First sentence.", "Second sentence.", "First sentence."]
    narrator.generate_narration(script(), tmp_path / "reused")
    assert len(calls) == 3
    assert manifest(tmp_path / "reused")["voice_cache"]["reused_sections"] == 2
    assert not list(cache.glob("*.tmp"))


def test_opt_out_and_unwritable_cache_do_not_break_recording(tmp_path, recordings, monkeypatch):
    calls, _ = recordings
    monkeypatch.setattr(config, "VOICE_CACHE_ENABLED", False)
    narrator.generate_narration(script(), tmp_path / "disabled")
    narrator.generate_narration(script(), tmp_path / "disabled-again")
    assert len(calls) == 4
    assert not (Path(config.DATA_DIR) / "voice-cache").exists()
    narrator.generate_narration(script(), tmp_path / "override", use_cache=True)
    narrator.generate_narration(script(), tmp_path / "override-again", use_cache=True)
    assert len(calls) == 6
    not_directory = tmp_path / "read-only-cache"
    not_directory.write_text("A user file, never replace it.", encoding="utf-8")
    monkeypatch.setattr(config, "VOICE_CACHE_DIR", str(not_directory))
    narrator.generate_narration(script(), tmp_path / "still-recorded", use_cache=True)
    assert len(calls) == 8
    assert not_directory.read_text() == "A user file, never replace it."


def test_cancelled_new_section_retains_previous_output_and_atomic_cache(tmp_path, recordings, monkeypatch):
    calls, _ = recordings
    destination = tmp_path / "output"
    narrator.generate_narration(script(), destination)
    saved = {path.name: path.read_bytes() for path in destination.iterdir()}
    event = threading.Event()
    synth = narrator._synthesise
    async def cancel(*args, **kwargs):
        result = await synth(*args, **kwargs)
        event.set()
        return result
    monkeypatch.setattr(narrator, "_synthesise", cancel)
    changed = script()
    changed["sections"][1]["narration"] = "Edited second sentence."
    with pytest.raises(InterruptedError):
        narrator.generate_narration(changed, destination, stop_event=event)
    assert all((destination / name).read_bytes() == value for name, value in saved.items())
    cache = Path(config.DATA_DIR) / "voice-cache"
    assert len(list(cache.glob("*.zip"))) == 2 and not list(cache.glob("*.tmp"))
    monkeypatch.setattr(narrator, "_synthesise", synth)
    narrator.generate_narration(script(), tmp_path / "retry")
    assert manifest(tmp_path / "retry")["voice_cache"]["reused_sections"] == 2


def test_cache_eviction_is_bounded_and_only_removes_its_own_entries(tmp_path, recordings, monkeypatch):
    calls, _ = recordings
    monkeypatch.setattr(narrator, "_VOICE_CACHE_ENTRIES", 1)
    cache = Path(config.DATA_DIR) / "voice-cache"
    cache.mkdir(parents=True)
    unrelated = cache / "recordings.zip"
    unrelated.write_bytes(b"user data")
    narrator.generate_narration(script(), tmp_path / "output")
    assert len(list(cache.glob("v1_*.zip"))) == 1
    assert unrelated.read_bytes() == b"user data"
    assert manifest(tmp_path / "output")["duration"] > 1
