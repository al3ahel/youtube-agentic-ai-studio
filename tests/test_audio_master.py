"""Real audio finishing: measured level, preserved dynamics and narration clock."""
import json
import shutil
import subprocess
import threading
import wave

import numpy as np
import pytest

from video.audio_master import master_soundtrack


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for audio finishing")
RATE = 48000


def write_wav(path, signal):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes((np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes())


def read_audio(path):
    result = subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-i", str(path),
                             "-f", "f32le", "-ac", "2", "-ar", str(RATE), "-"],
                            capture_output=True, check=True, timeout=30)
    return np.frombuffer(result.stdout, dtype="<f4").reshape(-1, 2)


def rms(signal):
    return float(np.sqrt(np.mean(signal ** 2)))


@pytest.mark.parametrize("preset,target,ceiling", [("general", -16, -1.5), ("news", -16, -1.5), ("asmr", -20, -2)])
def test_mastering_measures_level_preserves_duration_and_originals(tmp_path, preset, target, ceiling):
    times = np.arange(RATE * 4 + 137) / RATE
    voice, prepared, output = (tmp_path / name for name in ("voice.wav", "prepared.wav", "master.wav"))
    write_wav(voice, .1 * np.sin(2 * np.pi * 400 * times))
    write_wav(prepared, .2 * np.sin(2 * np.pi * 1000 * times))
    originals = {path: path.read_bytes() for path in (voice, prepared)}
    path, report = master_soundtrack(voice, prepared, output, preset)
    assert path == str(output)
    assert report["status"] == "applied"
    assert report["normalization"] == "constant_gain"
    assert abs(report["output_lufs"] - target) < .3
    assert report["output_true_peak_dbtp"] <= ceiling
    samples = read_audio(output)
    assert len(samples) == len(times)
    assert np.isfinite(samples).all()
    assert max(abs(samples).max(), 0) <= 10 ** (ceiling / 20)
    assert all(path.read_bytes() == original for path, original in originals.items())
    assert json.loads(json.dumps(report, allow_nan=False))["preset"] == preset
    assert not list(tmp_path.glob(".audio-master-*"))


def test_asmr_keeps_quiet_loud_relationship_and_bounds_boost(tmp_path):
    times = np.arange(8 * RATE) / RATE
    envelope = np.where(times < 4, .006, .024)
    original = envelope * np.sin(2 * np.pi * 1000 * times)
    voice, output = tmp_path / "whisper.wav", tmp_path / "master.wav"
    write_wav(voice, original)
    path, report = master_soundtrack(voice, voice, output, "asmr")
    assert path == str(output)
    assert report["status"] == "target_shortfall"
    assert report["applied_gain_db"] == 3
    assert "boost_limit" in report["limited_by"]
    assert report["output_lufs"] < -20
    signal = read_audio(output)[:, 0]
    original_signal = read_audio(voice)[:, 0]
    quiet, loud = slice(RATE, 3 * RATE), slice(5 * RATE, 7 * RATE)
    assert rms(signal[loud]) / rms(signal[quiet]) == pytest.approx(
        rms(original_signal[loud]) / rms(original_signal[quiet]), rel=.002)
    assert rms(signal[quiet]) / rms(original_signal[quiet]) == pytest.approx(10 ** (3 / 20), rel=.002)
    assert report["warnings"]


def test_transient_peak_headroom_wins_over_integrated_loudness(tmp_path):
    times = np.arange(6 * RATE) / RATE
    signal = .01 * np.sin(2 * np.pi * 700 * times)
    signal[3 * RATE:3 * RATE + 240] = .98 * np.sin(2 * np.pi * 700 * times[:240])
    voice, output = tmp_path / "peaky.wav", tmp_path / "master.wav"
    write_wav(voice, signal)
    path, report = master_soundtrack(voice, voice, output)
    assert path == str(output)
    assert report["status"] == "target_shortfall"
    assert "true_peak_headroom" in report["limited_by"]
    assert report["output_true_peak_dbtp"] <= -1.5
    assert len(read_audio(output)) == len(signal)


def test_silence_stays_silent_without_amplifying_or_nan_report(tmp_path):
    voice, output = tmp_path / "silence.wav", tmp_path / "unused.wav"
    write_wav(voice, np.zeros(RATE))
    before = voice.read_bytes()
    path, report = master_soundtrack(voice, voice, output)
    assert path == str(voice) and report["status"] == "silent"
    assert report["applied_gain_db"] == 0 and report["input_lufs"] is None
    json.dumps(report, allow_nan=False)
    assert voice.read_bytes() == before and not output.exists()


def test_failed_or_cancelled_master_keeps_prepared_and_previous_success(tmp_path, monkeypatch):
    from video import audio_master
    voice, output = tmp_path / "voice.wav", tmp_path / "master.wav"
    write_wav(voice, np.ones(RATE) * .05)
    output.write_bytes(b"previous successful master")
    original = voice.read_bytes()
    monkeypatch.setattr(audio_master, "_measure", lambda *args: {"lufs": -24., "true_peak": -21., "range": 2.})
    def unavailable(*args, **kwargs):
        raise RuntimeError("fixture failure")
    monkeypatch.setattr(audio_master, "_run", unavailable)
    path, report = master_soundtrack(voice, voice, output)
    assert path == str(voice) and report["status"] == "unavailable"
    assert report["warnings"]
    event = threading.Event()
    event.set()
    with pytest.raises(InterruptedError):
        master_soundtrack(voice, voice, output, stop_event=event)
    assert output.read_bytes() == b"previous successful master" and voice.read_bytes() == original
    assert not list(tmp_path.glob(".audio-master-*"))


def test_master_rejects_mismatched_clock_or_overwriting_sources(tmp_path):
    voice, other = tmp_path / "voice.wav", tmp_path / "other.wav"
    write_wav(voice, np.zeros(RATE))
    write_wav(other, np.zeros(RATE * 2))
    with pytest.raises(ValueError, match="duration"):
        master_soundtrack(voice, other, tmp_path / "master.wav")
    with pytest.raises(ValueError, match="separate files"):
        master_soundtrack(voice, voice, voice)
