"""Measured soundtrack finishing with one constant gain, never dynamic pumping.

The clean narration owns the duration. FFmpeg measures integrated loudness and
true peak before a separate rendering pass; peak headroom and a bounded boost
take precedence over hitting the loudness target. ASMR retains its dynamics.
"""
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time


_PRESETS = {
    "general": {"lufs": -16.0, "true_peak": -1.5, "max_boost": 9.0},
    "news": {"lufs": -16.0, "true_peak": -1.5, "max_boost": 9.0},
    "asmr": {"lufs": -20.0, "true_peak": -2.0, "max_boost": 3.0},
}
_RATE = 48000
_PEAK_MARGIN = .1


def _run(command, stop_event=None, timeout=600):
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Stopped before audio mastering")
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Stopped while mastering audio")
                if time.monotonic() > deadline:
                    raise RuntimeError("Audio mastering took too long.")
                try:
                    process.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    pass
            errors.seek(max(0, errors.tell() - 65536))
            report = errors.read(65536).decode("utf-8", "replace")
            if process.returncode:
                raise RuntimeError("FFmpeg could not process the soundtrack.")
            return report
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


def _measure(ffmpeg, source, stop_event):
    raw = _run([ffmpeg, "-hide_banner", "-nostdin", "-v", "info", "-i", str(source), "-vn",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=50:print_format=json", "-f", "null", "-"], stop_event)
    matches = re.findall(r'\{\s*"input_i"\s*:[^{}]+\}', raw)
    if not matches:
        raise ValueError("The soundtrack loudness could not be measured.")
    values = json.loads(matches[-1])

    def number(key):
        value = float(values[key])
        return value if math.isfinite(value) else None

    return {"lufs": number("input_i"), "true_peak": number("input_tp"), "range": number("input_lra")}


def master_soundtrack(narration_path, prepared_path, output_path, preset="general", stop_event=None):
    """Return ``(soundtrack_path, measured_report)`` without altering source audio.

    General/news target -16 LUFS and -1.5 dBTP; ASMR targets -20 LUFS and
    -2 dBTP. Boost is limited to 9 dB (3 dB for ASMR). A target that cannot be
    reached with constant gain is reported honestly instead of compressing the
    dynamics. Silence or an unavailable analyser keeps the prepared soundtrack.
    """
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Stopped before audio mastering")
    if not isinstance(preset, str) or preset not in _PRESETS:
        raise ValueError("Choose general, news or asmr audio mastering.")
    narration, prepared = Path(narration_path).resolve(), Path(prepared_path).resolve()
    output = Path(output_path).resolve()
    if output in {narration, prepared}:
        raise ValueError("Keep original narration, prepared audio and mastered audio in separate files.")
    if output.suffix.lower() != ".wav":
        raise ValueError("Use a WAV output for audio mastering.")
    from video.timeline import probe_duration
    duration = probe_duration(str(narration))
    if not math.isfinite(duration) or not 0 < duration <= 7200:
        raise ValueError("Narration duration is outside the supported range.")
    if abs(probe_duration(str(prepared)) - duration) > .15:
        raise ValueError("The prepared soundtrack does not match the narration duration.")
    target = _PRESETS[preset]
    report = {"version": 1, "status": "unavailable", "preset": preset, "normalization": "constant_gain",
              "target_lufs": target["lufs"], "true_peak_limit_dbtp": target["true_peak"],
              "max_boost_db": target["max_boost"], "duration": duration, "sample_rate": _RATE,
              "input_lufs": None, "input_true_peak_dbtp": None, "output_lufs": None,
              "output_true_peak_dbtp": None, "applied_gain_db": 0.0, "warnings": []}
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        report["warnings"].append("FFmpeg is unavailable; the prepared soundtrack was kept without mastering.")
        return str(prepared), report
    try:
        measured = _measure(ffmpeg, prepared, stop_event)
        report.update(input_lufs=measured["lufs"], input_true_peak_dbtp=measured["true_peak"])
        if measured["true_peak"] is None or measured["true_peak"] < -90:
            report.update(status="silent", output_lufs=measured["lufs"], output_true_peak_dbtp=measured["true_peak"])
            return str(prepared), report
        requested = target["lufs"] - measured["lufs"] if measured["lufs"] is not None else 0.0
        headroom = target["true_peak"] - _PEAK_MARGIN - measured["true_peak"]
        gain = min(requested, target["max_boost"], headroom)
        reasons = []
        if requested > target["max_boost"]:
            reasons.append("boost_limit")
        if requested > headroom:
            reasons.append("true_peak_headroom")
        if measured["lufs"] is None:
            reasons.append("loudness_below_measurement_range")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".audio-master-", dir=output.parent) as work:
            temporary = Path(work) / "master.wav"

            def render(level):
                # Constant volume preserves the level difference between quiet
                # and loud passages; no compressor or dynamic loudnorm fallback.
                filters = (f"volume={level:.9f}dB,aresample={_RATE},apad,"
                           f"atrim=duration={duration:.12f},asetpts=PTS-STARTPTS")
                _run([ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error", "-i", str(prepared),
                      "-vn", "-af", filters, "-ar", str(_RATE), "-ac", "2", "-c:a", "pcm_s24le",
                      str(temporary)], stop_event)
                if abs(probe_duration(str(temporary)) - duration) > 1 / _RATE + 1e-9:
                    raise RuntimeError("Audio mastering changed the narration duration.")
                return _measure(ffmpeg, temporary, stop_event)

            final = render(gain)
            # Account for tiny resampling/quantization differences once, always
            # returning to the untouched input instead of processing a mix twice.
            if final["true_peak"] is not None and final["true_peak"] > target["true_peak"]:
                gain += target["true_peak"] - _PEAK_MARGIN - final["true_peak"]
                if "true_peak_headroom" not in reasons:
                    reasons.append("true_peak_headroom")
                final = render(gain)
            if final["true_peak"] is not None and final["true_peak"] > target["true_peak"] + .05:
                raise RuntimeError("The mastered soundtrack did not meet its true-peak limit.")
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("Stopped before saving mastered audio")
            os.replace(temporary, output)
        report.update(status="applied", output_lufs=final["lufs"], output_true_peak_dbtp=final["true_peak"],
                      applied_gain_db=round(gain, 6), limited_by=reasons)
        if final["lufs"] is None or abs(final["lufs"] - target["lufs"]) > .6:
            report["status"] = "target_shortfall"
            level = f"{final['lufs']:.1f} LUFS" if final["lufs"] is not None else "below the measurable loudness range"
            report["warnings"].append(
                f"Kept the soundtrack dynamics and peak headroom; finished at {level} instead of forcing {target['lufs']:g} LUFS.")
        return str(output), report
    except InterruptedError:
        raise
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        report["warnings"].append("Audio mastering could not be completed; the prepared soundtrack was kept.")
        return str(prepared), report
