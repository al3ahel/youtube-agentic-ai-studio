"""One measured clock for narration, scenes, captions, and chapter markers.

Times at the renderer boundary are quantized from absolute audio positions,
never by repeatedly adding rounded scene lengths. No transition consumes time
from this clock and no narrator audio is shortened to fit a picture sequence.
"""
import hashlib
import json
import math
import re
import shutil
import subprocess
import wave
from pathlib import Path


def script_signature(script: dict) -> str:
    spoken = [{"id": str(s.get("id", i + 1)),
               "narration": str(s.get("narration", "")).strip()}
              for i, s in enumerate(script.get("sections", []))]
    return hashlib.sha256(json.dumps(spoken, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")).hexdigest()


def audio_signature(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as audio:
        for block in iter(lambda: audio.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe_duration(path: str) -> float:
    """Measure an audio/video file, rejecting a missing or unmeasurable file."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Media file is missing: {source.name}")
    if source.suffix.lower() == ".wav":
        try:
            with wave.open(str(source), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
            if duration > 0:
                return duration
        except (wave.Error, EOFError):
            pass
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(source.resolve())],
            capture_output=True, text=True, timeout=30, check=True)
        try:
            duration = float(json.loads(result.stdout)["format"]["duration"])
            if math.isfinite(duration) and duration > 0:
                return duration
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            pass
    # The classic renderer already supplies this decoder. Never guess using
    # file size: compressed bitrate is not a timing source.
    try:
        from moviepy.editor import AudioFileClip
        with AudioFileClip(str(source)) as audio:
            duration = float(audio.duration)
        if math.isfinite(duration) and duration > 0:
            return duration
    except (ImportError, OSError, ValueError):
        pass
    raise ValueError(f"Cannot measure {source.name}. Install FFmpeg and ffprobe, then regenerate narration.")


def read_srt(path: str, duration: float | None = None) -> list:
    """Read real cue positions without inventing per-word timestamps."""
    source = Path(path)
    if not source.is_file():
        return []
    content = source.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    stamp = r"(\d+):(\d{2}):(\d{2})[,.](\d{3})"
    pattern = re.compile(stamp + r"\s*-->\s*" + stamp + r"[^\n]*\n(.*?)(?=\n\s*\n|\Z)", re.S)
    cues = []
    for match in pattern.finditer(content):
        values = match.groups()
        def seconds(start):
            h, m, s, ms = map(int, values[start:start + 4])
            return h * 3600 + m * 60 + s + ms / 1000
        start, end = max(0.0, seconds(0)), seconds(4)
        if duration is not None:
            end = min(end, duration)
        text = " ".join(values[8].split())
        if text and end > start:
            cues.append({"start": start, "end": end, "text": text})
    return sorted(cues, key=lambda cue: cue["start"])


def build_timeline(script: dict, audio_path: str, output_dir: str,
                   video_type: str = "normal") -> dict:
    """Use the measured section manifest produced by segmented narration.

    Legacy voiceovers without measured section boundaries are deliberately not
    relabeled as synchronized. Regenerating narration creates the required file.
    """
    import config
    duration = probe_duration(audio_path)
    path = Path(output_dir) / "narration.timings.json"
    try:
        timing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Narration timings are missing. Run the Narration step to enable synchronized motion.") from exc
    if timing.get("script_signature") != script_signature(script):
        raise ValueError("The script changed after narration. Run the Narration step again before rendering.")
    recorded_duration = float(timing.get("duration", 0))
    if not math.isfinite(recorded_duration) or abs(recorded_duration - duration) > .15:
        raise ValueError("Narration audio and timings do not match. Regenerate narration.")
    audio_hash = audio_signature(audio_path)
    if timing.get("audio_sha256") and timing["audio_sha256"] != audio_hash:
        raise ValueError("Narration audio changed after timing was measured. Regenerate narration.")
    portrait = video_type == "shorts" or script.get("video_type") == "shorts"
    fps = int(config.SHORTS_FPS if portrait else config.VIDEO_FPS)
    if not 1 <= fps <= 60:
        raise ValueError("Video frame rate must be between 1 and 60.")
    total_frames = math.ceil(duration * fps - 1e-8)
    script_sections = script.get("sections", [])
    measured = timing.get("sections", [])
    if not measured or len(measured) != len(script_sections):
        raise ValueError("Narration section timings are incomplete. Regenerate narration.")
    sections, previous_frame = [], 0
    for index, (section, audio_section) in enumerate(zip(script_sections, measured)):
        sid = section.get("id", index + 1)
        if str(audio_section.get("id")) != str(sid):
            raise ValueError("Narration section order differs from the script. Regenerate narration.")
        start = float(audio_section["start"])
        end = start + float(audio_section["duration"])
        if not all(math.isfinite(v) for v in (start, end)) or end <= start:
            raise ValueError("Invalid narration section timing.")
        expected_start = 0 if index == 0 else float(measured[index - 1]["start"]) + float(measured[index - 1]["duration"])
        if abs(start - expected_start) > .002:
            raise ValueError("Narration section timings contain a gap or overlap.")
        end_frame = total_frames if index == len(measured) - 1 else round(end * fps)
        if end_frame <= previous_frame or end_frame > total_frames:
            raise ValueError("A narration section is too short for the selected frame rate.")
        sections.append({
            "id": sid, "title": str(section.get("title", "")),
            "narration": str(section.get("narration", "")),
            "start": previous_frame / fps,
            "duration": (end_frame - previous_frame) / fps,
            "start_frame": previous_frame, "duration_frames": end_frame - previous_frame,
            "speech_start": start, "speech_end": min(end, duration),
            "caption_timing": audio_section.get("caption_timing", "estimated"),
        })
        previous_frame = end_frame
    if abs(end - duration) > .002:
        raise ValueError("Section timings do not cover the complete narration.")
    timeline = {
        "version": 1, "title": str(script.get("title", "")), "audio_sha256": audio_hash,
        "script_signature": script_signature(script), "fps": fps,
        "width": int(config.SHORTS_WIDTH if portrait else config.VIDEO_WIDTH),
        "height": int(config.SHORTS_HEIGHT if portrait else config.VIDEO_HEIGHT),
        "duration": total_frames / fps, "audio_duration": duration,
        "total_frames": total_frames, "timing_source": "measured_sections",
        "sections": sections, "words": timing.get("words", []),
        "captions": read_srt(str(Path(output_dir) / "narration.srt"), duration),
    }
    # A same-length voice replacement or edited caption file must not silently
    # reuse an old storyboard. The hash contains timing as well as audio identity.
    signature_fields = {k: timeline[k] for k in ("script_signature", "audio_sha256", "fps",
                        "width", "height", "sections", "captions", "words", "total_frames")}
    timeline["timeline_signature"] = hashlib.sha256(json.dumps(
        signature_fields, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    target = Path(output_dir) / "timeline.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return timeline
