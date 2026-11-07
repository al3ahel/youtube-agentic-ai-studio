"""Actual sound mixing: preserve timing, duck the bed, and bound peaks."""
import shutil
import threading
import wave
from pathlib import Path

import numpy as np
import pytest

from video.music import mix_music_with_narration
from video.sound_effects import mix_sound_effects


def wav(path, samples, rate=48000):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


def test_empty_library_returns_original_without_wrong_extension(tmp_path):
    voice = tmp_path / "voice.wav"
    wav(voice, np.zeros(48000))
    original = voice.read_bytes()
    target = tmp_path / "mixed.mp3"
    assert mix_music_with_narration(str(voice), str(target), library_dir=str(tmp_path / "missing")) == str(voice)
    assert voice.read_bytes() == original and not target.exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for actual sound mixing")
def test_music_ducks_during_speech_and_preserves_exact_wav_duration(tmp_path):
    rate = 48000
    times = np.arange(6 * rate) / rate
    voice_samples = .45 * np.sin(2 * np.pi * 400 * times) * ((times >= 2) & (times < 4))
    voice = tmp_path / "voice.wav"
    wav(voice, voice_samples)
    tracks = tmp_path / "music"
    tracks.mkdir()
    wav(tracks / "bed.wav", .6 * np.sin(2 * np.pi * 1000 * times))
    target = tmp_path / "mixed.wav"
    result = mix_music_with_narration(str(voice), str(target), music_volume=.5, library_dir=str(tracks))
    assert result == str(target)
    with wave.open(str(target), "rb") as handle:
        assert handle.getframerate() == rate and handle.getnframes() == 6 * rate
        signal = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").reshape(-1, 2)[:, 0] / 32768
    def amplitude(start):
        cut = signal[int(start * rate):int((start + .2) * rate)]
        basis = np.exp(-2j * np.pi * 1000 * np.arange(len(cut)) / rate)
        return abs(np.mean(cut * basis))
    assert amplitude(3) < amplitude(1.5) * .65
    assert abs(signal).max() <= .951
    assert not list(tmp_path.glob(".soundtrack-*"))


@pytest.mark.parametrize("volume", [-1, 2, float("nan"), True])
def test_invalid_gain_is_rejected(tmp_path, volume):
    with pytest.raises(ValueError):
        mix_music_with_narration(str(tmp_path / "a.wav"), str(tmp_path / "b.wav"), music_volume=volume)


def test_cancelled_mix_does_not_touch_files(tmp_path):
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(InterruptedError):
        mix_music_with_narration(str(tmp_path / "a.wav"), str(tmp_path / "b.wav"), stop_event=stopped)
    with pytest.raises(InterruptedError):
        mix_sound_effects(str(tmp_path / "a.wav"), str(tmp_path / "b.wav"), str(tmp_path / "effect.wav"), stop_event=stopped)
    assert not list(tmp_path.iterdir())


def read_mix(path, expected_frames):
    with wave.open(str(path), "rb") as handle:
        assert handle.getframerate() == 48000 and handle.getnframes() == expected_frames
        signal = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").reshape(-1, 2)[:, 0] / 32768
    assert np.isfinite(signal).all() and abs(signal).max() <= .951
    return signal


def tone_amplitude(signal, frequency, start, length=.1):
    cut = signal[round(start * 48000):round((start + length) * 48000)]
    basis = np.exp(-2j * np.pi * frequency * np.arange(len(cut)) / 48000)
    return abs(np.mean(cut * basis))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for actual sound mixing")
def test_selected_music_is_exact_and_does_not_fall_back_to_folder(tmp_path, monkeypatch):
    from video import music
    times = np.arange(3 * 48000) / 48000
    voice, selected = tmp_path / "voice.wav", tmp_path / "chosen.wav"
    wav(voice, np.zeros(len(times)))
    wav(selected, .6 * np.sin(2 * np.pi * 1000 * times))
    original = voice.read_bytes()
    folder = tmp_path / "random-music"
    folder.mkdir()
    wav(folder / "not-selected.wav", .6 * np.sin(2 * np.pi * 2000 * times))
    monkeypatch.setattr(music, "_get_library_tracks", lambda *args: pytest.fail("An explicit track must not use folder selection"))
    target = tmp_path / "mixed.wav"
    assert mix_music_with_narration(voice, target, .3, library_dir=folder, track_path=selected) == str(target)
    signal = read_mix(target, len(times))
    assert tone_amplitude(signal, 1000, 1.5) > .03
    assert tone_amplitude(signal, 2000, 1.5) < .001
    missing_output = tmp_path / "missing-selected.wav"
    assert mix_music_with_narration(voice, missing_output, .3, library_dir=folder,
                                    track_path=tmp_path / "deleted.wav") == str(voice)
    assert not missing_output.exists() and voice.read_bytes() == original


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for actual sound mixing")
def test_transition_effects_play_at_boundaries_without_looping_or_overlapping(tmp_path):
    rate = 48000
    times = np.arange(6 * rate) / rate
    voice, effect, target = (tmp_path / name for name in ("voice.wav", "effect.wav", "transitions.wav"))
    wav(voice, np.zeros(len(times)))
    # A four-second effect must be clipped to the next boundary, two seconds,
    # or the end of narration rather than run underneath the entire video.
    effect_times = np.arange(4 * rate) / rate
    wav(effect, .8 * np.sin(2 * np.pi * 1000 * effect_times))
    retained = {path: path.read_bytes() for path in (voice, effect)}
    result = mix_sound_effects(voice, target, effect, effect_volume=.4,
                              sections=[{"start": start} for start in (0, 1, 1.5, 4.5)])
    assert result == str(target)
    signal = read_mix(target, len(times))
    for start in (1.12, 1.7, 3.1, 4.8):
        assert tone_amplitude(signal, 1000, start) > .06
    for start in (.2, .7, 3.7, 4.2):
        assert tone_amplitude(signal, 1000, start) < .001
    # The first one-shot fades before the next boundary rather than stacking.
    assert tone_amplitude(signal, 1000, 1.45, .04) < tone_amplitude(signal, 1000, 1.12) * .5
    assert all(path.read_bytes() == original for path, original in retained.items())
    assert not list(tmp_path.glob(".sound-effects-*"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for actual sound mixing")
def test_ambience_loops_and_ducks_against_clean_voice_while_preserving_music(tmp_path):
    rate = 48000
    times = np.arange(6 * rate) / rate
    voice_samples = .45 * np.sin(2 * np.pi * 400 * times) * ((times >= 2) & (times < 4))
    music_samples = .35 * np.sin(2 * np.pi * 700 * times)
    voice, prepared, effect = (tmp_path / name for name in ("voice.wav", "prepared.wav", "ambience.wav"))
    wav(voice, voice_samples)
    wav(prepared, voice_samples + music_samples)
    effect_times = np.arange(rate) / rate
    wav(effect, .7 * np.sin(2 * np.pi * 1200 * effect_times))
    retained = {path: path.read_bytes() for path in (voice, prepared, effect)}
    signals = []
    for index, base in enumerate((voice, prepared)):
        target = tmp_path / f"ambience-mix-{index}.wav"
        assert mix_sound_effects(voice, target, effect, soundtrack_path=base,
                                 effect_volume=.5, mode="ambience") == str(target)
        signal = read_mix(target, len(times))
        quiet = tone_amplitude(signal, 1200, 1.45)
        during_speech = tone_amplitude(signal, 1200, 3.45)
        assert quiet > .04 and during_speech < quiet * .65
        assert tone_amplitude(signal, 1200, 4.45) > during_speech * 1.5
        signals.append(signal)
    # Existing music stays audible, but never becomes the ducking sidechain.
    assert tone_amplitude(signals[1], 700, 1.45) > .1
    for stamp in (1.45, 3.45):
        assert abs(tone_amplitude(signals[0], 1200, stamp) - tone_amplitude(signals[1], 1200, stamp)) < .002
    assert all(path.read_bytes() == original for path, original in retained.items())
    assert not list(tmp_path.glob(".sound-effects-*"))


@pytest.mark.parametrize("volume", [-.1, 1.1, float("nan"), True])
def test_invalid_effect_volume_is_rejected_before_writing(tmp_path, volume):
    with pytest.raises(ValueError, match="volume"):
        mix_sound_effects(tmp_path / "voice.wav", tmp_path / "mix.wav", tmp_path / "effect.wav", effect_volume=volume)
    assert not list(tmp_path.iterdir())


def test_unavailable_effect_and_no_transitions_preserve_existing_soundtrack(tmp_path):
    voice, prepared, effect = (tmp_path / name for name in ("voice.wav", "prepared.wav", "effect.wav"))
    wav(voice, np.zeros(48000))
    wav(prepared, np.ones(48000) * .1)
    wav(effect, np.ones(48000) * .2)
    target = tmp_path / "out.wav"
    for asset, sections in ((tmp_path / "deleted.wav", [{"start": .5}]), (effect, [{"start": 0}])):
        assert mix_sound_effects(voice, target, asset, soundtrack_path=prepared, sections=sections) == str(prepared)
    assert not target.exists()
