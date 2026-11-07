"""Actual sound mixing: preserve timing, duck the bed, and bound peaks."""
import shutil
import threading
import wave
from pathlib import Path

import numpy as np
import pytest

from video.music import mix_music_with_narration


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
    assert not list(tmp_path.iterdir())
