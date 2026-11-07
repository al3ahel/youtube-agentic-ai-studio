"""Optional music bed with gentle fades, speech ducking and peak limiting.

Missing music preserves the original narration, never WAV bytes under an MP3
filename. Motion uses a lossless WAV soundtrack. Selected tracks are exact.
"""
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import time

SUPPORTED_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a", ".flac")


def _get_library_tracks(library_dir):
    root = Path(library_dir) if library_dir else None
    if root is None or not root.is_dir():
        return []
    tracks = [str(item.resolve()) for item in root.iterdir()
              if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS]
    random.shuffle(tracks)
    return tracks


def _run(command, stop_event=None):
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
        deadline = time.monotonic() + 600
        try:
            while process.poll() is None:
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Stopped while mixing audio")
                if time.monotonic() > deadline:
                    raise RuntimeError("Audio mixing took too long.")
                try:
                    process.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode:
                errors.seek(0)
                raise RuntimeError(errors.read(2000).decode("utf-8", "replace"))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


def mix_music_with_narration(narration_path, output_path, music_volume=.08,
                              library_dir="", stop_event=None, *, track_path=None):
    """Return a mixed soundtrack, or the original speech if music is unavailable."""
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Stopped before mixing audio")
    if isinstance(music_volume, bool) or not isinstance(music_volume, (int, float)) or not math.isfinite(music_volume) or not 0 <= music_volume <= 1:
        raise ValueError("Music volume must be between 0 and 1.")
    narration = Path(narration_path).resolve()
    output = Path(output_path).resolve()
    if narration == output:
        raise ValueError("Keep the original narration and mixed soundtrack in separate files.")
    if output.suffix.lower() not in {".wav", ".mp3"}:
        raise ValueError("Use a WAV or MP3 output for the soundtrack.")
    if track_path is not None:
        selected = Path(track_path).resolve()
        if selected == output:
            raise ValueError("Keep the selected music and mixed soundtrack in separate files.")
        tracks = [str(selected)] if selected.is_file() and selected.suffix.lower() in SUPPORTED_EXTENSIONS else []
    else:
        tracks = _get_library_tracks(library_dir)
    if not tracks or music_volume == 0:
        print("   → Music is unavailable or muted; preserving the original narration.")
        return str(narration)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("   → FFmpeg is unavailable for music mixing; preserving narration.")
        return str(narration)
    from video.timeline import probe_duration
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(str(narration))
    if duration <= 0:
        raise ValueError("Narration has no measurable duration.")
    with tempfile.TemporaryDirectory(prefix=".soundtrack-", dir=output.parent) as work:
        bed = Path(work) / "bed.wav"
        track = None
        for candidate in tracks[:12]:
            try:
                length = min(probe_duration(candidate), duration)
                if length <= .1:
                    continue
                fade = min(.3, length / 4)
                # Soften loop seams. One stable track avoids abrupt music changes.
                filters = f"atrim=duration={length:.9f},asetpts=PTS-STARTPTS,afade=t=in:d={fade:.6f},afade=t=out:st={length-fade:.6f}:d={fade:.6f}"
                _run([ffmpeg, "-nostdin", "-y", "-v", "error", "-i", candidate,
                      "-vn", "-af", filters, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(bed)], stop_event)
                track = candidate
                break
            except InterruptedError:
                raise
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                continue
        if track is None:
            print("   → No readable music track; preserving original narration.")
            return str(narration)
        fade_in, fade_out = min(1.5, duration / 5), min(2.5, duration / 5)
        graph = (
            "[0:a]aresample=48000,asplit=2[voice][control];"
            f"[1:a]aresample=48000,volume={music_volume:.6f},"
            f"afade=t=in:d={fade_in:.6f},afade=t=out:st={duration-fade_out:.6f}:d={fade_out:.6f}[bed];"
            "[bed][control]sidechaincompress=threshold=0.02:ratio=6:attack=30:release=450[ducked];"
            "[voice][ducked]amix=inputs=2:duration=first:normalize=0,"
            f"alimiter=limit=0.95:level=0:latency=1,apad,atrim=duration={duration:.9f},asetpts=PTS-STARTPTS[out]"
        )
        temporary = Path(work) / ("mixed" + output.suffix.lower())
        codec = ["-c:a", "pcm_s16le"] if output.suffix.lower() == ".wav" else ["-c:a", "libmp3lame", "-b:a", "192k"]
        try:
            _run([ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(narration),
                  "-stream_loop", "-1", "-i", str(bed), "-filter_complex", graph,
                  "-map", "[out]", "-ar", "48000", "-ac", "2", *codec, str(temporary)], stop_event)
            tolerance = 1 / 48000 + 1e-9 if output.suffix.lower() == ".wav" else .08
            if abs(probe_duration(str(temporary)) - duration) > tolerance:
                raise RuntimeError("Soundtrack length changed during mixing.")
            os.replace(temporary, output)
        except InterruptedError:
            raise
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"   → Music mix unavailable ({type(exc).__name__}); preserving narration.")
            return str(narration)
    print(f"   → Quiet music bed from {Path(track).name}; speech ducking and peak limiting enabled.")
    return str(output)
