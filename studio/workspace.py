"""Recover the current local project without generating or fetching anything.

The small checkpoint records which renders still belong to the current source.
Old MP4 files may deliberately remain on disk after edits; their existence alone
is never proof that the current storyboard has been rendered.
"""
from copy import deepcopy
from collections import OrderedDict
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

import config
from studio.revisions import plan_revision
from studio.state import job_lock, state

CHECKPOINT = "workspace.json"
_JSON_LIMIT = 8 * 1024 * 1024
_MEDIA_LIMIT = 8 * 1024 * 1024 * 1024
_AUDIO = {".wav", ".mp3", ".m4a", ".ogg"}
_PICTURES = {".jpg", ".jpeg", ".png", ".webp"}
_fingerprints = OrderedDict()
_fingerprint_lock = threading.Lock()


def has_valid_storyboard(plan):
    if not isinstance(plan, dict) or not plan.get("scenes"):
        return False
    from agents.visual_director import validate_plan
    try:
        validate_plan(plan)
        return True
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False


def _local_file(root, value, extensions=None, *, relative=False):
    if not isinstance(value, (str, Path)) or not str(value) or "://" in str(value):
        return None
    supplied = Path(value).expanduser()
    if relative and (supplied.is_absolute() or "\\" in str(value) or ":" in str(value)):
        return None
    if ".." in supplied.parts:
        return None
    candidates = [supplied] if supplied.is_absolute() else [root / supplied]
    if not relative and not supplied.is_absolute():
        candidates.append(supplied.absolute())
    for path in candidates:
        try:
            resolved = path.resolve()
            if (resolved != path.absolute() or not resolved.is_relative_to(root)
                    or not resolved.is_file() or resolved.stat().st_size <= 0
                    or (extensions and resolved.suffix.lower() not in extensions)):
                continue
            return resolved
        except (OSError, ValueError, RuntimeError):
            continue
    return None


def _read_json(root, name):
    path = _local_file(root, name, {".json"}, relative=True)
    if not path:
        return None
    try:
        if path.stat().st_size > _JSON_LIMIT:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError):
        return None


def _fingerprint(root, path):
    if path is None:
        return None
    before = path.stat()
    if not 0 < before.st_size <= _MEDIA_LIMIT:
        raise ValueError("A saved media file is too large to verify automatically.")
    cache_key = (str(path), before.st_dev, before.st_ino, before.st_size,
                 before.st_mtime_ns, before.st_ctime_ns)
    with _fingerprint_lock:
        cached = _fingerprints.get(cache_key)
        if cached:
            _fingerprints.move_to_end(cache_key)
            return {"path": path.relative_to(root).as_posix(), **cached}
    digest = hashlib.sha256()
    remaining = _MEDIA_LIMIT
    with path.open("rb") as handle:
        while block := handle.read(min(1024 * 1024, remaining + 1)):
            remaining -= len(block)
            if remaining < 0:
                raise ValueError("A saved media file changed while it was being checked.")
            digest.update(block)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("A saved media file changed while it was being checked.")
    identity = {"bytes": after.st_size, "sha256": digest.hexdigest()}
    with _fingerprint_lock:
        _fingerprints[cache_key] = identity
        _fingerprints.move_to_end(cache_key)
        while len(_fingerprints) > 256:
            _fingerprints.popitem(last=False)
    return {"path": path.relative_to(root).as_posix(), **identity}


def _script_signature(script):
    from video.timeline import script_signature
    if (not isinstance(script, dict) or not isinstance(script.get("sections"), list)
            or not 1 <= len(script["sections"]) <= 80
            or any(not isinstance(section, dict) or not isinstance(section.get("narration"), str)
                   for section in script["sections"])):
        return None
    return script_signature(script)


def _binding(snapshot, root):
    signature = _script_signature(snapshot.get("script"))
    plan, timeline = snapshot.get("visual_plan"), snapshot.get("timeline") or {}
    if not signature or (snapshot.get("visual_mode") in {"motion", "hybrid"} and not has_valid_storyboard(plan)):
        return None
    narration = _local_file(root, snapshot.get("audio_path"), _AUDIO)
    soundtrack = _local_file(root, snapshot.get("render_audio_path") or snapshot.get("audio_path"), _AUDIO)
    if not narration or not soundtrack:
        return None
    fingerprints = {}

    def identify(path):
        key = str(path)
        if key not in fingerprints:
            fingerprints[key] = _fingerprint(root, path)
        return fingerprints[key]

    narration_identity = identify(narration)
    if (timeline.get("script_signature") != signature
            or timeline.get("audio_sha256") != narration_identity["sha256"]):
        return None
    assets = {}
    for scene in (plan or {}).get("scenes", []):
        for kind, extensions in (("image", _PICTURES), ("footage", {".mp4"})):
            if scene.get(kind):
                path = _local_file(root, scene[kind].get("path"), extensions)
                if not path:
                    return None
                assets[path.relative_to(root).as_posix()] = identify(path)
    return {"script_signature": signature, "plan_revision": plan_revision(plan),
            "timeline_signature": timeline.get("timeline_signature"),
            "narration": narration_identity, "soundtrack": identify(soundtrack), "assets": assets}


def _image_map(root, values):
    result = {}
    if not isinstance(values, dict):
        return result
    for section, images in list(values.items())[:80]:
        images = images if isinstance(images, (list, tuple)) else [images]
        paths = [_local_file(root, value, _PICTURES) for value in images[:20]]
        result[str(section)] = list(dict.fromkeys(str(path) for path in paths if path))
    return result


def checkpoint_workspace(output_dir=None, *, rendered_kind=None):
    """Persist current pointers; only a completed render can gain a new receipt."""
    root = Path(output_dir or config.OUTPUT_DIR).expanduser().resolve()
    try:
        with job_lock:
            snapshot = deepcopy(state)
        previous = _read_json(root, CHECKPOINT) or {}
        old_renders = previous.get("renders") if isinstance(previous.get("renders"), dict) else {}
        binding = _binding(snapshot, root)
        paths = {}
        for key, extensions in (("audio_path", _AUDIO), ("render_audio_path", _AUDIO),
                                ("thumb_path", _PICTURES), ("thumb_bg", _PICTURES)):
            path = _local_file(root, snapshot.get(key), extensions)
            paths[key] = path.relative_to(root).as_posix() if path else None
        renders = {}
        for kind, key in (("draft", "preview_path"), ("final", "video_path")):
            path = _local_file(root, snapshot.get(key), {".mp4"})
            receipt = old_renders.get(kind)
            if not binding or not path:
                continue
            if rendered_kind == kind:
                renders[kind] = {"binding": binding, "artifact": _fingerprint(root, path)}
            elif (isinstance(receipt, dict) and receipt.get("binding") == binding
                  and (receipt.get("artifact") or {}).get("path") == path.relative_to(root).as_posix()):
                renders[kind] = receipt
        from agents.visual_director import plan_uses_project_photos
        graphics_only = snapshot.get("visual_mode") == "motion" and not plan_uses_project_photos(snapshot.get("visual_plan"))
        images = {} if graphics_only else _image_map(root, snapshot.get("image_map"))
        saved = {"version": 1, "profile_id": snapshot.get("profile_id") or "default",
                 "script_signature": _script_signature(snapshot.get("script")),
                 "visual_mode": snapshot.get("visual_mode"), "video_type": snapshot.get("video_type", "normal"),
                 "paths": paths, "renders": renders,
                 "image_map": {key: [Path(path).relative_to(root).as_posix() for path in values]
                               for key, values in images.items()}}
        for key in ("description", "thumb_style", "thumb_text", "thumb_bg_index", "library_id", "yt_url", "image_reuse_note"):
            value = snapshot.get(key)
            if value is None or isinstance(value, (str, int)):
                saved[key] = value
        root.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".workspace-", suffix=".json", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(saved, handle, ensure_ascii=False, allow_nan=False)
                handle.write("\n")
            os.replace(temporary, root / CHECKPOINT)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        state["workspace_warning"] = f"The current project could not be saved for restart recovery: {exc}"
        return False


def _probe_render(path, expected):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not isinstance(expected, dict):
        return False
    try:
        result = subprocess.run([ffprobe, "-v", "error", "-show_entries",
            "stream=codec_type,width,height,avg_frame_rate:format=duration", "-of", "json", str(path)],
            check=True, capture_output=True, timeout=15)
        if len(result.stdout) > 65536:
            return False
        info = json.loads(result.stdout)
        streams = info.get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
        duration = float(info.get("format", {}).get("duration", 0))
        fps = float(Fraction(video.get("avg_frame_rate", "0")))
        return (any(stream.get("codec_type") == "audio" for stream in streams)
                and (video.get("width"), video.get("height")) == (expected.get("width"), expected.get("height"))
                and math.isfinite(duration) and duration > 0
                and abs(duration - float(expected["duration"])) <= max(.35, 2 / float(expected["fps"]))
                and abs(fps - float(expected["fps"])) < .001)
    except (OSError, ValueError, TypeError, KeyError, ZeroDivisionError, subprocess.SubprocessError):
        return False


def _matching_timeline(script, timeline, audio):
    try:
        return (isinstance(timeline, dict) and timeline.get("script_signature") == _script_signature(script)
                and audio is not None and timeline.get("audio_sha256") == audio["sha256"]
                and isinstance(timeline.get("timeline_signature"), str)
                and 0 < float(timeline["duration"]) <= 7200 and 1 <= float(timeline["fps"]) <= 60
                and 64 <= int(timeline["width"]) <= 4096 and 64 <= int(timeline["height"]) <= 4096)
    except (TypeError, ValueError, KeyError):
        return False


def restore_workspace(output_dir=None):
    """Restore an empty editor from known local files, retaining their channel."""
    with job_lock:
        if any(state.get(key) for key in ("running", "visual_editing", "uploading", "script", "research",
                                         "visual_plan", "audio_path", "video_path", "preview_path")):
            return False
        root = Path(output_dir or config.OUTPUT_DIR).expanduser().resolve()
        warnings = []
        try:
            script, research = _read_json(root, "script.json"), _read_json(root, "research.json")
            if not script and not research:
                if any(os.path.lexists(root / name) for name in ("script.json", "research.json", "scene_plan.json", CHECKPOINT)):
                    state["workspace_warning"] = "Saved project files could not be read. Open a saved video from My videos, or start a new project."
                return False
            from studio.channels import get_profile
            from studio.project_identity import cached_profile_owner
            profile = get_profile(cached_profile_owner(root) or "default")
            checkpoint_exists = os.path.lexists(root / CHECKPOINT)
            checkpoint = _read_json(root, CHECKPOINT)
            if checkpoint and (checkpoint.get("version") != 1 or checkpoint.get("profile_id") != profile["id"]):
                checkpoint = None
            if checkpoint_exists and checkpoint is None:
                warnings.append("Saved video information is unreadable or belongs to another project. Render a new draft before publishing.")
            checkpoint = checkpoint or {}
            signature = _script_signature(script)
            same_script = bool(signature and checkpoint.get("script_signature") == signature)
            saved_paths = checkpoint.get("paths") if same_script and isinstance(checkpoint.get("paths"), dict) else {}
            timeline = _read_json(root, "timeline.json")
            candidates = [saved_paths.get("audio_path"), *("narration" + suffix for suffix in (".wav", ".mp3", ".m4a", ".ogg"))]
            audio_path, audio_identity = None, None
            for candidate in dict.fromkeys(value for value in candidates if isinstance(value, str)):
                path = _local_file(root, candidate, _AUDIO)
                if not path:
                    continue
                identity = _fingerprint(root, path)
                if timeline and not _matching_timeline(script, timeline, identity):
                    continue
                audio_path, audio_identity = path, identity
                break
            if timeline and not _matching_timeline(script, timeline, audio_identity):
                timeline = None
                warnings.append("Saved narration timing no longer matches the script and audio. Open its original saved video or rebuild the narration and storyboard.")
            plan = _read_json(root, "scene_plan.json")
            saved_mode = plan.get("mode") if plan else None
            if plan:
                if (not has_valid_storyboard(plan) or not timeline or plan.get("script_signature") != signature
                        or plan.get("timeline_signature") != timeline.get("timeline_signature")
                        or any(plan.get(key) != timeline.get(key) for key in ("fps", "width", "height"))
                        or abs(plan["duration"] - timeline["duration"]) >= .001):
                    plan = None
                    warnings.append("The saved storyboard is incomplete or no longer matches narration. Run the Visuals step to rebuild it.")
                else:
                    for scene in plan["scenes"]:
                        for kind, extensions in (("image", _PICTURES), ("footage", {".mp4"})):
                            if scene.get(kind) and not _local_file(root, scene[kind].get("path"), extensions):
                                warnings.append("Some saved scene media is missing or outside this project. Restore its pictures or run Visuals again before rendering.")
            elif os.path.lexists(root / "scene_plan.json"):
                warnings.append("The saved storyboard could not be read. Run the Visuals step to rebuild it.")
            soundtrack = audio_path
            soundtrack_manifest = _read_json(root, "render_audio.json") or {}
            if audio_identity and soundtrack_manifest.get("audio_sha256") == audio_identity["sha256"]:
                soundtrack = _local_file(root, soundtrack_manifest.get("path"), _AUDIO, relative=True) or audio_path
            elif saved_paths.get("render_audio_path") == saved_paths.get("audio_path"):
                soundtrack = audio_path
            mode = plan["mode"] if plan else checkpoint.get("visual_mode") if same_script else saved_mode
            if mode not in {"motion", "hybrid", "classic"}:
                mode = "motion" if os.path.lexists(root / "scene_plan.json") else "classic"
            images = {}
            from agents.visual_director import plan_uses_project_photos
            if mode != "motion" or plan_uses_project_photos(plan):
                from studio.visual_assets import validate_binding
                map_exists = os.path.lexists(root / "image_map.json")
                raw_images = _read_json(root, "image_map.json") if map_exists else (
                    checkpoint.get("image_map") if same_script else None)
                # A broken current manifest is not permission to rediscover
                # old pictures from a checkpoint, plan or section filenames.
                photo_binding = False if map_exists and raw_images is None else validate_binding(
                    root, raw_images or {}, script, profile["id"])
                if photo_binding is False:
                    warnings.append("The saved photo map no longer matches this script, channel or local files. "
                                    "Its cached photos were not restored; regenerate images or reload the original saved project.")
                else:
                    images = _image_map(root, raw_images)
                    if photo_binding is None and not any(images.values()) and plan:
                        # Old storyboards may predate image_map.json. Their
                        # exact references remain legacy, requiring photo opt-in
                        # for a new plan; no filename discovery is performed.
                        images = _image_map(root, {section["id"]: [scene["image"]["path"] for scene in plan["scenes"]
                                                 if scene["section_id"] == section["id"] and scene.get("image")]
                                                 for section in plan["sections"]})
            video_type = "shorts" if timeline and timeline["height"] > timeline["width"] else (script or {}).get("video_type", "normal")
            if video_type not in {"normal", "shorts"}:
                video_type = "normal"
            recovered = {"script": script, "research": research or {}, "profile_id": profile["id"],
                         "profile_name": profile["name"], "visual_mode": mode, "video_type": video_type,
                         "visual_plan": plan, "timeline": timeline, "image_map": images,
                         "audio_path": str(audio_path) if audio_path else None,
                         "render_audio_path": str(soundtrack) if soundtrack else None,
                         "video_path": None, "preview_path": None, "visual_qa": None,
                         "library_id": None, "yt_url": None, "preview_only": bool(plan), "error": None}
            if same_script:
                for key in ("description", "thumb_style", "thumb_text", "thumb_bg_index", "image_reuse_note"):
                    value = checkpoint.get(key)
                    if value is not None and isinstance(value, (str, int)):
                        recovered[key] = value
                for key in ("thumb_path", "thumb_bg"):
                    path = _local_file(root, saved_paths.get(key), _PICTURES, relative=True)
                    recovered[key] = str(path) if path else None
            binding = _binding(recovered, root)
            renders = checkpoint.get("renders") or {}
            if not isinstance(renders, dict):
                renders = {}
            for kind, key in (("draft", "preview_path"), ("final", "video_path")):
                receipt = renders.get(kind)
                if not isinstance(receipt, dict) or not binding or receipt.get("binding") != binding:
                    continue
                artifact = receipt.get("artifact") or {}
                path = _local_file(root, artifact.get("path"), {".mp4"}, relative=True) if isinstance(artifact, dict) else None
                if path and _fingerprint(root, path) == artifact and _probe_render(path, plan or timeline):
                    recovered[key] = str(path)
            migrated_draft = False
            if not checkpoint_exists and plan and binding:
                path = _local_file(root, "motion_preview.mp4", {".mp4"}, relative=True)
                sources = [root / name for name in ("script.json", "research.json", "scene_plan.json", "timeline.json",
                           "render_audio.json", "image_map.json", "motion/index.html", "motion/project.json") if (root / name).is_file()]
                sources += [audio_path, soundtrack]
                sources += [_local_file(root, asset, _PICTURES | {".mp4"}) for asset in binding["assets"]]
                fresh = path and all(source and path.stat().st_mtime_ns >= source.stat().st_mtime_ns for source in sources)
                if fresh and _probe_render(path, plan):
                    recovered["preview_path"] = str(path)
                    migrated_draft = True
            if recovered["video_path"]:
                recovered["preview_only"] = False
                if same_script:
                    recovered["yt_url"] = checkpoint.get("yt_url")
            if same_script and (recovered["video_path"] or recovered["preview_path"]):
                recovered["library_id"] = checkpoint.get("library_id")
            if plan and not (recovered["preview_path"] or recovered["video_path"]):
                warnings.append("Your saved storyboard and narration were recovered. Choose Render draft MP4 in Review to make a current playable video.")
            elif not plan and not recovered["video_path"] and not warnings:
                if signature:
                    warnings.append("Your saved script was recovered, but no video is ready. Continue with narration and visuals, then render the video.")
                else:
                    warnings.append("Your saved project is incomplete. Generate a script, narration and visuals before rendering a video.")
            if recovered["video_path"] or recovered["preview_path"]:
                recovered["visual_qa"] = _read_json(root, "visual_qa.json")
            state.update(recovered, workspace_warning=" ".join(dict.fromkeys(warnings)) or None, workspace_restored=True)
            state["stages"].update(preparation="done", research="done" if research else "pending",
                script="done" if signature else "pending", images="done" if any(images.values()) else "pending",
                narration="done" if audio_path else "pending", visuals="done" if plan else "pending",
                video="done" if recovered["video_path"] or recovered["preview_path"] else "pending")
            if not checkpoint_exists:
                checkpoint_workspace(root, rendered_kind="draft" if migrated_draft else None)
            return True
        except (OSError, ValueError, TypeError, KeyError, AttributeError, LookupError) as exc:
            state["workspace_warning"] = f"The saved workspace could not be recovered: {exc}. Open the original project from My videos, or start a new project."
            return False
