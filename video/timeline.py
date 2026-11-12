"""One measured clock for narration, scenes, captions, and chapter markers.

Times at the renderer boundary are quantized from absolute audio positions,
never by repeatedly adding rounded scene lengths. No transition consumes time
from this clock and no narrator audio is shortened to fit a picture sequence.
"""
import hashlib
import html
import json
import math
import re
import shutil
import subprocess
import wave
from difflib import SequenceMatcher
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=True)
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
    raise ValueError(f"Cannot measure {source.name}. Install FFmpeg and ffprobe, then retry with the existing narration.")


def read_srt(path: str, duration: float | None = None) -> list:
    """Read real cue positions without inventing per-word timestamps."""
    source = Path(path)
    if not source.is_file():
        return []
    raw = source.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    content = raw.decode(encoding).replace("\r\n", "\n")
    stamp = r"(\d+):(\d{2}):(\d{2})[,.](\d{3})"
    pattern = re.compile(stamp + r"\s*-->\s*" + stamp + r"[^\n]*\n(.*?)(?=\n\s*\n|\Z)", re.S)
    cues = []
    for match in pattern.finditer(content):
        values = match.groups()
        def seconds(start):
            h, m, s, ms = map(int, values[start:start + 4])
            if m >= 60 or s >= 60:
                return -1
            return h * 3600 + m * 60 + s + ms / 1000
        start, end = seconds(0), seconds(4)
        if start < 0 or end < 0:
            continue
        if duration is not None:
            end = min(end, duration)
        text = " ".join(values[8].split())
        if text and end > start:
            cues.append({"start": start, "end": end, "text": text})
    return sorted(cues, key=lambda cue: cue["start"])


def build_timeline(script: dict, audio_path: str, output_dir: str,
                   video_type: str = "normal") -> dict:
    """Use the measured section manifest produced by segmented narration.

    This strict path validates existing measured metadata. Legacy voiceovers
    use import_legacy_timeline, which keeps their timing provenance explicit.
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


def _spoken_tokens(text: str) -> list[str]:
    """Comparison tokens only; their positions are never exposed as word times."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(text))).casefold()
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text, flags=re.UNICODE)


def _legacy_boundaries(script_sections: list[dict], captions: list[dict], duration: float):
    """Infer section boundaries from matching cues, interpolating missing anchors.

    A cue can span two script sections. Its internal boundary is estimated by
    text position, not misrepresented as a measured word event. Sparse or
    unrelated subtitle text never overrides duration-based section estimates.
    """
    token_rows = [_spoken_tokens(section.get("narration", "")) for section in script_sections]
    weights = [max(1, len(tokens)) for tokens in token_rows]
    script_tokens, offsets = [], [0]
    for tokens in token_rows:
        script_tokens.extend(tokens)
        offsets.append(len(script_tokens))
    cue_tokens, positions = [], []
    for cue_index, cue in enumerate(captions):
        tokens = _spoken_tokens(cue["text"])
        for offset, token in enumerate(tokens):
            cue_tokens.append(token)
            positions.append((cue_index, offset, len(tokens)))
    anchors = {0: 0.0, len(script_sections): duration}
    sources = {0: "audio_start", len(script_sections): "audio_end"}
    matched = {}
    # Bound work on imported files. Very large/unrelated scripts still render
    # safely with explicitly estimated timings rather than blocking the UI.
    if script_tokens and cue_tokens and max(len(script_tokens), len(cue_tokens)) <= 20000:
        matcher = SequenceMatcher(None, script_tokens, cue_tokens, autojunk=True)
        for block in matcher.get_matching_blocks():
            for offset in range(block.size):
                matched[block.a + offset] = block.b + offset
    matching_cues = bool(matched) and len(matched) / max(len(script_tokens), len(cue_tokens), 1) >= .65
    if matching_cues:
        previous = 0.0
        for index in range(1, len(script_sections)):
            start, end = offsets[index], offsets[index + 1]
            found = [position for position in range(start, end) if position in matched]
            if not found or len(found) / max(1, end - start) < .6 or found[0] - start > 2:
                continue
            cue_index, token_offset, token_count = positions[matched[found[0]]]
            cue = captions[cue_index]
            boundary = cue["start"]
            source = "subtitle_cue"
            if token_offset:
                # A caption crosses a section boundary: interpolation is an
                # estimate inside that phrase, never an exact word timestamp.
                boundary += (cue["end"] - cue["start"]) * token_offset / token_count
                source = "subtitle_interpolated"
            if previous < boundary < duration:
                anchors[index], sources[index] = boundary, source
                previous = boundary
    boundaries = [0.0] * (len(script_sections) + 1)
    ordered = sorted(anchors)
    for left, right in zip(ordered, ordered[1:]):
        span_weight = sum(weights[left:right])
        elapsed_weight = 0
        for index in range(left, right + 1):
            boundaries[index] = anchors[left] + (anchors[right] - anchors[left]) * elapsed_weight / span_weight
            if index < right:
                elapsed_weight += weights[index]
    return boundaries, sources, matching_cues


def _check_previous_import(path: Path, current_audio_hash: str, current_script_hash: str) -> None:
    """Keep a saved recording bound to the narration text it was imported with."""
    if not path.exists() and not path.is_symlink():
        return
    guidance = ("The saved narration timeline is unreadable or lacks a valid recording identity. "
                "Restore the original timeline and matching script, or run the Narration step to regenerate narration.")
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(guidance) from exc
    if not isinstance(previous, dict):
        raise ValueError(guidance)
    old_audio = previous.get("audio_sha256")
    if not isinstance(old_audio, str) or not re.fullmatch(r"[0-9a-f]{64}", old_audio):
        raise ValueError(guidance)
    # A different recording is a new import. Its previous script association
    # does not constrain the current narration text, even at the same duration.
    if old_audio != current_audio_hash:
        return
    old_script = previous.get("script_signature")
    if not isinstance(old_script, str) or not re.fullmatch(r"[0-9a-f]{64}", old_script):
        raise ValueError(guidance)
    if old_script != current_script_hash:
        raise ValueError(
            "The script's narration changed after this recording was imported. "
            "Restore the script that matches the existing audio, or run the Narration step to regenerate narration."
        )


def import_legacy_timeline(script: dict, audio_path: str, output_dir: str,
                           video_type: str = "normal", srt_path: str | None = None) -> dict:
    """Reuse an existing voiceover without re-recording or claiming word accuracy.

    Existing SRT timestamps remain intact. Script section boundaries are inferred
    from matching subtitle text when possible, otherwise estimated proportionally
    from the measured audio length. No pseudo word timestamps are generated. If a
    measured manifest exists, its normal identity checks remain mandatory. An
    already imported recording cannot silently be paired with changed narration.
    """
    import config

    output = Path(output_dir)
    if (output / "narration.timings.json").exists():
        return build_timeline(script, audio_path, output_dir, video_type)
    duration = probe_duration(audio_path)
    if duration > 7200:
        raise ValueError("Motion narration must be no longer than two hours.")
    original_sections = script.get("sections", [])
    if not isinstance(original_sections, list) or not 1 <= len(original_sections) <= 80:
        raise ValueError("Legacy narration needs one to eighty script sections.")
    if any(not isinstance(section, dict) for section in original_sections):
        raise ValueError("Each legacy script section must be an object.")
    ids = [str(section.get("id", index + 1)) for index, section in enumerate(original_sections)]
    if len(set(ids)) != len(ids):
        raise ValueError("Script section IDs must be unique.")
    current_audio_hash = audio_signature(audio_path)
    current_script_hash = script_signature(script)
    _check_previous_import(output / "timeline.json", current_audio_hash, current_script_hash)
    portrait = video_type == "shorts" or script.get("video_type") == "shorts"
    fps = int(config.SHORTS_FPS if portrait else config.VIDEO_FPS)
    if not 1 <= fps <= 60:
        raise ValueError("Video frame rate must be between 1 and 60.")
    total_frames = math.ceil(duration * fps - 1e-8)
    if total_frames < len(original_sections):
        raise ValueError("The existing narration is too short for the number of script sections.")
    warnings = []
    subtitle_path = Path(srt_path) if srt_path is not None else output / "narration.srt"
    try:
        if subtitle_path.is_file() and subtitle_path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Subtitle file is too large")
        captions = read_srt(str(subtitle_path), duration)
        if len(captions) > 20000:
            raise ValueError("Too many subtitle cues")
    except (OSError, UnicodeError, ValueError):
        captions = []
        warnings.append("The existing subtitle file could not be imported; the narration audio is still reusable.")
    boundaries, anchors, matching_cues = _legacy_boundaries(original_sections, captions, duration)
    timing_source = "legacy_srt" if matching_cues else "estimated_duration"
    if matching_cues:
        warnings.append("Existing narration and subtitle timestamps were reused. Section boundaries are inferred from subtitle text; exact word alignment is unavailable.")
        if any(index not in anchors for index in range(1, len(original_sections))):
            warnings.append("Some section boundaries could not be matched to subtitles and use duration-based estimates between available cue anchors.")
    else:
        warnings.append("Existing narration was reused. Section and motion timing is estimated from the measured audio duration; exact word alignment is unavailable.")
        if captions:
            warnings.append("Existing subtitle timestamps were preserved, but their text did not match the script closely enough to infer section boundaries.")
    sections, previous_frame = [], 0
    for index, section in enumerate(original_sections):
        end_frame = total_frames if index == len(original_sections) - 1 else max(
            previous_frame + 1,
            min(total_frames - (len(original_sections) - index - 1), round(boundaries[index + 1] * fps)),
        )
        sections.append({
            "id": section.get("id", index + 1), "title": str(section.get("title", "")),
            "narration": str(section.get("narration", "")),
            "start": previous_frame / fps, "duration": (end_frame - previous_frame) / fps,
            "start_frame": previous_frame, "duration_frames": end_frame - previous_frame,
            "caption_timing": "phrase" if captions else "estimated",
            "timing_source": "subtitle_inferred" if matching_cues else "estimated_duration",
            "start_boundary": anchors.get(index, "duration_estimate"),
        })
        previous_frame = end_frame
    timeline = {
        "version": 1, "title": str(script.get("title", "")),
        "audio_sha256": current_audio_hash, "script_signature": current_script_hash,
        "fps": fps, "width": int(config.SHORTS_WIDTH if portrait else config.VIDEO_WIDTH),
        "height": int(config.SHORTS_HEIGHT if portrait else config.VIDEO_HEIGHT),
        "duration": total_frames / fps, "audio_duration": duration, "total_frames": total_frames,
        "timing_source": timing_source, "timing_precision": "phrase" if matching_cues else "estimated",
        "narration_reused": True, "captions_source": "existing_srt" if captions else "none",
        "sections": sections, "captions": captions, "words": [], "warnings": warnings,
    }
    signature_fields = {key: timeline[key] for key in (
        "script_signature", "audio_sha256", "fps", "width", "height", "sections",
        "captions", "words", "total_frames", "timing_source", "timing_precision",
    )}
    timeline["timeline_signature"] = hashlib.sha256(json.dumps(
        signature_fields, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    target = output / "timeline.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(timeline, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(target)
    return timeline
