"""Calibrate automatic layers to the measured voice before applying their gain.

Library recordings have unrelated mastering levels. A fixed percentage followed
by heavy ducking can turn a quiet but valid file into an inaudible layer. This
module preserves source files, bounds gain, and removes only leading silence
from one-shot effects so the scheduled cue contains the actual sound.
"""
import math
from pathlib import Path
import re
import shutil
import tempfile


def measure_audio_level(path, *, stop_event=None):
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Stopped before measuring automatic audio")
    from video.music import _run
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to measure automatic audio.")
    output = _run([ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", str(path),
                   "-vn", "-sn", "-dn", "-af", "volumedetect", "-f", "null", "-"],
                  stop_event, capture=True)
    result = {}
    for field in ("mean_volume", "max_volume"):
        values = re.findall(r"\b" + field + r":\s*(-?(?:inf|[\d.]+))\s*dB", output)
        if not values:
            raise ValueError("The selected recording has no measurable audio level.")
        result[field] = float(values[-1])
    return result


def prepare_automatic_audio(source, narration, destination, *, transient=False,
                            duration_limit=600, stop_event=None):
    """Write a bounded, voice-relative PCM layer and return measured diagnostics.

    The caller still applies the director's volume and speech ducking. These
    gains never apply to Manual sound, the original voice, or stored assets.
    """
    from video.music import _run
    from video.timeline import probe_duration
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Stopped before preparing automatic audio")
    source, narration, destination = (Path(value).resolve() for value in (source, narration, destination))
    if destination in {source, narration}:
        raise ValueError("Keep audio calibration separate from original recordings.")
    if (isinstance(duration_limit, bool) or not isinstance(duration_limit, (int, float))
            or not math.isfinite(duration_limit) or not 0 < duration_limit <= 7200):
        raise ValueError("Automatic audio needs a bounded duration.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to prepare automatic audio.")
    voice = measure_audio_level(narration, stop_event=stop_event)
    # Long pauses must not make the entire background inaudible; a silent
    # authoring preview uses a conservative reference rather than infinite gain.
    target = max(-30.0, min(-18.0, voice["mean_volume"])) if math.isfinite(voice["mean_volume"]) else -24.0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".auto-level-", dir=destination.parent) as work:
        clip = Path(work) / "clip.wav"
        filters = []
        if transient:
            original = measure_audio_level(source, stop_event=stop_event)
            if not math.isfinite(original["max_volume"]) or original["max_volume"] < -70:
                raise ValueError("The selected effect contains no usable audible signal.")
            threshold = max(-75.0, min(-35.0, original["max_volume"] - 35.0))
            filters.append(f"silenceremove=start_periods=1:start_duration=0.008:start_threshold={threshold:.3f}dB:start_silence=0.008:detection=rms:window=0.005")
        filters.extend([f"atrim=duration={duration_limit:.9f}", "asetpts=PTS-STARTPTS"])
        _run([ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(source),
              "-vn", "-af", ",".join(filters), "-ar", "48000", "-ac", "2",
              "-c:a", "pcm_s16le", str(clip)], stop_event)
        level = measure_audio_level(clip, stop_event=stop_event)
        if (not all(math.isfinite(value) for value in level.values())
                or level["max_volume"] < -70 or level["mean_volume"] < -75):
            raise ValueError("The selected recording contains no usable audible signal.")
        # Protect transients as well as limiting the maximum boost to 30 dB.
        gain = max(-36.0, min(30.0, target - level["mean_volume"], -1.0 - level["max_volume"]))
        _run([ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(clip),
              "-af", f"volume={gain:.6f}dB", "-ar", "48000", "-ac", "2",
              "-c:a", "pcm_s16le", str(destination)], stop_event)
    return {"source_mean_dbfs": level["mean_volume"], "source_peak_dbfs": level["max_volume"],
            "voice_mean_dbfs": voice["mean_volume"], "gain_db": gain,
            "prepared_mean_dbfs": level["mean_volume"] + gain,
            "prepared_peak_dbfs": level["max_volume"] + gain,
            "duration": probe_duration(str(destination)), "trimmed_leading_silence": bool(transient)}
