"""
Library: every finished render is archived to output/library/<id>/ so it can
be re-opened, re-edited, re-published, downloaded or deleted later.
"""
import glob
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import tempfile
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify, request

import config
from studio.description import build_description, load_credits, with_credit
from studio.files import send, slug
from studio.state import job_lock, log, state
from studio.thumbs import thumb_backgrounds, thumb_settings

bp = Blueprint("library", __name__)

LIB_DIR = os.path.join(config.OUTPUT_DIR, "library")

def lib_path(pid, *parts):
    if not re.fullmatch(r"[\w-]+", pid or ""): abort(404)
    root = Path(LIB_DIR).resolve()
    project = root / pid
    target = project.joinpath(*parts).resolve()
    if project.resolve() != project or not target.is_relative_to(project):
        abort(404)
    return str(target)


def _json(path, fallback=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _relative_file(root, name):
    """Only portable paths underneath this archive may be restored."""
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("Invalid archived file path")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or any(part.startswith(".") for part in relative.parts):
        raise ValueError("Archived file paths must stay inside the project")
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError(f"Archived file is missing or outside the project: {relative.name}")
    return target


def _copy_local_tree(source, destination):
    """Copy generated local assets, never links or unrelated secret/code files."""
    allowed = {".html", ".js", ".mjs", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg", ".webp",
               ".mp3", ".wav", ".m4a", ".ogg", ".mp4", ".woff", ".woff2", ".ttf", ".otf"}
    source, destination = Path(source), Path(destination)
    if source.is_symlink():
        raise ValueError("A project directory must not be a symbolic link")
    if not source.is_dir():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for directory, subdirs, files in os.walk(source, followlinks=False):
        base = Path(directory)
        if any((base / name).is_symlink() for name in subdirs + files):
            raise ValueError("Motion projects cannot contain symbolic links")
        subdirs[:] = [name for name in subdirs if not name.startswith(".")]
        for name in files:
            item = base / name
            license_text = item.suffix.lower() == ".txt" and "LICENSE" in name.upper()
            if name.startswith(".") or (item.suffix.lower() not in allowed and not license_text):
                continue
            target = destination / item.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _portable_media(value, destination, copied):
    """Copy a referenced output asset and return its project-relative path."""
    output = Path(config.OUTPUT_DIR).resolve()
    source = Path(value).resolve()
    if not source.is_relative_to(output) or not source.is_file() or source.suffix.lower() not in {".mp4", ".webm", ".mov", ".jpg", ".png", ".jpeg", ".webp"}:
        raise ValueError("A storyboard asset is missing or outside the output directory")
    key = str(source)
    if key not in copied:
        filename = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20] + source.suffix.lower()
        relative = Path("assets") / "media" / filename
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied[key] = relative.as_posix()
    return copied[key]


def _archived_media(root, relative):
    source = _relative_file(root, relative)
    if Path(relative).parts[0] not in {"assets", "motion"} or source.suffix.lower() not in {".mp4", ".webm", ".mov", ".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("Archived footage must be a local media asset")
    return source

def read_meta(pid):
    with open(lib_path(pid, "meta.json"), encoding="utf-8") as f:
        return json.load(f)

def write_meta(pid, meta):
    _write_json(lib_path(pid, "meta.json"), meta)

def archive_project(allow_draft=False):
    """Copy the finished render + metadata into the Library. Copies, never hard-links:
    ffmpeg and PIL overwrite output files in place, which would corrupt a linked copy."""
    script, research = state.get("script") or {}, state.get("research") or {}
    draft = bool(state.get("preview_only"))
    rendered_path = state.get("preview_path") if draft else state.get("video_path")
    if (draft and not allow_draft) or (state.get("visual_plan") and state.get("stages", {}).get("video") != "done") or not (rendered_path and os.path.exists(rendered_path)):
        return None
    pid = time.strftime("%Y%m%d-%H%M%S") + "-" + slug(script.get("title")) + "-" + uuid.uuid4().hex[:6]
    d = os.path.join(LIB_DIR, pid)
    os.makedirs(d, exist_ok=True)

    def _put(src, name):
        if src and os.path.exists(src):
            shutil.copy2(src, os.path.join(d, name))
            return name
        return None

    files = {
        "video":    _put(rendered_path, "video.mp4"),
        "thumb":    _put(state.get("thumb_path"), "thumbnail.jpg"),
        "captions": _put(os.path.join(config.OUTPUT_DIR, "narration.srt"), "captions.srt"),
    }
    audio_path = state.get("audio_path")
    if audio_path:
        suffix = Path(audio_path).suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".ogg"}:
            raise ValueError("Unsupported narration format in project")
        files["narration"] = _put(audio_path, "narration" + suffix)
    files["timings"] = _put(os.path.join(config.OUTPUT_DIR, "narration.timings.json"), "narration.timings.json") if state.get("timeline") else None
    mixed_name = next((name for name in ("narration_mixed.wav", "narration_mixed.mp3") if os.path.isfile(os.path.join(config.OUTPUT_DIR, name))), None)
    files["mixed_audio"] = _put(os.path.join(config.OUTPUT_DIR, mixed_name), mixed_name) if mixed_name else None
    files["render_audio_manifest"] = _put(os.path.join(config.OUTPUT_DIR, "render_audio.json"), "render_audio.json")
    render_audio = state.get("render_audio_path") or audio_path
    if render_audio and os.path.isfile(render_audio):
        if audio_path and os.path.abspath(render_audio) == os.path.abspath(audio_path):
            files["render_audio"] = files.get("narration")
        elif mixed_name and os.path.abspath(render_audio) == os.path.abspath(os.path.join(config.OUTPUT_DIR, mixed_name)):
            files["render_audio"] = files.get("mixed_audio")
        else:
            suffix = Path(render_audio).suffix.lower()
            if suffix not in {".wav", ".mp3", ".m4a", ".ogg"}:
                raise ValueError("Unsupported render audio format")
            files["render_audio"] = _put(render_audio, "render_audio" + suffix)
    for key, filename in (("timeline", "timeline.json"), ("visual_qa", "visual_qa.json")):
        value = state.get(key)
        if value is not None:
            _write_json(Path(d) / filename, value)
            files[key] = filename
    copied = {}
    visual_plan = deepcopy(state.get("visual_plan"))
    if visual_plan:
        for scene in visual_plan.get("scenes", []):
            for kind in ("footage", "image"):
                if scene.get(kind):
                    scene[kind]["path"] = _portable_media(scene[kind]["path"], Path(d), copied)
        _write_json(Path(d) / "scene_plan.json", visual_plan)
        files["scene_plan"] = "scene_plan.json"
        source_motion = Path(config.OUTPUT_DIR) / "motion"
        _copy_local_tree(source_motion, Path(d) / "motion")
        if (Path(d) / "motion" / "index.html").is_file():
            files["motion"] = "motion/index.html"
    video_credits = _json(Path(config.OUTPUT_DIR) / "video_credits.json", [])
    if isinstance(video_credits, list):
        portable_credits = []
        for record in video_credits:
            if not isinstance(record, dict):
                continue
            item = deepcopy(record)
            if item.get("path"):
                try:
                    item["path"] = _portable_media(item["path"], Path(d), copied)
                except ValueError:
                    item.pop("path", None)
            portable_credits.append(item)
        _write_json(Path(d) / "video_credits.json", portable_credits)
        files["video_credits"] = "video_credits.json"
    image_map = {}
    photo_names = {}
    for section, paths in (state.get("image_map") or {}).items():
        kept = []
        for index, path in enumerate(paths if isinstance(paths, list) else [paths]):
            if not path or not Path(path).is_file():
                continue
            source = Path(path)
            section_name = re.sub(r"[^a-zA-Z0-9_-]", "", str(section)) or "1"
            if section_name.isdigit():
                section_name = f"{int(section_name):02d}"
            relative = f"images/section_{section_name}_img{index + 1:02d}{source.suffix.lower()}"
            target = Path(d) / relative
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(source, target)
            kept.append(relative)
            photo_names.setdefault(source.name, []).append(Path(relative).name)
        image_map[str(section)] = kept
    _write_json(Path(d) / "image_map.json", image_map)
    files["image_map"] = "image_map.json"
    cover = state.get("thumb_bg") or next((p for v in (state.get("image_map") or {}).values()
                                            for p in (v if isinstance(v, list) else [v]) if p and os.path.exists(p)), None)
    files["cover"] = _put(cover, "cover.jpg")
    # keep a few of the video's photos so the thumbnail can be redesigned later
    os.makedirs(os.path.join(d, "photos"), exist_ok=True)
    for i, p in enumerate(thumb_backgrounds(state.get("image_map"))[:8]):
        shutil.copy2(p, os.path.join(d, "photos", f"{i + 1:02d}.jpg"))
    # Video credits have their own portable manifest above. Keep photo credits
    # separate so loading a project never merges it with another video's assets.
    photo_credits = _json(Path(config.OUTPUT_DIR) / "credits.json", [])
    portable_photo_credits = []
    for credit in photo_credits if isinstance(photo_credits, list) else []:
        if not isinstance(credit, dict):
            continue
        names = photo_names.get(Path(str(credit.get("file", ""))).name, [])
        for name in dict.fromkeys(names or [credit.get("file", "")]):
            portable_photo_credits.append({**credit, "file": name})
    photo_credits = portable_photo_credits
    for name, obj in (("script.json", script), ("research.json", research), ("credits.json", photo_credits)):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)

    try:
        timeline = state.get("timeline") or {}
        duration = float(timeline.get("duration", 0))
        if not math.isfinite(duration) or duration <= 0:
            from video.timeline import probe_duration
            duration = probe_duration(rendered_path)
        duration = round(duration, 1)
    except Exception:
        duration = None

    meta = {
        "id":          pid,
        "title":       script.get("title", ""),
        "topic":       research.get("topic", ""),
        "video_type":  state.get("video_type", "normal"),
        "visual_mode": state.get("visual_mode") or "classic",
        "project_version": 3,
        "profile_id": state.get("profile_id") or "default",
        "profile_name": state.get("profile_name") or config.CHANNEL_NAME,
        "schedule_id": state.get("schedule_id"),
        "preview_only": draft,
        "image_reuse_note": state.get("image_reuse_note"),
        "created_at":  datetime.now().isoformat(timespec="seconds"),
        "render_secs": int(time.time() - state["started_at"]) if state.get("started_at") else None,
        "duration":    duration,
        "sections":    len(script.get("sections", [])),
        "sources":     len((research.get("grounding") or {}).get("sources") or []),
        "description": state.get("description", ""),
        "tags":        script.get("tags", []),
        "yt_url":      None,
        "thumb_style": thumb_settings(script)[0],
        "thumb_text":  state.get("thumb_text"),
        "files":       files,
    }
    write_meta(pid, meta)
    state["library_id"] = pid
    log(f"Saved to Library: {pid}")
    return pid

def sync_library():
    """Push review edits (title, description, tags, thumbnail, upload URL) into the Library entry."""
    from studio.workspace import checkpoint_workspace
    checkpoint_workspace()
    pid = state.get("library_id")
    if not pid or not os.path.isdir(lib_path(pid)):
        return
    try:
        meta = read_meta(pid)
        script = state.get("script") or {}
        meta.update({
            "title":       script.get("title", meta.get("title")),
            "tags":        script.get("tags", meta.get("tags")),
            "thumb_style": thumb_settings(script)[0],
            "thumb_text":  state.get("thumb_text"),
            "description": state.get("description", meta.get("description")),
            "yt_url":      state.get("yt_url") or meta.get("yt_url"),
        })
        tp = state.get("thumb_path")
        if tp and os.path.exists(tp) and os.path.abspath(tp) != os.path.abspath(lib_path(pid, "thumbnail.jpg")):
            shutil.copy2(tp, lib_path(pid, "thumbnail.jpg"))
            meta["files"]["thumb"] = "thumbnail.jpg"
        with open(lib_path(pid, "script.json"), "w", encoding="utf-8") as f:
            json.dump(script, f, indent=2)
        write_meta(pid, meta)
    except Exception as e:
        log(f"Library sync failed: {e}", "warn")

def list_library():
    items = []
    if os.path.isdir(LIB_DIR):
        for pid in os.listdir(LIB_DIR):
            try: items.append(read_meta(pid))
            except Exception: continue
    items.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return items

@bp.route("/api/library")
def library_index():
    items = list_library()
    profile_id = request.args.get("profile_id")
    if profile_id:
        from studio.channels import get_profile
        try:
            get_profile(profile_id)
        except (ValueError, LookupError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        items = [item for item in items if (item.get("profile_id") or "default") == profile_id]
    today = datetime.now().date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(59, -1, -1)]
    counts = {d: 0 for d in days}
    for m in items:
        d = (m.get("created_at") or "")[:10]
        if d in counts: counts[d] += 1
    return jsonify({
        "items":    items,
        "activity": [{"day": d, "count": counts[d]} for d in days],
        "totals": {
            "videos":   len(items),
            "shorts":   sum(1 for m in items if m.get("video_type") == "shorts"),
            "uploaded": sum(1 for m in items if m.get("yt_url")),
            "minutes":  round(sum((m.get("duration") or 0) for m in items) / 60, 1),
        },
    })

@bp.route("/library/<pid>/<name>")
def library_file(pid, name):
    allowed = {"video.mp4": "video/mp4", "thumbnail.jpg": "image/jpeg",
               "cover.jpg": "image/jpeg", "captions.srt": "text/plain"}
    if name not in allowed: abort(404)
    return send(lib_path(pid, name), allowed[name], f"{pid}-{name}")

@bp.route("/api/library/<pid>/load", methods=["POST"])
def library_load(pid):
    with job_lock:
        from studio.channels import is_connecting
        if state.get("running") or state.get("visual_editing") or state.get("uploading") or is_connecting():
            return jsonify({"ok": False, "error": "Wait for the current job to finish before opening another project."}), 409
        return _load_project(pid)


_PROJECT_CACHE = ("script.json", "research.json", "project_profile.json", "workspace.json", "image_map.json", "credits.json", "video_credits.json", "narration.srt",
                  "narration.wav", "narration.mp3", "narration.m4a", "narration.ogg", "narration.timings.json",
                  "timeline.json", "scene_plan.json", "visual_qa.json", "motion", "assets", "images",
                  "motion_preview.mp4", "narration_with_music.mp3", "narration_mixed.mp3", "narration_mixed.wav", "audio_mixed.mp3",
                  "render_audio.wav", "render_audio.mp3", "render_audio.m4a", "render_audio.ogg", "render_audio.json")


def _replace_project_cache(staging, output):
    """Replace the current job's files with rollback if a local copy fails."""
    previous = staging / ".previous"
    previous.mkdir()
    changed = []
    try:
        for name in _PROJECT_CACHE:
            target, replacement, backup = output / name, staging / name, previous / name
            had_previous = target.exists() or target.is_symlink()
            if had_previous:
                os.replace(target, backup)
            changed.append((target, backup, had_previous))
            if replacement.exists():
                os.replace(replacement, target)
    except Exception:
        for target, backup, had_previous in reversed(changed):
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            if had_previous:
                os.replace(backup, target)
        raise


def _load_project(pid):
    try:
        meta = read_meta(pid)
        from studio.channels import get_profile, activate_profile
        profile = get_profile(meta.get("profile_id") or "default")
        archive = Path(lib_path(pid))
        script = json.loads(_relative_file(archive, "script.json").read_text(encoding="utf-8"))
        research = json.loads(_relative_file(archive, "research.json").read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or not isinstance(script, dict) or not isinstance(research, dict):
            raise ValueError("Project metadata is not a JSON object")
        files = meta.get("files") or {}
        if not isinstance(files, dict):
            raise ValueError("Invalid project files manifest")
        mode = meta.get("visual_mode", "classic")
        if mode not in {"motion", "hybrid", "classic"}:
            raise ValueError("Unknown archived visual mode")
        output = Path(config.OUTPUT_DIR).resolve()
        output.mkdir(parents=True, exist_ok=True)
        plan, timeline, qa, audio_name, render_audio_name, restored_images = None, None, None, None, None, {}
        image_reuse_note = meta.get("image_reuse_note")
        with tempfile.TemporaryDirectory(prefix=".project-load-", dir=output) as temporary:
            staging = Path(temporary)
            _write_json(staging / "script.json", script)
            _write_json(staging / "research.json", research)
            from studio.project_identity import write_project_identity
            write_project_identity(staging, profile["id"], profile["name"])
            for archive_name, current_name in (("credits.json", "credits.json"), ("captions.srt", "narration.srt")):
                if (archive / archive_name).exists():
                    shutil.copy2(_relative_file(archive, archive_name), staging / current_name)
            narration = files.get("narration") or next((name for name in ("narration.wav", "narration.mp3", "narration.m4a", "narration.ogg") if (archive / name).is_file()), None)
            if narration:
                source = _relative_file(archive, narration)
                if source.suffix.lower() not in {".wav", ".mp3", ".m4a", ".ogg"}:
                    raise ValueError("Invalid archived narration format")
                audio_name = "narration" + source.suffix.lower()
                shutil.copy2(source, staging / audio_name)
            elif (archive / "video.mp4").is_file() and shutil.which("ffmpeg"):
                # The original YAAS archives kept the MP4 and captions but not
                # necessarily a separate narration track. Recover its soundtrack
                # without another TTS request; any old music remains in it.
                source = _relative_file(archive, "video.mp4")
                recovered = staging / "narration.wav"
                try:
                    subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-y", "-v", "error", "-i", str(source),
                                    "-map", "0:a:0", "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(recovered)],
                                   check=True, capture_output=True, timeout=120)
                    audio_name = recovered.name
                    log("Recovered the old video's soundtrack; existing music remains in this audio.")
                except (OSError, subprocess.SubprocessError):
                    recovered.unlink(missing_ok=True)
            if audio_name and (archive / "narration.timings.json").exists():
                shutil.copy2(_relative_file(archive, "narration.timings.json"), staging / "narration.timings.json")
            for mixed_file in ("narration_mixed.wav", "narration_mixed.mp3"):
                if audio_name and (archive / mixed_file).exists():
                    shutil.copy2(_relative_file(archive, mixed_file), staging / mixed_file)
            if files.get("render_audio"):
                source = _relative_file(archive, files["render_audio"])
                if source.suffix.lower() not in {".wav", ".mp3", ".m4a", ".ogg"}:
                    raise ValueError("Invalid archived render audio")
                render_audio_name = source.name if source.name in _PROJECT_CACHE else "render_audio" + source.suffix.lower()
                if not (staging / render_audio_name).exists():
                    shutil.copy2(source, staging / render_audio_name)
            else:
                render_audio_name = next((name for name in ("narration_mixed.wav", "narration_mixed.mp3") if (staging / name).exists()), audio_name)
            if render_audio_name and (archive / "render_audio.json").exists():
                manifest = json.loads(_relative_file(archive, "render_audio.json").read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("Invalid archived soundtrack manifest")
                manifest["path"] = render_audio_name
                _write_json(staging / "render_audio.json", manifest)
            if (archive / "timeline.json").exists():
                timeline = json.loads(_relative_file(archive, "timeline.json").read_text(encoding="utf-8"))
                from video.timeline import script_signature
                if not isinstance(timeline, dict) or timeline.get("script_signature") != script_signature(script):
                    raise ValueError("Archived timing does not match the script")
                _write_json(staging / "timeline.json", timeline)
            if mode in {"motion", "hybrid"} and (archive / "scene_plan.json").exists():
                plan = json.loads(_relative_file(archive, "scene_plan.json").read_text(encoding="utf-8"))
                if not isinstance(plan, dict) or not isinstance(plan.get("scenes"), list):
                    raise ValueError("Invalid archived storyboard")
                for scene in plan["scenes"]:
                    for kind in ("footage", "image"):
                        if scene.get(kind):
                            relative = scene[kind]["path"]
                            source = _archived_media(archive, relative)
                            destination = staging / relative
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source, destination)
                            scene[kind]["path"] = str(output / relative)
                _write_json(staging / "scene_plan.json", plan)
                _copy_local_tree(archive / "motion", staging / "motion")
                if (archive / "visual_qa.json").exists():
                    qa = json.loads(_relative_file(archive, "visual_qa.json").read_text(encoding="utf-8"))
                    _write_json(staging / "visual_qa.json", qa)
            raw_image_map = _json(archive / "image_map.json")
            if isinstance(raw_image_map, dict):
                for section, paths in raw_image_map.items():
                    if not isinstance(paths, list):
                        raise ValueError("Invalid archived image list")
                    restored_images[section] = []
                    for relative in paths:
                        source = _relative_file(archive, relative)
                        if Path(relative).parts[0] != "images" or source.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                            raise ValueError("Archived images must be in the images directory")
                        destination = staging / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, destination)
                        restored_images[section].append(str(output / relative))
            else:
                photos = sorted(glob.glob(lib_path(pid, "photos", "*.jpg")))
                if photos:
                    image_reuse_note = "This old archive has photos but no section map. Recovered photos were distributed across scenes; check that each picture matches its narration before publishing."
                for index, section in enumerate(script.get("sections", [])):
                    if not photos:
                        break
                    section_id = section.get("id", index + 1)
                    from video.stock import section_key
                    relative = f"images/section_{section_key(section_id)}_img01.jpg"
                    destination = staging / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(photos[index % len(photos)], destination)
                    restored_images[str(section_id)] = [str(output / relative)]
            _write_json(staging / "image_map.json", {
                str(section): [str(Path(path).relative_to(output)) for path in paths]
                for section, paths in restored_images.items()
            })
            video_credits = _json(archive / "video_credits.json", [])
            if not isinstance(video_credits, list):
                raise ValueError("Invalid video credit manifest")
            for credit in video_credits:
                if credit.get("path"):
                    relative = credit["path"]
                    source = _archived_media(archive, relative)
                    destination = staging / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if not destination.exists():
                        shutil.copy2(source, destination)
                    credit["path"] = str(output / relative)
            _write_json(staging / "video_credits.json", video_credits)
            _replace_project_cache(staging, output)
        activate_profile(profile["id"])
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not load project: {e}"})
    cover = lib_path(pid, "cover.jpg")
    from studio.profile_context import capture_profile_settings
    with config.profile_context(capture_profile_settings(profile["id"])):
        restored_description = with_credit(meta.get("description") or build_description(
            script, research, meta.get("video_type", "normal"), timeline=timeline))
    state.update({
        "profile_id": profile["id"],
        "profile_name": profile["name"],
        "schedule_id": meta.get("schedule_id"),
        "script":      script,
        "research":    research,
        "video_type":  meta.get("video_type", "normal"),
        "video_path":  lib_path(pid, "video.mp4") if not meta.get("preview_only") and os.path.isfile(lib_path(pid, "video.mp4")) else None,
        "thumb_path":  lib_path(pid, "thumbnail.jpg") if os.path.isfile(lib_path(pid, "thumbnail.jpg")) else None,
        "thumb_bg":    None if glob.glob(lib_path(pid, "photos", "*.jpg")) else (cover if os.path.exists(cover) else None),
        "image_map":   restored_images,
        "image_reuse_note": image_reuse_note,
        "audio_path":  str(output / audio_name) if audio_name else None,
        "render_audio_path": str(output / render_audio_name) if render_audio_name else None,
        "visual_mode": mode,
        "visual_plan": plan,
        "timeline": timeline,
        "visual_qa": qa,
        "preview_path": lib_path(pid, "video.mp4") if meta.get("preview_only") and os.path.isfile(lib_path(pid, "video.mp4")) else None,
        "preview_only": bool(meta.get("preview_only")),
        "preflight": None,
        "visual_revision": state.get("visual_revision", 0) + 1,
        "description": restored_description,
        "yt_url":      meta.get("yt_url"),
        "library_id":  pid,
        "thumb_style": meta.get("thumb_style"),
        "thumb_text":  meta.get("thumb_text"),
        "thumb_bg_index": 0,
        "error":       None,
        "workspace_warning": None,
        "workspace_restored": False,
        "started_at": None,
        "stop_requested": False,
        "upload_warning": None,
        "upload_pct": 0,
    })
    state["stages"] = {name: "done" for name in state["stages"]}
    if not audio_name:
        state["stages"]["narration"] = "pending"
    if not plan:
        state["stages"]["visuals"] = "skipped"
    from studio.workspace import checkpoint_workspace
    checkpoint_workspace(rendered_kind="draft" if state.get("preview_path") else "final" if state.get("video_path") else None)
    return jsonify({"ok": True})

@bp.route("/api/library/<pid>", methods=["DELETE"])
def library_delete(pid):
    with job_lock:
        if state.get("running") or state.get("visual_editing") or state.get("uploading"):
            return jsonify({"ok": False, "error": "Wait for the current job to finish before deleting a project."}), 409
        path = lib_path(pid)
        if not os.path.isdir(path):
            return jsonify({"ok": False, "error": "Not found"})
        if state.get("library_id") == pid:
            state["library_id"] = None
            for key in ("video_path", "thumb_path", "thumb_bg"):
                if state.get(key) and Path(state[key]).resolve().is_relative_to(Path(path)):
                    state[key] = None
        shutil.rmtree(path)
        return jsonify({"ok": True})
