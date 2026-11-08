"""Audible automatic layers: level variance, cue onset and original preservation."""
import shutil
import threading
import wave

import numpy as np
import pytest

from video.audio_levels import prepare_automatic_audio
from video.music import mix_music_with_narration
from video.sound_effects import mix_sound_events

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Real FFmpeg required")
RATE = 48000


def write(path, signal):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes((np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes())


def read(path):
    with wave.open(str(path), "rb") as stream:
        assert stream.getframerate() == RATE
        result = np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").reshape(-1, stream.getnchannels())[:, 0] / 32768
    assert np.isfinite(result).all() and abs(result).max() <= .951
    return result


def component(signal, frequency, start, duration=.2):
    sample = signal[round(start * RATE):round((start + duration) * RATE)]
    return abs(np.mean(sample * np.exp(-2j * np.pi * frequency * np.arange(len(sample)) / RATE)))


def test_automatic_music_is_audible_and_consistent_for_different_source_levels(tmp_path):
    time = np.arange(5 * RATE) / RATE
    voice = tmp_path / "voice.wav"
    write(voice, .18 * np.sin(2 * np.pi * 220 * time))
    original_voice = voice.read_bytes()
    amplitudes = []
    for name, level in (("quiet", .008), ("loud", .5)):
        source, target = tmp_path / f"{name}.wav", tmp_path / f"{name}-mix.wav"
        write(source, level * np.sin(2 * np.pi * 880 * time))
        original_source = source.read_bytes()
        assert mix_music_with_narration(voice, target, music_volume=.20, track_path=source, auto_level=True) == str(target)
        signal = read(target)
        assert len(signal) == len(time)
        ratio = component(signal, 880, 2) / component(signal, 220, 2)
        assert .05 < ratio < .25, "The bed must remain audible beneath the unchanged voice"
        amplitudes.append(component(signal, 880, 2))
        assert source.read_bytes() == original_source
    assert max(amplitudes) / min(amplitudes) < 1.08
    assert voice.read_bytes() == original_voice


def test_automatic_effect_trims_leading_silence_before_placing_measured_cue(tmp_path):
    time = np.arange(5 * RATE) / RATE
    voice, effect, target = tmp_path / "voice.wav", tmp_path / "late-cue.wav", tmp_path / "mixed.wav"
    write(voice, .18 * np.sin(2 * np.pi * 220 * time))
    sound = np.arange(round(.4 * RATE)) / RATE
    write(effect, np.concatenate([np.zeros(round(1.5 * RATE)), .01 * np.sin(2 * np.pi * 1200 * sound)]))
    original_voice, original_effect = voice.read_bytes(), effect.read_bytes()
    assert mix_sound_events(voice, target, [{"path": effect, "start": 2.0, "duration": .3, "volume": .30}], auto_level=True) == str(target)
    signal = read(target)
    assert len(signal) == len(time)
    assert component(signal, 1200, 2.08, .1) > .004
    assert component(signal, 1200, 1.5) < .0001
    assert component(signal, 1200, 2.5) < .0001
    assert voice.read_bytes() == original_voice and effect.read_bytes() == original_effect


def test_silent_automatic_recording_is_not_reported_as_an_applied_layer(tmp_path):
    time = np.arange(3 * RATE) / RATE
    voice, silent, target = tmp_path / "voice.wav", tmp_path / "silent.wav", tmp_path / "mixed.wav"
    write(voice, .18 * np.sin(2 * np.pi * 220 * time))
    write(silent, np.zeros(len(time)))
    assert mix_music_with_narration(voice, target, .2, track_path=silent, auto_level=True) == str(voice)
    assert not target.exists()
    assert mix_sound_events(voice, target, [{"path": silent, "start": 1, "duration": .3, "volume": .3}], auto_level=True) == str(voice)
    assert not target.exists()


def test_cancelled_calibration_preserves_files(tmp_path):
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(InterruptedError):
        prepare_automatic_audio(tmp_path / "source.wav", tmp_path / "voice.wav", tmp_path / "prepared.wav", stop_event=stopped)
    assert not list(tmp_path.iterdir())
