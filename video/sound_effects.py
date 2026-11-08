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


def mix_sound_events(narration_path, output_path, events, *, soundtrack_path=None, stop_event=None, auto_level=False):
    """Apply planned one-shots from trusted local files, or keep the prepared mix.

    Events contain ``path``, ``start``, ``duration`` (at most two seconds), and
    ``volume``. Times are absolute narration seconds. Overlaps and duplicate
    starts are rejected so the plan cannot unexpectedly stack loud effects.
    Every selected asset must remain readable: a failed cue mix applies none of
    the events, allowing its caller to keep credits consistent with the audio.
    """
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Stopped before mixing planned sound effects")
    if not isinstance(events, (list, tuple)) or len(events) > 40:
        raise ValueError("Plan no more than 40 sound events.")
    narration = Path(narration_path).resolve()
    soundtrack = Path(soundtrack_path or narration).resolve()
    output = Path(output_path).resolve()
    if output in {narration, soundtrack}:
        raise ValueError("Keep original narration, prepared audio and sound effects in separate files.")
    if output.suffix.lower() != ".wav":
        raise ValueError("Use a WAV output for sound effects.")
    selected = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Every sound event needs a resolved local asset and measured timing.")
        for key, minimum, maximum in (("start", 0, 7200), ("duration", 0, 2), ("volume", 0, 1)):
            value = event.get(key)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not minimum <= value <= maximum
                    or (key == "duration" and value == 0)):
                raise ValueError(f"Sound-event {key} is outside the supported range.")
        path = event.get("path")
        if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
            raise ValueError("Every sound event needs a resolved local asset path.")
        path = Path(path).resolve()
        if output == path:
            raise ValueError("Keep selected sound assets and the mixed soundtrack in separate files.")
        selected.append({"path": path, "start": float(event["start"]),
                         "duration": float(event["duration"]), "volume": float(event["volume"])})
    selected.sort(key=lambda event: event["start"])
    for first, second in zip(selected, selected[1:]):
        if (second["start"] == first["start"]
                or second["start"] < first["start"] + first["duration"] - 1e-9):
            raise ValueError("Sound events must have distinct starts and must not overlap.")
    if not selected or not any(event["volume"] for event in selected):
        return str(soundtrack)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("   → FFmpeg is unavailable; preserving the prepared soundtrack without planned sound effects.")
        return str(soundtrack)
    from video.timeline import probe_duration
    duration = probe_duration(str(narration))
    if not math.isfinite(duration) or not 0 < duration <= 7200:
        raise ValueError("Narration duration is outside the supported range.")
    if abs(probe_duration(str(soundtrack)) - duration) > .08:
        raise ValueError("The prepared soundtrack does not match the original narration duration.")
    if any(event["start"] >= duration for event in selected):
        raise ValueError("Sound events must start within the narration.")
    sources = list(dict.fromkeys(event["path"] for event in selected))
    lengths = {}
    for source in sources:
        if not source.is_file() or source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            print("   → A planned sound asset is unavailable; preserving the prepared soundtrack without the cue mix.")
            return str(soundtrack)
        try:
            lengths[source] = probe_duration(str(source))
            if not math.isfinite(lengths[source]) or lengths[source] <= 0:
                raise ValueError("The sound asset has no audio.")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            print("   → A planned sound asset is unreadable; preserving the prepared soundtrack without the cue mix.")
            return str(soundtrack)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".sound-events-", dir=output.parent) as work:
        try:
            prepared_sources = {source: source for source in sources}
            if auto_level:
                from video.audio_levels import prepare_automatic_audio
                for index, source in enumerate(sources):
                    prepared = Path(work) / f"calibrated-effect-{index}.wav"
                    level = prepare_automatic_audio(source, narration, prepared, transient=True,
                                                    duration_limit=2, stop_event=stop_event)
                    prepared_sources[source] = prepared
                    lengths[source] = level["duration"]
            standard = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
            graph = [f"[0:a]{standard},apad,atrim=duration={duration:.12f},asetpts=PTS-STARTPTS[base]",
                     f"[1:a]{standard},apad,atrim=duration={duration:.12f},asetpts=PTS-STARTPTS[control]"]
            inputs = []
            for index, source in enumerate(sources):
                indices = [i for i, event in enumerate(selected) if event["path"] == source]
                labels = "".join(f"[shot{i}]" for i in indices)
                graph.append(f"[{index+2}:a]{standard},asplit={len(indices)}{labels}")
                inputs.extend(["-t", "2", "-i", str(prepared_sources[source])])
            for index, event in enumerate(selected):
                length = min(event["duration"], lengths[event["path"]], duration - event["start"])
                fade_in, fade_out = min(.08, length / 5), min(.25, length / 4)
                delay = round(event["start"] * 48000)
                graph.append(f"[shot{index}]atrim=duration={length:.12f},asetpts=PTS-STARTPTS,"
                             f"afade=t=in:d={fade_in:.9f},afade=t=out:st={length-fade_out:.9f}:d={fade_out:.9f},"
                             f"volume={event['volume']:.9f},adelay={delay}S:all=1[event{index}]")
            labels = "".join(f"[event{index}]" for index in range(len(selected)))
            graph.append(f"{labels}amix=inputs={len(selected)}:duration=longest:normalize=0,"
                         f"apad,atrim=duration={duration:.12f}[effects]")
            ducking = ("sidechaincompress=threshold=0.04:ratio=2:attack=15:release=220:mix=0.45"
                       if auto_level else "sidechaincompress=threshold=0.02:ratio=6:attack=20:release=350")
            graph.append(f"[effects][control]{ducking}[ducked]")
            graph.append("[base][ducked]amix=inputs=2:duration=first:normalize=0,"
                         f"alimiter=limit=0.95:level=0:latency=1,apad,atrim=duration={duration:.12f},asetpts=PTS-STARTPTS[out]")
            temporary = Path(work) / "events.wav"
            _run([ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(soundtrack), "-i", str(narration),
                  *inputs, "-filter_complex", ";".join(graph), "-map", "[out]",
                  "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(temporary)], stop_event)
            if abs(probe_duration(str(temporary)) - duration) > 1 / 48000 + 1e-9:
                raise RuntimeError("Planned sound effects changed the narration duration.")
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("Stopped before saving planned sound effects")
            os.replace(temporary, output)
        except InterruptedError:
            raise
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"   → Planned sound-effect mix unavailable ({type(exc).__name__}); preserving the prepared soundtrack.")
            return str(soundtrack)
    print(f"   → Applied {len(selected)} planned sound events with speech ducking and peak limiting.")
    return str(output)


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
                      mode="transitions", stop_event=None, auto_level=False):
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
            if auto_level:
                from video.audio_levels import prepare_automatic_audio
                source = Path(work) / "calibrated-effect.wav"
                level = prepare_automatic_audio(effect, narration, source, transient=mode == "transitions",
                                                duration_limit=2 if mode == "transitions" else min(600, duration),
                                                stop_event=stop_event)
                effect_duration = level["duration"]
                if mode == "transitions":
                    windows = _transition_windows(sections, duration, effect_duration)
            if mode == "ambience":
                original = source
                source = Path(work) / "ambience.wav"
                length = min(effect_duration, duration)
                fade = min(.3, length / 4)
                filters = (f"atrim=duration={length:.9f},asetpts=PTS-STARTPTS,"
                           f"afade=t=in:d={fade:.6f},afade=t=out:st={length-fade:.6f}:d={fade:.6f}")
                _run([ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(original), "-vn", "-af", filters,
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
            ducking = (f"sidechaincompress=threshold=0.04:ratio=2:attack=20:release=300:mix={0.65 if mode == 'ambience' else 0.45}"
                       if auto_level else "sidechaincompress=threshold=0.02:ratio=6:attack=20:release=350")
            graph.append(f"[effects][control]{ducking}[ducked]")
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
