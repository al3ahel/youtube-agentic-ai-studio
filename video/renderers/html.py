"""Render a validated storyboard with the pinned, local HyperFrames CLI.

This adapter never executes generated code. A manifest supplies text, numbers,
timings and approved local media to the versioned templates in renderers/.
It deliberately has no MoviePy/NumPy dependency and never runs ``npx`` at render
time: install the lockfile once with ``npm ci`` in renderers/hyperframes.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from fractions import Fraction
from typing import Callable

import config


RENDERER_DIR = Path(__file__).resolve().parents[2] / "renderers" / "hyperframes"
HYPERFRAMES_VERSION = "0.9.19"
TEMPLATES = {"flow", "comparison", "timeline", "chart", "explain", "text", "footage"}
BEAT_ACTIONS = {"reveal", "highlight", "connect", "draw", "count", "emphasize"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac", ".opus"}
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class MotionRenderError(RuntimeError):
    """An actionable preparation, dependency or rendering failure."""


class MotionRenderCancelled(InterruptedError):
    """The requested render and its child processes were stopped."""


def _number(value, name: str, minimum=0, maximum=7200) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def _text(value, name: str, limit: int = 1000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if len(value) > limit:
        raise ValueError(f"{name} is too long (maximum {limit} characters)")
    # Newlines are retained; NUL is never useful in a title or caption.
    return value.replace("\x00", "").strip()


def _validate_plan(scene_plan: dict) -> dict:
    if not isinstance(scene_plan, dict) or type(scene_plan.get("version")) is not int or scene_plan.get("version") != 1:
        raise ValueError("Motion storyboard must have version 1")
    source = copy.deepcopy(scene_plan)
    fps = _number(source.get("fps", 30), "fps", 1, 60)
    if not fps.is_integer():
        raise ValueError("Motion storyboard fps must be an integer")
    width = _number(source.get("width", 1920), "width", 64, 4096)
    height = _number(source.get("height", 1080), "height", 64, 4096)
    if any(not x.is_integer() or int(x) % 2 for x in (width, height)):
        raise ValueError("Motion dimensions must be even integers")
    ratio = width / height
    if min(abs(ratio - 16 / 9), abs(ratio - 9 / 16)) > 0.002:
        raise ValueError("Motion layouts support 16:9 landscape or 9:16 portrait")
    duration = _number(source.get("duration"), "duration", 0.05, 7200)
    plan = {
        "version": 1, "fps": int(fps), "width": int(width), "height": int(height),
        "duration": duration, "title": _text(source.get("title", "Untitled"), "title", 240),
        "mode": _text(source.get("mode", "motion"), "mode", 40),
        "sections": [], "scenes": [], "captions": [],
    }
    if "script_signature" in source:
        plan["script_signature"] = _text(source["script_signature"], "script_signature", 256)
    if "timeline_signature" in source:
        plan["timeline_signature"] = _text(source["timeline_signature"], "timeline_signature", 256)
    if "audio_duration" in source:
        plan["audio_duration"] = _number(source["audio_duration"], "audio_duration", 0.05, 7200)
    sections = source.get("sections", [])
    if not isinstance(sections, list) or len(sections) > 256:
        raise ValueError("sections must be a list of at most 256 sections")
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            raise ValueError("Each section must be an object")
        plan["sections"].append({
            "id": _text(str(section.get("id", index)), "section.id", 100),
            "start": _number(section.get("start", 0), "section.start"),
            "duration": _number(section.get("duration", duration), "section.duration"),
            "narration": _text(section.get("narration", ""), "section.narration", 50000),
        })
    scenes = source.get("scenes")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 256:
        raise ValueError("Storyboard needs 1–256 scenes")
    seen = set()
    for index, original in enumerate(scenes):
        if not isinstance(original, dict):
            raise ValueError("Each scene must be an object")
        scene = {
            "id": _text(str(original.get("id", f"scene-{index}")), "scene.id", 100),
            "section_id": _text(str(original.get("section_id", "")), "section_id", 100),
            "template": original.get("template", "explain"),
            "start": _number(original.get("start"), "scene.start"),
            "duration": _number(original.get("duration"), "scene.duration", 0.05),
            "title": _text(original.get("title", ""), "scene.title", 240),
            "visual_goal": _text(original.get("visual_goal", ""), "visual_goal", 2000),
            "items": [], "beats": [],
        }
        if scene["id"] in seen:
            raise ValueError(f"Duplicate scene id: {scene['id']}")
        seen.add(scene["id"])
        if scene["template"] not in TEMPLATES:
            raise ValueError(f"Unsupported motion template: {scene['template']}")
        items = original.get("items", [])
        if not isinstance(items, list) or len(items) > 6:
            raise ValueError("Each motion scene supports at most six items")
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError("Scene items must be objects")
            clean = {"label": _text(item.get("label", ""), "item.label", 180)}
            if "id" in item:
                clean["id"] = _text(str(item["id"]), "item.id", 100)
            if item.get("value") is not None:
                value = item["value"]
                if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    raise ValueError("item.value must be text or a finite number")
                if isinstance(value, (int, float)):
                    value = _number(value, "item.value", -1e15, 1e15)
                else:
                    value = _text(value, "item.value", 100)
                clean["value"] = value
            # Source metadata is text only; it is never turned into an active URL.
            for key in ("source", "source_url", "unit"):
                if key in item:
                    clean[key] = _text(item[key], f"item.{key}", 2000)
            scene["items"].append(clean)
        if scene["template"] == "chart" and (
            not scene["items"] or any(
                not isinstance(item.get("value"), (int, float)) for item in scene["items"]
            )
        ):
            raise ValueError("A chart requires supplied numeric values for every item; no values are invented")
        beats = original.get("beats", [])
        if not isinstance(beats, list) or len(beats) > 128:
            raise ValueError("Each scene supports at most 128 beats")
        for beat in beats:
            if not isinstance(beat, dict):
                raise ValueError("Scene beats must be objects")
            action = beat.get("action", "reveal")
            if action not in BEAT_ACTIONS:
                raise ValueError(f"Unsupported beat action: {action}")
            scene["beats"].append({
                "time": _number(beat.get("time"), "beat.time", 0, scene["duration"]),
                "target": _text(str(beat.get("target", "scene")), "beat.target", 180),
                "action": action,
            })
        scene["beats"].sort(key=lambda beat: beat["time"])
        if original.get("footage") is not None:
            footage = original["footage"]
            if not isinstance(footage, dict):
                raise ValueError("footage must be an object")
            scene["footage"] = {
                "path": _text(footage.get("path", ""), "footage.path", 4096),
                "source_url": _text(footage.get("source_url", ""), "footage.source_url", 4096),
                "credit": _text(footage.get("credit", ""), "footage.credit", 500),
                "in_point": _number(footage.get("in_point", 0), "footage.in_point"),
            }
        if scene["template"] == "footage" and not scene.get("footage", {}).get("path"):
            raise ValueError("A footage scene needs a downloaded local video")
        for key in ("source", "source_url"):
            if key in original:
                scene[key] = _text(original[key], f"scene.{key}", 2000)
        plan["scenes"].append(scene)
    plan["scenes"].sort(key=lambda scene: scene["start"])
    tolerance = max(1 / fps, 0.05)
    previous_end = 0.0
    for scene in plan["scenes"]:
        if abs(scene["start"] - previous_end) > tolerance:
            raise ValueError("Scenes must cover the narration continuously without gaps or overlaps")
        previous_end = scene["start"] + scene["duration"]
    if abs(previous_end - duration) > tolerance:
        raise ValueError("The final scene must end at the storyboard duration")
    captions = source.get("captions", [])
    if not isinstance(captions, list) or len(captions) > 20000:
        raise ValueError("captions must contain at most 20,000 timed cues")
    for caption in captions:
        if not isinstance(caption, dict):
            raise ValueError("Caption cues must be objects")
        start = _number(caption.get("start"), "caption.start", 0, duration)
        end = _number(caption.get("end"), "caption.end", start, duration + tolerance)
        text = _text(caption.get("text", ""), "caption.text", 1000)
        if end > start and text:
            plan["captions"].append({"start": start, "end": min(end, duration), "text": text})
    plan["captions"].sort(key=lambda caption: (caption["start"], caption["end"]))
    return plan


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _media_path(value: str, output: Path, extensions: set, *, restrict=False) -> Path:
    if not value or "://" in value or value.startswith(("data:", "//", "\\\\")):
        raise ValueError("Motion assets must be downloaded local files")
    supplied = Path(value).expanduser()
    candidates = [supplied] if supplied.is_absolute() else [output / supplied, supplied]
    roots = [output, Path(getattr(config, "OUTPUT_DIR", "output")).expanduser().resolve()]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and resolved.suffix.lower() in extensions:
            if not restrict or any(_inside(resolved, root) for root in roots):
                return resolved
    raise ValueError(f"Missing or unsupported local media file: {value}")


def _copy_asset(source: Path, assets: Path, prefix: str) -> str:
    stat = source.stat()
    fingerprint = hashlib.sha256(
        f"{source}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
    ).hexdigest()[:16]
    destination = assets / f"{prefix}-{fingerprint}{source.suffix.lower()}"
    if source != destination and (
        not destination.exists() or destination.stat().st_size != stat.st_size
    ):
        shutil.copy2(source, destination)
    return f"assets/{destination.name}"


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_vendor(project: Path) -> None:
    dependencies = RENDERER_DIR / "node_modules"
    gsap = dependencies / "gsap" / "dist" / "gsap.min.js"
    font = dependencies / "@fontsource-variable" / "inter"
    if not gsap.is_file() or not (font / "wght.css").is_file():
        raise MotionRenderError(
            "Motion renderer dependencies are missing. Install Node.js 22+ and run "
            "`npm ci` in renderers/hyperframes, then retry."
        )
    vendor = project / "vendor"
    files = vendor / "files"
    files.mkdir(parents=True, exist_ok=True)
    shutil.copy2(gsap, vendor / "gsap.min.js")
    shutil.copy2(font / "wght.css", vendor / "fonts.css")
    shutil.copy2(font / "LICENSE", vendor / "INTER-LICENSE.txt")
    for path in (font / "files").glob("*-wght-normal.woff2"):
        shutil.copy2(path, files / path.name)


def prepare_motion_project(scene_plan: dict, audio_path: str, output_dir: str) -> Path:
    """Create output_dir/motion/index.html and its local, self-contained assets.

    The input dict is never mutated. JSON is embedded as inert data with HTML
    delimiters escaped. All text is rendered with textContent by scene.js.
    ``?preview=1`` enables fitted playback controls; the clean URL is the render
    composition. An empty audio_path is allowed only for silent authoring previews.
    """
    plan = _validate_plan(scene_plan)
    output = Path(output_dir).expanduser().resolve()
    audio = _media_path(audio_path, output, AUDIO_EXTENSIONS) if audio_path else None
    footage_sources = {}
    for index, scene in enumerate(plan["scenes"]):
        if scene.get("footage"):
            footage_sources[index] = _media_path(
                scene["footage"]["path"], output, VIDEO_EXTENSIONS, restrict=True,
            )
    project = output / "motion"
    if project.is_symlink():
        raise ValueError("The generated motion directory cannot be a symbolic link")
    for folder in (project, project / "assets", project / "vendor"):
        if folder.is_symlink():
            raise ValueError("Motion output asset directories cannot be symbolic links")
        folder.mkdir(parents=True, exist_ok=True)
    _copy_vendor(project)
    for filename in ("scene.js", "scene.css", "preview.js"):
        shutil.copy2(RENDERER_DIR / "templates" / filename, project / filename)
    media_markup = []
    if audio:
        plan["audio"] = {"path": _copy_asset(audio, project / "assets", "narration")}
        media_markup.append(
            f'<audio id="narration" src="{plan["audio"]["path"]}" preload="auto" '
            f'data-start="0" data-duration="{plan["duration"]}" data-volume="1"></audio>'
        )
    for index, source in footage_sources.items():
        scene = plan["scenes"][index]
        footage = scene["footage"]
        footage["path"] = _copy_asset(source, project / "assets", "footage")
        media_markup.append(
            f'<video id="footage-{index}" src="{footage["path"]}" muted playsinline '
            f'preload="auto" data-start="{scene["start"]}" data-duration="{scene["duration"]}" '
            f'data-media-start="{footage["in_point"]}" data-volume="0"></video>'
        )
    plan["renderer"] = {"name": "hyperframes", "version": HYPERFRAMES_VERSION}
    payload = json.dumps(plan, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    embedded = (payload.replace("&", "\\u0026").replace("<", "\\u003c")
                .replace(">", "\\u003e").replace("\u2028", "\\u2028")
                .replace("\u2029", "\\u2029"))
    template = (RENDERER_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    replacements = {
        "@@TITLE@@": html.escape(plan["title"]), "@@WIDTH@@": str(plan["width"]),
        "@@HEIGHT@@": str(plan["height"]), "@@FPS@@": str(plan["fps"]),
        "@@DURATION@@": str(plan["duration"]), "@@PLAN@@": embedded,
        "@@MEDIA@@": "\n".join(media_markup),
    }
    # One pass avoids treating placeholder-like user text as template instructions.
    template = re.sub(r"@@(?:TITLE|WIDTH|HEIGHT|FPS|DURATION|PLAN|MEDIA)@@",
                      lambda match: replacements[match.group(0)], template)
    _atomic_text(project / "project.json", json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False))
    _atomic_text(project / "index.html", template)
    return project / "index.html"


def _command(binary: str, label: str) -> str:
    result = shutil.which(binary)
    if not result:
        raise MotionRenderError(f"{label} is not on PATH. Install it, reopen PowerShell, and retry.")
    return result


def _dependencies() -> tuple[str, Path, str]:
    node = _command(str(getattr(config, "MOTION_NODE_BINARY", "node")), "Node.js 22+")
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        version = subprocess.run([node, "--version"], capture_output=True, text=True,
                                 encoding="utf-8", timeout=10, check=True, **flags).stdout.strip()
        match = re.match(r"v?(\d+)\.", version)
        if not match or int(match[1]) < 22:
            raise MotionRenderError(f"Motion rendering needs Node.js 22+; found {version or 'unknown version'}.")
    except (OSError, subprocess.SubprocessError) as exc:
        raise MotionRenderError("Could not start Node.js. Check `node --version` in PowerShell.") from exc
    package = RENDERER_DIR / "node_modules" / "hyperframes"
    try:
        installed = json.loads((package / "package.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError) as exc:
        raise MotionRenderError("Run `npm ci` in renderers/hyperframes to install the pinned renderer.") from exc
    if installed != HYPERFRAMES_VERSION:
        raise MotionRenderError(f"Expected HyperFrames {HYPERFRAMES_VERSION}; run `npm ci` in renderers/hyperframes.")
    _command("ffmpeg", "FFmpeg")
    ffprobe = _command("ffprobe", "FFprobe (included with FFmpeg)")
    return node, package / "bin" / "hyperframes.mjs", ffprobe


def _probe(path: Path, ffprobe: str) -> dict:
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, check=True, **flags,
        )
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise MotionRenderError(f"FFprobe could not read {path.name}.") from exc


def _terminate_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None and os.name == "nt":
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=5)
    finally:
        # The session can still contain a Chrome/FFmpeg child after its Node
        # leader has exited. Only our own newly-created process group is targeted.
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _emit(progress: Callable[[str], None] | None, message: str) -> None:
    if progress:
        progress(message)
    else:
        print(message, flush=True)


def _progress_message(line: str) -> str | None:
    """Expose useful render progress without streaming internal trace JSON to the UI."""
    if not line.startswith("@hf-progress "):
        return None
    try:
        event = json.loads(line[len("@hf-progress "):])
    except (ValueError, TypeError):
        return None
    labels = {
        "compile": "Preparing motion composition", "extract_video": "Preparing footage frames",
        "process_audio": "Preparing narration audio", "start_browsers": "Starting frame renderer",
        "capture": "Rendering motion frames", "encode": "Encoding video", "assemble": "Adding narration to video",
    }
    label = labels.get(event.get("code"))
    if not label:
        return None
    percent = event.get("pct")
    if isinstance(percent, (int, float)) and math.isfinite(percent):
        label += f" — {max(0, min(100, round(percent)))}%"
    return label


def _run_render(command: list[str], cwd: Path, timeout: float, stop_event=None,
                progress=None) -> None:
    if stop_event is not None and stop_event.is_set():
        raise MotionRenderCancelled("Motion render cancelled")
    environment = os.environ.copy()
    environment.update({
        "DO_NOT_TRACK": "1", "HYPERFRAMES_NO_TELEMETRY": "1", "NO_COLOR": "1",
        "HYPERFRAMES_NO_CAPTURE_DOTENV": "1", "HYPERFRAMES_SKIP_SKILLS": "1",
    })
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
               if os.name == "nt" else {"start_new_session": True})
    process = subprocess.Popen(
        command, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
        bufsize=1, env=environment, **options,
    )
    lines: queue.Queue = queue.Queue()
    tail = deque(maxlen=24)

    def read_output():
        try:
            for line in process.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    started = time.monotonic()
    last_update = started
    log_bytes = 0
    finished = False
    try:
        with (cwd / "render.log").open("w", encoding="utf-8") as logfile:
            while not finished or process.poll() is None:
                if stop_event is not None and stop_event.is_set():
                    raise MotionRenderCancelled("Motion render cancelled")
                now = time.monotonic()
                if now - started > timeout:
                    raise MotionRenderError(f"Motion render exceeded its {timeout:g}-second time limit. See motion/render.log.")
                try:
                    line = lines.get(timeout=0.1)
                except queue.Empty:
                    if now - last_update > 15:
                        _emit(progress, f"Rendering motion video… {int(now - started)}s elapsed")
                        last_update = now
                    continue
                if line is None:
                    finished = True
                    continue
                clean = _ANSI.sub("", line).strip()
                if not clean:
                    continue
                tail.append(clean[-2000:])
                if log_bytes < 8_000_000:
                    logfile.write(clean[:8000] + "\n")
                    logfile.flush()
                    log_bytes += len(clean[:8000])
                message = _progress_message(clean)
                if message and now - last_update > 0.5:
                    _emit(progress, message)
                    last_update = now
        code = process.wait(timeout=5)
        if code:
            detail = "\n".join(tail)[-5000:]
            raise MotionRenderError(f"HyperFrames exited with code {code}.\n{detail}\nFull log: {cwd / 'render.log'}")
    except BaseException:
        _terminate_tree(process)
        raise
    finally:
        reader.join(timeout=2)
        if process.stdout:
            process.stdout.close()


def render_motion_video(script, audio_path: str, output_dir: str, scene_plan: dict,
                        video_type: str = "normal", preview: bool = False,
                        stop_event=None, progress=None) -> str:
    """Render an MP4 using an argument-list subprocess, with timeout/cancellation.

    ``progress`` receives text messages. A failed/cancelled render leaves an
    existing final MP4 intact. Narration is included by HyperFrames' media mixer.
    """
    if stop_event is not None and stop_event.is_set():
        raise MotionRenderCancelled("Motion render cancelled")
    node, cli, ffprobe = _dependencies()
    output = Path(output_dir).expanduser().resolve()
    audio = _media_path(audio_path, output, AUDIO_EXTENSIONS)
    plan = _validate_plan(scene_plan)
    audio_info = _probe(audio, ffprobe)
    if not any(stream.get("codec_type") == "audio" for stream in audio_info.get("streams", [])):
        raise MotionRenderError("The narration file has no audio stream.")
    audio_duration = float(audio_info.get("format", {}).get("duration", 0))
    if not math.isfinite(audio_duration) or audio_duration <= 0:
        raise MotionRenderError("Could not determine the narration duration.")
    if abs(audio_duration - plan["duration"]) > max(0.35, 2 / plan["fps"]):
        raise MotionRenderError("Storyboard duration does not match narration. Rebuild the storyboard after generating narration.")
    for scene in plan["scenes"]:
        if not scene.get("footage"):
            continue
        source = _media_path(scene["footage"]["path"], output, VIDEO_EXTENSIONS, restrict=True)
        media = _probe(source, ffprobe)
        video_stream = next((stream for stream in media.get("streams", []) if stream.get("codec_type") == "video"), None)
        if not video_stream:
            raise MotionRenderError(f"Footage for scene {scene['id']} has no video stream. Choose another clip.")
        length = video_stream.get("duration") or media.get("format", {}).get("duration")
        try:
            length = float(length)
        except (TypeError, ValueError) as exc:
            raise MotionRenderError(f"Cannot measure footage for scene {scene['id']}. Choose another clip.") from exc
        needed = scene["footage"]["in_point"] + scene["duration"]
        if not math.isfinite(length) or length + 1 / plan["fps"] < needed:
            raise MotionRenderError(
                f"Footage for scene {scene['id']} is too short ({length:g}s; needs {needed:g}s). "
                "Choose a longer clip or change this scene to an animated template."
            )
    _emit(progress, "Preparing local motion scenes and narration…")
    entry = prepare_motion_project(plan, str(audio), str(output))
    quality = "draft" if preview else str(getattr(config, "MOTION_QUALITY", "standard"))
    if quality not in {"draft", "standard", "high", "looks", "delivery"}:
        quality = "standard"
    workers = max(1, min(4, int(getattr(config, "MOTION_WORKERS", 1))))
    timeout = max(30, min(86400, float(getattr(config, "MOTION_RENDER_TIMEOUT_SECONDS", 3600))))
    filename = "motion_preview.mp4" if preview else ("final_short.mp4" if video_type == "shorts" else "final_video.mp4")
    final = output / filename
    temporary = output / f".motion-render-{uuid.uuid4().hex}.mp4"
    command = [node, str(cli), "render", str(entry.parent), "--output", str(temporary),
               "--fps", str(plan["fps"]), "--quality", quality, "--workers", str(workers),
               "--no-browser-gpu", "--sdr"]
    try:
        _emit(progress, f"Rendering {plan['width']}×{plan['height']} at {plan['fps']} fps ({quality})…")
        _run_render(command, entry.parent, timeout, stop_event, progress)
        if stop_event is not None and stop_event.is_set():
            raise MotionRenderCancelled("Motion render cancelled")
        if not temporary.is_file() or temporary.stat().st_size < 1000:
            raise MotionRenderError("HyperFrames finished without a usable MP4.")
        result = _probe(temporary, ffprobe)
        streams = result.get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
        if (video.get("width"), video.get("height")) != (plan["width"], plan["height"]):
            raise MotionRenderError("Rendered dimensions do not match the storyboard.")
        if not any(stream.get("codec_type") == "audio" for stream in streams):
            raise MotionRenderError("Rendered video is missing narration audio.")
        try:
            rendered_fps = float(Fraction(video.get("avg_frame_rate", "0")))
        except (ValueError, ZeroDivisionError) as exc:
            raise MotionRenderError("Could not verify the rendered frame rate.") from exc
        if abs(rendered_fps - plan["fps"]) > .001:
            raise MotionRenderError("Rendered frame rate does not match the storyboard.")
        result_duration = float(result.get("format", {}).get("duration", 0))
        if not math.isfinite(result_duration) or result_duration < audio_duration - max(0.25, 2 / plan["fps"]):
            raise MotionRenderError("Rendered video is shorter than its narration.")
        if result_duration > plan["duration"] + max(.35, 2 / plan["fps"]):
            raise MotionRenderError("Rendered video is longer than its storyboard.")
        temporary.replace(final)
        _emit(progress, "Motion video rendered and verified.")
        return str(final)
    finally:
        temporary.unlink(missing_ok=True)
