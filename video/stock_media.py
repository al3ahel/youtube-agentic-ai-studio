"""Probe and decode downloaded stock clips before they enter a storyboard.

Metadata is advisory: the actual local MP4 must supply enough measured video,
fit the requested frame without an extreme crop, and decode usable pictures.
No source audio is used by the footage renderer.
"""
from __future__ import annotations

import colorsys
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import time


MIN_CROP_FRACTION = .65
MAX_CLIP_SECONDS = 180
MAX_DIMENSION = 2560
MAX_PROBE_SECONDS = 12


class StockMediaError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code.replace("_", " "))


def check_stop(stop_event=None):
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Stopped while preparing stock footage")


def crop_fraction(width, height, orientation):
    target = {"landscape": 16 / 9, "portrait": 9 / 16, "square": 1}[orientation]
    ratio = width / height
    return min(ratio / target, target / ratio)


def _run(command, stop_event, deadline, maximum_bytes):
    check_stop(stop_event)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            check_stop(stop_event)
            if time.monotonic() >= deadline:
                raise StockMediaError("media_probe_timeout")
            try:
                stdout, _ = process.communicate(timeout=.1)
                break
            except subprocess.TimeoutExpired:
                continue
        check_stop(stop_event)
        if process.returncode or len(stdout) > maximum_bytes:
            raise StockMediaError("media_probe_failed")
        return stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise StockMediaError("invalid_video_metadata") from None
    if not math.isfinite(result):
        raise StockMediaError("invalid_video_metadata")
    return result


def _palette_score(frames, palette):
    if not palette:
        return None
    targets = [colorsys.rgb_to_hsv(*(int(value[index:index + 2], 16) / 255
                                    for index in (1, 3, 5))) for value in palette]
    scores = []
    # A few hundred pixels per frame suffice for a color preference. This is
    # never treated as evidence of the subject being present in a picture.
    for frame in frames:
        for index in range(0, len(frame), 48):
            hue, saturation, brightness = colorsys.rgb_to_hsv(*(byte / 255 for byte in frame[index:index + 3]))
            distances = []
            for target_hue, target_saturation, target_brightness in targets:
                hue_gap = min(abs(hue - target_hue), 1 - abs(hue - target_hue)) * 2
                distances.append(.55 * hue_gap * min(saturation, target_saturation)
                                 + .2 * abs(saturation - target_saturation)
                                 + .25 * abs(brightness - target_brightness))
            scores.append(1 - min(distances))
    return round(sum(scores) / len(scores), 6)


def inspect_clip(path, orientation, min_duration, *, palette=(), stop_event=None, deadline=None):
    """Return measured dimensions/duration, sample evidence and a color score.

    Color measures appearance only; semantic qualification belongs to the
    provider metadata/scene brief. Missing FFmpeg or unusable media is explicit.
    """
    check_stop(stop_event)
    ffprobe, ffmpeg = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        raise StockMediaError("ffprobe_unavailable" if not ffprobe else "ffmpeg_unavailable")
    limit = min(deadline or float("inf"), time.monotonic() + MAX_PROBE_SECONDS)
    path = str(Path(path).resolve())
    raw = _run([ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "v:0", "-show_entries",
                "stream=codec_type,width,height,duration:stream_tags=rotate:stream_side_data=rotation:format=duration",
                "-of", "json", path], stop_event, limit, 128 * 1024)
    try:
        payload = json.loads(raw)
        stream = payload["streams"][0]
        if stream.get("codec_type") != "video":
            raise StockMediaError("missing_video_stream")
        width, height = int(_number(stream.get("width"))), int(_number(stream.get("height")))
        duration = _number(stream.get("duration") or payload.get("format", {}).get("duration"))
        rotation = stream.get("tags", {}).get("rotate", 0)
        for side in stream.get("side_data_list", []):
            if "rotation" in side:
                rotation = side["rotation"]
        if abs(_number(rotation)) % 180 == 90:
            width, height = height, width
    except (ValueError, TypeError, KeyError, IndexError):
        raise StockMediaError("invalid_video_metadata") from None
    if min(width, height) < 360 or max(width, height) > MAX_DIMENSION:
        raise StockMediaError("unsupported_video_dimensions")
    if not 0 < duration <= MAX_CLIP_SECONDS or duration + .001 < min_duration:
        raise StockMediaError("clip_too_short" if duration < min_duration else "unsupported_video_duration")
    retained = crop_fraction(width, height, orientation)
    if retained < MIN_CROP_FRACTION:
        raise StockMediaError("excessive_crop")
    visible_duration = min_duration if min_duration > 0 else duration
    times = [max(0, min(duration - .05, visible_duration * fraction)) for fraction in (.25, .7)]
    frames = []
    for position in times:
        frame = _run([ffmpeg, "-v", "error", "-protocol_whitelist", "file,pipe", "-ss", f"{position:.6f}", "-i", path,
                      "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=64:64",
                      "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], stop_event, limit, 64 * 64 * 3)
        if len(frame) != 64 * 64 * 3:
            raise StockMediaError("frame_decode_failed")
        frames.append(frame)
    # Reject black/blank downloads; a film's subject cannot be established from
    # an empty color card even if its API metadata promises relevant footage.
    deviations = []
    for frame in frames:
        luminance = [.2126 * frame[index] + .7152 * frame[index + 1] + .0722 * frame[index + 2]
                     for index in range(0, len(frame), 3)]
        deviations.append(statistics.pstdev(luminance))
    if max(deviations) < 2:
        raise StockMediaError("blank_video_frames")
    return {"width": width, "height": height, "duration": duration, "in_point": 0,
            "crop_fraction": round(retained, 6), "palette_score": _palette_score(frames, palette),
            "decoded_frames": len(frames)}
