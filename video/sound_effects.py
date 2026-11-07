"""Mix an explicitly selected local effect without changing the narration clock.

Transitions are short one-shots at section boundaries. Ambience is a quiet,
looping bed. The untouched narration controls ducking in both modes, including
when the input soundtrack already contains background music.
"""
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from video.music import SUPPORTED_EXTENSIONS, _run


def _transition_windows(sections, duration, effect_duration):
    if not isinstance(sections, (list, tuple)) or len(sections) > 80:
        raise ValueError("Sound effects need a bounded list of narration sections.")
    starts = set()
    for section in sections:
        start = section.get("start") if isinstance(section, dict) else None
        if (isinstance(start, bool) or not isinstance(start, (int, float))
                or not math.isfinite(start) or start < 0):
            raise ValueError("Sound-effect section boundaries must be measured nonnegative times.")
        if 0 < start < duration:
            starts.add(float(start))
    starts = sorted(starts)
    windows = []
    for index, start in enumerate(starts):
        next_start = starts[index + 1] if index + 1 < len(starts) else duration
        length = min(2.0, effect_duration, next_start - start, duration - start)
        if length >= .02:
            windows.append((start, length))
    return windows


def mix_sound_effects(narration_path, output_path, effect_path, *,
                      soundtrack_path=None, sections=(), effect_volume=.08,
                      mode="transitions", stop_event=None):
    """Return the applied WAV, or the unchanged soundtrack if the effect fails."""
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Stopped before mixing sound effects")
    if (isinstance(effect_volume, bool) or not isinstance(effect_volume, (int, float))
            or not math.isfinite(effect_volume) or not 0 <= effect_volume <= 1):
        raise ValueError("Sound-effect volume must be between 0 and 1.")
    if mode not in {"transitions", "ambience"}:
        raise ValueError("Choose transitions or ambience for sound effects.")
    narration = Path(narration_path).resolve()
    soundtrack = Path(soundtrack_path or narration).resolve()
    effect = Path(effect_path).resolve()
    output = Path(output_path).resolve()
    if output in {narration, soundtrack, effect}:
        raise ValueError("Keep the original narration, selected effect and mixed soundtrack in separate files.")
    if output.suffix.lower() != ".wav":
        raise ValueError("Use a WAV output for sound effects.")
    if effect_volume == 0:
        print("   → Sound effects are muted; preserving the prepared soundtrack.")
        return str(soundtrack)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not effect.is_file() or effect.suffix.lower() not in SUPPORTED_EXTENSIONS:
        print("   → Selected sound effect or FFmpeg is unavailable; preserving the prepared soundtrack.")
        return str(soundtrack)
    from video.timeline import probe_duration
    duration = probe_duration(str(narration))
    if abs(probe_duration(str(soundtrack)) - duration) > .08:
        raise ValueError("The prepared soundtrack does not match the original narration duration.")
    try:
        effect_duration = probe_duration(str(effect))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print("   → Selected sound effect is unreadable; preserving the prepared soundtrack.")
        return str(soundtrack)
    windows = _transition_windows(sections, duration, effect_duration) if mode == "transitions" else []
    if mode == "transitions" and not windows:
        print("   → No section transitions need a sound effect; preserving the prepared soundtrack.")
        return str(soundtrack)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".sound-effects-", dir=output.parent) as work:
        source = effect
        try:
            if mode == "ambience":
                source = Path(work) / "ambience.wav"
                length = min(effect_duration, duration)
                fade = min(.3, length / 4)
                filters = (f"atrim=duration={length:.9f},asetpts=PTS-STARTPTS,"
                           f"afade=t=in:d={fade:.6f},afade=t=out:st={length-fade:.6f}:d={fade:.6f}")
                _run([ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(effect), "-vn", "-af", filters,
                      "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(source)], stop_event)
            standard = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
            graph = [f"[0:a]{standard},apad,atrim=duration={duration:.9f},asetpts=PTS-STARTPTS[base]",
                     f"[1:a]{standard},apad,atrim=duration={duration:.9f},asetpts=PTS-STARTPTS[control]"]
            if mode == "ambience":
                fade_in, fade_out = min(1.5, duration / 5), min(2.5, duration / 5)
                graph.append(f"[2:a]{standard},volume={effect_volume:.6f},"
                             f"afade=t=in:d={fade_in:.6f},afade=t=out:st={duration-fade_out:.6f}:d={fade_out:.6f},"
                             f"atrim=duration={duration:.9f},asetpts=PTS-STARTPTS[effects]")
            else:
                count = len(windows)
                labels = "".join(f"[shot{index}]" for index in range(count))
                graph.append(f"[2:a]{standard},asplit={count}{labels}")
                for index, (start, length) in enumerate(windows):
                    fade_in, fade_out = min(.08, length / 5), min(.25, length / 4)
                    delay = round(start * 48000)
                    graph.append(f"[shot{index}]atrim=duration={length:.9f},asetpts=PTS-STARTPTS,"
                                 f"afade=t=in:d={fade_in:.6f},afade=t=out:st={length-fade_out:.6f}:d={fade_out:.6f},"
                                 f"volume={effect_volume:.6f},adelay={delay}S:all=1[event{index}]")
                events = "".join(f"[event{index}]" for index in range(count))
                graph.append(f"{events}amix=inputs={count}:duration=longest:normalize=0,"
                             f"apad,atrim=duration={duration:.9f}[effects]")
            graph.append("[effects][control]sidechaincompress=threshold=0.02:ratio=6:attack=20:release=350[ducked]")
            graph.append("[base][ducked]amix=inputs=2:duration=first:normalize=0,"
                         f"alimiter=limit=0.95:level=0:latency=1,apad,atrim=duration={duration:.9f},asetpts=PTS-STARTPTS[out]")
            temporary = Path(work) / "mixed.wav"
            effect_input = ["-stream_loop", "-1"] if mode == "ambience" else []
            _run([ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(soundtrack), "-i", str(narration),
                  *effect_input, "-i", str(source), "-filter_complex", ";".join(graph), "-map", "[out]",
                  "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(temporary)], stop_event)
            if abs(probe_duration(str(temporary)) - duration) > 1 / 48000 + 1e-9:
                raise RuntimeError("Sound effects changed the narration duration.")
            os.replace(temporary, output)
        except InterruptedError:
            raise
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"   → Sound-effect mix unavailable ({type(exc).__name__}); preserving the prepared soundtrack.")
            return str(soundtrack)
    print(f"   → {mode.capitalize()} from {effect.name}; speech ducking and peak limiting enabled.")
    return str(output)
