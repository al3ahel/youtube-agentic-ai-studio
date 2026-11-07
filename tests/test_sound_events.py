"""Semantic cues use only their selected files and measured, nonoverlapping times."""
import shutil
import threading
import wave

import numpy as np
import pytest

from video.sound_effects import mix_sound_events


RATE = 48000


def write_wav(path, signal):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes((np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes())


def read_audio(path, samples):
    with wave.open(str(path), "rb") as stream:
        assert stream.getnframes() == samples and stream.getframerate() == RATE
        assert stream.getnchannels() == 2
        signal = np.frombuffer(stream.readframes(samples), dtype="<i2").reshape(-1, 2)[:, 0] / 32768
    assert np.isfinite(signal).all() and abs(signal).max() <= .951
    return signal


def amplitude(signal, frequency, start, length=.1):
    samples = signal[round(start * RATE):round((start + length) * RATE)]
    basis = np.exp(-2j * np.pi * frequency * np.arange(len(samples)) / RATE)
    return abs(np.mean(samples * basis))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for actual sound events")
def test_different_selected_assets_land_at_exact_cues_and_duck_from_clean_voice(tmp_path):
    times = np.arange(8 * RATE + 137) / RATE
    voice_samples = .4 * np.sin(2 * np.pi * 400 * times) * ((times >= 3) & (times < 5))
    voice, prepared, first, second = (tmp_path / name for name in ("voice.wav", "music.wav", "first.wav", "second.wav"))
    write_wav(voice, voice_samples)
    write_wav(prepared, voice_samples + .15 * np.sin(2 * np.pi * 700 * times))
    source_times = np.arange(3 * RATE) / RATE
    write_wav(first, .8 * np.sin(2 * np.pi * 1000 * source_times))
    write_wav(second, .6 * np.sin(2 * np.pi * 1800 * source_times))
    originals = {path: path.read_bytes() for path in (voice, prepared, first, second)}
    # The same asset may recur at semantic anchors; another event uses a
    # genuinely different file. Order in the caller's list is immaterial.
    events = [{"path": str(first), "start": 6.5, "duration": 1.2, "volume": .4},
              {"path": str(second), "start": 3.2, "duration": 1.2, "volume": .4},
              {"path": str(second), "start": .6, "duration": 1.2, "volume": .4}]
    results = []
    for number, base in enumerate((voice, prepared)):
        output = tmp_path / f"events-{number}.wav"
        assert mix_sound_events(voice, output, events, soundtrack_path=base) == str(output)
        signal = read_audio(output, len(times))
        quiet = amplitude(signal, 1800, .9)
        spoken = amplitude(signal, 1800, 3.5)
        assert quiet > .04 and spoken < quiet * .65
        assert amplitude(signal, 1000, 6.8) > .06
        assert amplitude(signal, 1000, .9) < .001
        assert amplitude(signal, 1800, 6.8) < .001
        for stamp in (.2, 2.2, 5.5, 7.8):
            assert amplitude(signal, 1000, stamp) < .001
            assert amplitude(signal, 1800, stamp) < .001
        results.append(signal)
    assert amplitude(results[1], 700, .9) > .04
    assert amplitude(results[1], 700, 3.5) > .04
    for stamp in (.9, 3.5):
        assert amplitude(results[1], 1800, stamp) == pytest.approx(amplitude(results[0], 1800, stamp), abs=.002)
    assert all(path.read_bytes() == original for path, original in originals.items())
    assert not list(tmp_path.glob(".sound-events-*"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for actual sound events")
def test_short_assets_never_loop_and_final_cue_preserves_last_voice_sample(tmp_path):
    samples = 3 * RATE + 4801
    voice, effect, output = (tmp_path / name for name in ("voice.wav", "short.wav", "events.wav"))
    write_wav(voice, np.zeros(samples))
    times = np.arange(round(.15 * RATE)) / RATE
    write_wav(effect, .7 * np.sin(2 * np.pi * 1000 * times))
    events = [{"path": str(effect), "start": 1, "duration": 1, "volume": .4},
              {"path": str(effect), "start": 3, "duration": 1, "volume": .4}]
    assert mix_sound_events(voice, output, events) == str(output)
    signal = read_audio(output, samples)
    assert amplitude(signal, 1000, 1.04, .04) > .04
    assert amplitude(signal, 1000, 1.3) < .001
    assert amplitude(signal, 1000, 3.03, .03) > .04
    assert abs(signal[-1]) < .001


@pytest.mark.parametrize("events", [
    [{"start": 1, "duration": 1}, {"start": 1, "duration": .5}],
    [{"start": 1, "duration": 1}, {"start": 1.5, "duration": .5}],
])
def test_overlapping_or_duplicate_cues_are_rejected_before_processing(tmp_path, events):
    events = [dict(event, path=str(tmp_path / "effect.wav"), volume=.1) for event in events]
    with pytest.raises(ValueError, match="overlap"):
        mix_sound_events(tmp_path / "voice.wav", tmp_path / "out.wav", events)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("key,value", [("start", -1), ("start", True), ("start", float("nan")),
    ("duration", 2.01), ("duration", 0), ("duration", float("inf")), ("volume", -1),
    ("volume", True), ("volume", 1.01), ("path", "relative.wav"), ("path", "https://example.invalid/effect.wav")])
def test_invalid_event_data_is_rejected_before_processing(tmp_path, key, value):
    event = {"path": str(tmp_path / "effect.wav"), "start": 1, "duration": .5, "volume": .1}
    event[key] = value
    with pytest.raises(ValueError):
        mix_sound_events(tmp_path / "voice.wav", tmp_path / "out.wav", [event])
    assert not list(tmp_path.iterdir())


def test_bounded_event_list_and_no_cues_keep_sources_untouched(tmp_path):
    voice, output = tmp_path / "voice.wav", tmp_path / "out.wav"
    assert mix_sound_events(voice, output, []) == str(voice)
    event = {"path": str(tmp_path / "effect.wav"), "start": 1, "duration": .5, "volume": 0}
    assert mix_sound_events(voice, output, [event]) == str(voice)
    with pytest.raises(ValueError, match="40"):
        mix_sound_events(voice, output, [event] * 41)
    with pytest.raises(ValueError, match="separate"):
        mix_sound_events(voice, voice, [])
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for actual sound events")
def test_missing_asset_or_failed_mix_applies_none_and_keeps_previous_success(tmp_path, monkeypatch, capsys):
    from video import sound_effects
    voice, prepared, effect, output = (tmp_path / name for name in ("voice.wav", "music.wav", "effect.wav", "out.wav"))
    write_wav(voice, np.zeros(4 * RATE))
    write_wav(prepared, np.ones(4 * RATE) * .05)
    write_wav(effect, np.ones(RATE) * .1)
    output.write_bytes(b"previous good mix")
    events = [{"path": str(effect), "start": .5, "duration": .5, "volume": .2},
              {"path": str(tmp_path / "deleted.wav"), "start": 2, "duration": .5, "volume": .2}]
    assert mix_sound_events(voice, output, events, soundtrack_path=prepared) == str(prepared)
    assert "asset is unavailable" in capsys.readouterr().out
    def fail(*args, **kwargs):
        raise RuntimeError("fixture failure")
    monkeypatch.setattr(sound_effects, "_run", fail)
    assert mix_sound_events(voice, output, events[:1], soundtrack_path=prepared) == str(prepared)
    assert "mix unavailable" in capsys.readouterr().out
    assert output.read_bytes() == b"previous good mix"
    assert not list(tmp_path.glob(".sound-events-*"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for actual sound events")
def test_cancel_after_encoding_keeps_previous_output(tmp_path, monkeypatch):
    from video import sound_effects
    voice, effect, output = (tmp_path / name for name in ("voice.wav", "effect.wav", "out.wav"))
    write_wav(voice, np.zeros(2 * RATE))
    write_wav(effect, np.ones(RATE) * .1)
    output.write_bytes(b"previous good mix")
    event = threading.Event()
    encode = sound_effects._run
    def cancel(*args, **kwargs):
        encode(*args, **kwargs)
        event.set()
    monkeypatch.setattr(sound_effects, "_run", cancel)
    with pytest.raises(InterruptedError):
        mix_sound_events(voice, output, [{"path": str(effect), "start": .5, "duration": .5, "volume": .2}], stop_event=event)
    assert output.read_bytes() == b"previous good mix"
    assert not list(tmp_path.glob(".sound-events-*"))
