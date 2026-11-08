"""Audio-clock invariants: real samples, absolute frame boundaries, no guessing."""
import json
import math
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from video.timeline import build_timeline, probe_duration, read_srt, script_signature


def wave_file(path, duration, rate=48000):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\x00\x00" * round(duration * rate))


@pytest.fixture
def spoken(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VIDEO_FPS", 24)
    script = {"title": "A measured story", "sections": [
        {"id": 1, "title": "First", "narration": "First comes observation."},
        {"id": 2, "title": "Second", "narration": "Then we test the explanation."},
    ]}
    wave_file(tmp_path / "narration.wav", 2.04)
    timing = {"version": 1, "script_signature": script_signature(script), "duration": 2.04,
              "sections": [{"id": 1, "start": 0, "duration": .817, "caption_timing": "word"},
                           {"id": 2, "start": .817, "duration": 1.223, "caption_timing": "phrase"}],
              "words": [{"word": "Then", "start": .9, "end": 1.05, "section_id": 2}]}
    (tmp_path / "narration.timings.json").write_text(json.dumps(timing))
    (tmp_path / "narration.srt").write_text("1\n00:00:00,900 --> 00:00:01,600\nThen we test\nthe explanation.\n")
    return tmp_path, script, timing


def test_measured_clock_covers_complete_audio_without_accumulated_rounding(spoken):
    path, script, _ = spoken
    timeline = build_timeline(script, str(path / "narration.wav"), str(path))
    assert timeline["total_frames"] == math.ceil(2.04 * 24)
    assert sum(s["duration_frames"] for s in timeline["sections"]) == timeline["total_frames"]
    assert timeline["sections"][1]["start_frame"] == round(.817 * 24)
    assert timeline["sections"][0]["duration"] == timeline["sections"][1]["start"]
    assert timeline["audio_duration"] == 2.04
    assert 0 <= timeline["duration"] - timeline["audio_duration"] < 1 / 24
    assert timeline["captions"][0] == {"start": .9, "end": 1.6, "text": "Then we test the explanation."}
    assert timeline["words"][0]["start"] == .9
    assert json.loads((path / "timeline.json").read_text()) == timeline


@pytest.mark.parametrize("damage", ["signature", "duration", "gap", "overlap", "order", "last_end", "nan"])
def test_stale_or_inconsistent_timing_is_rejected(spoken, damage):
    path, script, timing = spoken
    if damage == "signature":
        script["sections"][0]["narration"] += " Changed."
    elif damage == "duration":
        timing["duration"] = 4
    elif damage == "gap":
        timing["sections"][1]["start"] += .3
    elif damage == "overlap":
        timing["sections"][1]["start"] -= .3
    elif damage == "order":
        timing["sections"].reverse()
    elif damage == "last_end":
        timing["sections"][1]["duration"] += .3
    elif damage == "nan":
        timing["duration"] = float("nan")
    (path / "narration.timings.json").write_text(json.dumps(timing))
    with pytest.raises(ValueError):
        build_timeline(script, str(path / "narration.wav"), str(path))


def test_missing_legacy_metadata_requests_narration_instead_of_guessing(spoken):
    path, script, _ = spoken
    (path / "narration.timings.json").unlink()
    with pytest.raises(ValueError, match="Narration step"):
        build_timeline(script, str(path / "narration.wav"), str(path))


def test_audio_hash_detects_interrupted_or_replaced_same_length_recording(spoken):
    from video.timeline import audio_signature
    path, script, timing = spoken
    voice = path / "narration.wav"
    timing["audio_sha256"] = audio_signature(voice)
    (path / "narration.timings.json").write_text(json.dumps(timing))
    data = bytearray(voice.read_bytes())
    data[-2:] = b"\x01\x00"
    voice.write_bytes(data)
    with pytest.raises(ValueError, match="audio changed"):
        build_timeline(script, str(voice), str(path))


def test_srt_clamps_overrun_without_inventing_word_events(tmp_path):
    path = tmp_path / "voice.srt"
    path.write_text("1\n00:00:00,100 --> 00:00:04,000\nWords belong together.\n")
    assert read_srt(str(path), 2) == [{"start": .1, "end": 2, "text": "Words belong together."}]


def test_segmented_narration_preserves_samples_and_offsets_cues(spoken, monkeypatch):
    import shutil
    from video import narrator
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required for the decoded-audio integration test")
    path, script, _ = spoken
    durations = [.817, 1.223]
    calls = []
    async def fake_speech(text, audio_path, srt_path, timings_path=None, stop_event=None):
        index = len(calls)
        calls.append(text)
        # FFmpeg recognizes the WAV header even at the temporary .mp3 path.
        wave_file(audio_path, durations[index])
        Path(srt_path).write_text("1\n00:00:00,100 --> 00:00:00,500\nSpoken words\n")
        Path(timings_path).write_text(json.dumps({"source": "word", "words": [
            {"word": "Spoken", "start": .1, "end": .3}]}))
        return 1
    monkeypatch.setattr(narrator, "_synthesise", fake_speech)
    result = narrator.generate_narration(script, str(path))
    assert result.endswith("narration.wav")
    assert probe_duration(result) == sum(durations)
    metadata = json.loads((path / "narration.timings.json").read_text())
    assert metadata["sections"][1]["start"] == .817
    assert metadata["words"][1]["start"] == pytest.approx(.917)
    assert read_srt(str(path / "narration.srt"))[1]["start"] == .917
    assert build_timeline(script, result, str(path))["audio_duration"] == 2.04


def test_cancelled_narration_keeps_previous_successful_files(spoken):
    from video import narrator
    path, script, _ = spoken
    previous = (path / "narration.wav").read_bytes()
    event = threading.Event()
    event.set()
    with pytest.raises(InterruptedError):
        narrator.generate_narration(script, str(path), stop_event=event)
    assert (path / "narration.wav").read_bytes() == previous


def test_synthesiser_requests_actual_word_boundaries(tmp_path, monkeypatch):
    import asyncio
    import sys
    from video import narrator
    # This stub checks speech events, not an external proxy's TLS trust setup.
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    calls = []
    class Speech:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)
        async def stream(self):
            yield {"type": "audio", "data": b"fake-audio"}
            yield {"type": "WordBoundary", "offset": 1000000, "duration": 2000000, "text": "Hello"}
    monkeypatch.setitem(sys.modules, "edge_tts", SimpleNamespace(Communicate=Speech))
    timings = tmp_path / "words.json"
    asyncio.run(narrator._synthesise("Hello", str(tmp_path / "audio.mp3"), str(tmp_path / "audio.srt"), str(timings)))
    assert calls[0]["boundary"] == "WordBoundary"
    assert json.loads(timings.read_text())["words"] == [{"word": "Hello", "start": .1, "end": .3}]
