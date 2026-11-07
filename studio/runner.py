"""Pipeline worker: measured speech owns the clock, render-only reuses edits."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from contextlib import nullcontext
from copy import deepcopy

import config
from studio.description import build_description
from studio.library import archive_project
from studio.project_identity import write_project_identity
from studio.state import Capture, job_lock, log, log_queue, stage, state, stop_event
from studio.thumbs import make_thumbnail
from studio.workspace import checkpoint_workspace


def _load_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _save_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
                         encoding="utf-8")
    temporary.replace(path)


def _save_image_map(output, image_map):
    """Persist project-relative photos so skipped steps survive a restart."""
    root = output.resolve()
    saved = {}
    for section, paths in image_map.items():
        saved[str(section)] = []
        for value in (paths if isinstance(paths, list) else [paths]):
            if not value:
                continue
            path = Path(value).resolve()
            if path.is_file() and root in path.parents:
                saved[str(section)].append(path.relative_to(root).as_posix())
    _save_json(output / "image_map.json", saved)


def _load_image_map(output):
    root = output.resolve()
    saved = _load_json(output / "image_map.json") or {}
    result = {}
    for section, paths in saved.items():
        if not isinstance(paths, list):
            continue
        result[str(section)] = []
        for value in paths:
            if not isinstance(value, str):
                continue
            path = (root / value).resolve()
            if (path.is_file() and root in path.parents
                    and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}):
                result[str(section)].append(str(path))
    return result


def _invalidate(output, *, script=False, audio=False, visuals=False):
    """Invalidate generated caches after their source successfully changes."""
    state.update(video_path=None, preview_path=None, visual_qa=None,
                 library_id=None, yt_url=None, upload_warning=None)
    names = ["visual_qa.json"]
    if script:
        state.update(script=None, image_map={}, image_reuse_note=None, thumb_path=None, thumb_bg=None,
                     thumb_text=None, thumb_bg_index=0)
        names += ["script.json", "image_map.json", "credits.json", "video_credits.json", "audio_credits.json"]
    if audio:
        state.update(audio_path=None, render_audio_path=None)
        names += ["narration.wav", "narration.mp3", "narration_mixed.mp3", "narration_mixed.wav",
                  "narration_effects.wav", "narration.srt", "narration.timings.json", "render_audio.json",
                  "audio_credits.json"]
        # These assets belong to the old script, even when a user unchecks the
        # Images step on the next run. Never reuse them as the new script's B-roll.
        state.update(image_map={}, thumb_path=None, thumb_bg=None, thumb_text=None, thumb_bg_index=0)
        names += ["image_map.json", "credits.json", "video_credits.json"]
        images = Path(config.IMAGES_DIR).resolve()
        root = output.resolve()
        if images != root and root in images.parents and images.is_dir() and not Path(config.IMAGES_DIR).is_symlink():
            shutil.rmtree(images)
            images.mkdir(parents=True, exist_ok=True)
    if visuals or audio:
        state.update(timeline=None, visual_plan=None)
        names += ["timeline.json", "scene_plan.json"]
        motion = output / "motion"
        if motion.is_dir() and not motion.is_symlink():
            shutil.rmtree(motion)
    for name in names:
        (output / name).unlink(missing_ok=True)
    checkpoint_workspace(output)


def _audio_path(output):
    candidates = [state.get("audio_path"), *(output / ("narration" + suffix) for suffix in (".wav", ".mp3", ".m4a", ".ogg"))]
    root = output.resolve()
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()
                 and root in Path(p).resolve().parents), None)


def _matching_plan(plan, timeline, mode, use_project_photos=None):
    if not isinstance(plan, dict) or plan.get("script_signature") != timeline["script_signature"]:
        return False
    if plan.get("timeline_signature") != timeline["timeline_signature"]:
        return False
    if plan.get("mode") != mode or any(plan.get(k) != timeline[k] for k in ("fps", "width", "height")):
        return False
    from agents.visual_director import plan_uses_project_photos
    photos = mode != "motion" if use_project_photos is None else use_project_photos
    if plan_uses_project_photos(plan) != photos:
        return False
    try:
        return abs(float(plan["duration"]) - timeline["duration"]) < .001
    except (KeyError, TypeError, ValueError):
        return False


def _selected_audio(profile_id, asset_id, kind):
    from studio.audio_library import resolve_audio_asset
    try:
        return resolve_audio_asset(profile_id, asset_id, kind=kind)
    except (ValueError, OSError) as exc:
        log(f"Selected {kind} is unavailable: {exc}. Continuing without that asset.", "warn")
        return None


def _mix_project_audio(narration_path, timeline, output, profile_id):
    """Apply current channel choices to clean narration, retaining applied credits."""
    final_audio = str(narration_path)
    credits = []

    def credit(asset, volume, mode):
        fields = ("id", "kind", "title", "filename", "source_url", "creator", "license_note", "content_id")
        credits.append({**{key: asset.get(key, "") for key in fields}, "volume": volume, "mode": mode})

    if config.MUSIC_ENABLED:
        from video.music import mix_music_with_narration
        selected_id = getattr(config, "MUSIC_TRACK", "")
        asset = _selected_audio(profile_id, selected_id, "music") if selected_id else None
        if not selected_id or asset is not None:
            log("Mixing selected background music…" if asset else "Mixing background music from your local music folder…")
            volume = getattr(config, "MUSIC_VOLUME", .08)
            final_audio = mix_music_with_narration(
                narration_path=narration_path, output_path=str(output / "narration_mixed.wav"),
                music_volume=volume, library_dir=getattr(config, "MUSIC_LIBRARY_DIR", ""),
                track_path=asset["path"] if asset else None, stop_event=stop_event)
            if asset and Path(final_audio).resolve() != Path(narration_path).resolve():
                credit(asset, volume, "bed")
    if getattr(config, "SFX_ENABLED", False):
        selected_id = getattr(config, "SFX_TRACK", "")
        asset = _selected_audio(profile_id, selected_id, "sfx") if selected_id else None
        if not selected_id:
            log("Choose an imported sound effect to apply transitions or ambience. Keeping the prepared soundtrack.", "warn")
        if asset is not None:
            from video.sound_effects import mix_sound_effects
            volume, mode = getattr(config, "SFX_VOLUME", .08), getattr(config, "SFX_MODE", "transitions")
            log(f"Applying the selected sound effect as {mode}…")
            prepared = final_audio
            final_audio = mix_sound_effects(
                narration_path=narration_path, output_path=str(output / "narration_effects.wav"),
                effect_path=asset["path"], soundtrack_path=prepared, sections=timeline.get("sections", []),
                effect_volume=volume, mode=mode, stop_event=stop_event)
            if Path(final_audio).resolve() != Path(prepared).resolve():
                credit(asset, volume, mode)
    if credits:
        _save_json(output / "audio_credits.json", {
            "version": 1, "audio_sha256": timeline["audio_sha256"],
            "path": Path(final_audio).resolve().relative_to(output.resolve()).as_posix(), "assets": credits,
        })
    else:
        (output / "audio_credits.json").unlink(missing_ok=True)
    return final_audio


def _review_video(video_path, plan, output):
    """Optional remote frame review; disabled unless explicitly enabled."""
    if not getattr(config, "VISUAL_REVIEW_ENABLED", False):
        return {"status": "disabled", "passed": None, "issues": []}
    from apis.openrouter import review_frames
    frames = []
    try:
        folder = output / "visual_review"
        folder.mkdir(exist_ok=True)
        scenes = plan["scenes"]
        count = min(4, len(scenes))
        indices = sorted({round(i * (len(scenes) - 1) / max(1, count - 1)) for i in range(count)})
        for index in indices:
            if stop_event.is_set():
                raise InterruptedError("Stopped before visual review")
            scene = scenes[index]
            stamp = scene["start"] + scene["duration"] * .65
            target = folder / f"frame-{index}.jpg"
            subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-y", "-v", "error", "-ss", str(stamp),
                            "-i", str(video_path), "-frames:v", "1", "-vf", "scale=960:-2",
                            str(target)], check=True, capture_output=True, timeout=30)
            frames.append(str(target))
        log("Reviewing sampled frames with the selected free vision model…")
        return review_frames(frames,
                             "Check these video frames for unreadable text, clipping, overlaps, "
                             "missing visuals and poor contrast. Captions may cover the reserved "
                             "bottom area. Report visible problems only; this is not a fact check.",
                             model=getattr(config, "VISUAL_REVIEW_MODEL", "openrouter/free"))
    except InterruptedError:
        raise
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        log(f"Visual review unavailable: {exc}", "warn")
        return {"status": "unavailable", "reason": "frame_review_failed", "passed": None, "issues": []}


def run_pipeline_thread(steps: list, topic_override: str = "", video_type: str = "normal",
                        focus_angle: str = "", use_trends: bool = False,
                        visual_mode=None, preview_only=False, profile_id=None,
                        profile_values=None, schedule_id=None, release_job=True,
                        use_project_photos=None):
    """Capture a channel for the whole job, independent of the Home selection."""
    values = profile_values
    if profile_id and values is None:
        from studio.profile_context import capture_profile_settings
        values = capture_profile_settings(profile_id)
    state["job_profile_values"] = deepcopy(values)
    with (config.profile_context(values) if values is not None else nullcontext()):
        return _run_pipeline(steps, topic_override, video_type, focus_angle, use_trends,
                             visual_mode, preview_only, profile_id, schedule_id, release_job,
                             use_project_photos)


def _run_pipeline(steps, topic_override, video_type, focus_angle, use_trends,
                  visual_mode, preview_only, profile_id, schedule_id, release_job,
                  use_project_photos=None):
    orig_stdout = sys.stdout
    sys.stdout = Capture(orig_stdout)
    mode = visual_mode or getattr(config, "VISUAL_MODE", "motion")
    output = Path(config.OUTPUT_DIR)
    state.update(running=True, error=None, started_at=time.time(), visual_mode=mode,
                 preview_only=preview_only, video_type=video_type, schedule_id=schedule_id,
                 workspace_restored=False, workspace_warning=None)
    # /api/run clears the event before starting this worker. Clearing it here
    # would lose a Stop click arriving immediately after Start.

    def check_stop(after_stage):
        if stop_event.is_set():
            raise InterruptedError(f"Stopped after {after_stage}")

    def complete():
        log("Pipeline complete!", "info")
        log_queue.put(("done", ""))

    try:
        stage("preparation", "running")
        from studio.preflight import check_environment, validate_topic_steps
        validate_topic_steps(steps, topic_override)
        if use_project_photos is not None and not isinstance(use_project_photos, bool):
            raise ValueError("Use project photos must be true or false.")
        photos = mode != "motion" if use_project_photos is None else use_project_photos
        images_enabled = mode == "classic" or photos
        preserve_packaging = (not set(steps) & {"research", "script", "narration"}
                              and not ("images" in steps and images_enabled))
        checks = check_environment([step for step in steps if step != "images" or images_enabled], mode)
        state["preflight"] = checks
        if not checks["ok"]:
            failures = [c["message"] + (". Fix: " + c["fix"] if c.get("fix") else "")
                        for c in checks["checks"] if not c["ok"]]
            raise RuntimeError("Preparation failed: " + " | ".join(failures))
        output.mkdir(parents=True, exist_ok=True)
        Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
        stage("preparation", "done")
        check_stop("preparation")

        if "research" in steps:
            stage("research", "running")
            from agents.researcher import research_topic
            trend_context = None
            if use_trends and not topic_override:
                try:
                    from apis.trends import headlines
                    trend_context = headlines()
                except Exception as exc:
                    log(f"Trend Radar unavailable: {exc}", "warn")
            research = research_topic(config.CHANNEL_DESCRIPTION, topic_override=topic_override,
                                      focus_angle=focus_angle, trend_context=trend_context)
            check_stop("research")
            _invalidate(output, script=True, audio=True, visuals=True)
            if profile_id:
                from studio.channels import get_profile
                profile = get_profile(profile_id)
                state.update(profile_id=profile_id, profile_name=profile["name"])
            state["research"] = research
            for warning in research.get("warnings", []):
                log(warning, "warn")
            _save_json(output / "research.json", research)
            write_project_identity(output, state.get("profile_id") or "default",
                                   state.get("profile_name") or config.CHANNEL_NAME)
            stage("research", "done")
            log_queue.put(("research", ""))
            log(f"Topic: {research.get('video_title', topic_override)}")
        elif not state.get("research"):
            state["research"] = _load_json(output / "research.json")
        if not set(steps) - {"research"}:
            complete()
            return

        if "script" in steps:
            stage("script", "running")
            if not state.get("research"):
                raise ValueError("No research data. Run the Research step first.")
            from agents.scriptwriter import write_script
            log(f"Generating {'Shorts' if video_type == 'shorts' else 'normal'} script…")
            script = write_script(state["research"], video_type=video_type)
            check_stop("script")
            _invalidate(output, audio=True, visuals=True)
            state.update(script=script, image_map={}, thumb_path=None, thumb_bg=None)
            _save_json(output / "script.json", script)
            write_project_identity(output, state.get("profile_id") or profile_id or "default",
                                   state.get("profile_name") or config.CHANNEL_NAME)
            stage("script", "done")
            log(f"Script: {len(script.get('sections', []))} sections")
        elif not state.get("script"):
            state["script"] = _load_json(output / "script.json")
        script = state.get("script")
        if not script:
            raise ValueError("No script data. Run the Script step first.")
        check_stop("script")
        if not set(steps) - {"research", "script"}:
            state["description"] = build_description(script, state.get("research") or {}, video_type)
            complete()
            return

        if mode == "classic":
            state.update(visual_plan=None, visual_qa=None, preview_path=None)
        if not images_enabled:
            stage("images", "skipped")
        elif "images" in steps:
            stage("images", "running")
            from video.stock import download_images
            state["image_map"] = download_images(script, str(output))
            _save_image_map(output, state["image_map"])
            state["image_reuse_note"] = None
            _invalidate(output)
            stage("images", "done")
        elif not state.get("image_map") and not set(steps) & {"research", "script"}:
            # Filename matches are safe only when redoing the current project.
            # A fresh script must not inherit an older project's section photos.
            from video.stock import section_images
            saved_images = _load_image_map(output)
            state["image_map"] = {s["id"]: saved_images.get(str(s["id"]))
                                  or section_images(config.IMAGES_DIR, s["id"])
                                  for s in script.get("sections", [])}
        if mode != "classic" and photos and any(state["image_map"].values()):
            log("Reusing the original visual pipeline's pictures in the motion storyboard.")
        check_stop("images")
        if not set(steps) & {"narration", "music", "visuals", "video"}:
            complete()
            return

        if "narration" in steps:
            stage("narration", "running")
            from video.narrator import generate_narration
            audio_path = generate_narration(script, str(output), stop_event=stop_event)
            _invalidate(output, visuals=True)
            (output / "narration_mixed.mp3").unlink(missing_ok=True)
            (output / "narration_mixed.wav").unlink(missing_ok=True)
            (output / "narration_effects.wav").unlink(missing_ok=True)
            (output / "audio_credits.json").unlink(missing_ok=True)
            (output / "render_audio.json").unlink(missing_ok=True)
            state["render_audio_path"] = None
            state["audio_path"] = audio_path
            stage("narration", "done")
        else:
            state["audio_path"] = _audio_path(output)
        check_stop("narration")
        if not state.get("audio_path"):
            raise FileNotFoundError("Narration is missing. Run the Narration step first.")

        from video.timeline import build_timeline, import_legacy_timeline
        stage("narration", "running")
        if (output / "narration.timings.json").is_file():
            state["timeline"] = build_timeline(script, state["audio_path"], str(output), video_type)
        else:
            state["timeline"] = import_legacy_timeline(script, state["audio_path"], str(output), video_type)
            log("Reusing existing narration; timing source: " + state["timeline"]["timing_source"])
            for warning in state["timeline"].get("warnings", []):
                log(warning, "warn")
        stage("narration", "done")

        final_audio = state["audio_path"]
        if "music" in steps:
            stage("music", "running")
            final_audio = _mix_project_audio(
                state["audio_path"], state["timeline"], output,
                profile_id or state.get("profile_id") or "default")
        elif not set(steps) & {"music", "narration"}:
            soundtrack = _load_json(output / "render_audio.json") or {}
            timeline = state.get("timeline") or {}
            candidate = (output / str(soundtrack.get("path", ""))).resolve()
            if (soundtrack.get("audio_sha256") == timeline.get("audio_sha256")
                    and candidate.is_file() and output.resolve() in candidate.parents):
                final_audio = str(candidate)
        state["render_audio_path"] = final_audio
        if "music" in steps:
            _invalidate(output)
            stage("music", "done")
        if state.get("timeline"):
            _save_json(output / "render_audio.json", {
                "path": str(Path(final_audio).resolve().relative_to(output.resolve())),
                "audio_sha256": state["timeline"]["audio_sha256"],
            })
        check_stop("music")

        plan = None
        if mode in {"motion", "hybrid"} and (set(steps) & {"visuals", "video"}
                or ("music" in steps and state.get("visual_plan"))):
            stage("visuals", "running")
            from agents.visual_director import plan_scenes, plan_uses_project_photos, validate_plan
            from video.renderers.html import prepare_motion_project
            saved = state.get("visual_plan") or _load_json(output / "scene_plan.json")
            if "visuals" in steps:
                _invalidate(output)
                plan = plan_scenes(script, state["timeline"], mode=mode, output_dir=str(output),
                                   use_ai=getattr(config, "VISUAL_AI_ENABLED", True),
                                   image_map=state["image_map"] if photos else None,
                                   use_project_photos=photos)
                plan["script_signature"] = state["timeline"]["script_signature"]
                plan["timeline_signature"] = state["timeline"]["timeline_signature"]
                plan["audio_duration"] = state["timeline"]["audio_duration"]
                if photos and state.get("image_reuse_note"):
                    plan.setdefault("warnings", []).append(state["image_reuse_note"])
                _save_json(output / "scene_plan.json", plan)
            elif _matching_plan(saved, state["timeline"], mode, photos):
                plan = validate_plan(saved)
                log("Reusing the edited storyboard and recorded narration.")
            elif isinstance(saved, dict) and plan_uses_project_photos(saved) != photos:
                raise ValueError("The saved storyboard uses a different photo option. Enable the Visuals step to apply the selected style, or open Review to render the saved storyboard.")
            else:
                raise ValueError("No matching storyboard. Run the Visuals step after choosing the mode and generating narration.")
            state["visual_plan"] = plan
            for warning in plan.get("warnings", []):
                log(warning, "warn")
            prepare_motion_project(plan, final_audio, str(output))
            stage("visuals", "done")
        elif mode == "classic":
            state.update(visual_plan=None, visual_qa=None, preview_path=None)
            stage("visuals", "skipped")
        check_stop("visual planning")

        if "video" in steps:
            stage("video", "running")
            state.update(video_path=None, library_id=None, yt_url=None, visual_qa=None)
            if mode in {"motion", "hybrid"}:
                from video.renderers.html import render_motion_video
                video_path = render_motion_video(
                    script, final_audio, str(output), plan, video_type=video_type,
                    preview=preview_only, stop_event=stop_event, progress=log)
                if preview_only:
                    state["preview_path"] = video_path
                else:
                    state["video_path"] = video_path
                checkpoint_workspace(output, rendered_kind="draft" if preview_only else "final")
                state["visual_qa"] = _review_video(video_path, plan, output)
                _save_json(output / "visual_qa.json", state["visual_qa"])
                if state["visual_qa"].get("passed") is False:
                    log("Visual review flagged issues. Inspect the storyboard before publishing.", "warn")
            else:
                from video.creator import create_video
                image_map = state.get("image_map", {})
                state["video_path"] = create_video(script, final_audio, str(output), image_map,
                                                    video_type=video_type, timeline=state.get("timeline"))
                checkpoint_workspace(output, rendered_kind="final")
            check_stop("video rendering")
            stage("video", "done")
            try:
                thumb = state.get("thumb_path")
                if not preserve_packaging or not thumb or not Path(thumb).is_file():
                    image_map = state.get("image_map", {})
                    state["thumb_path"] = make_thumbnail(script, image_map, str(output))
            except Exception as exc:
                log(f"Thumbnail unavailable: {exc}", "warn")

        # Changing motion or the soundtrack preserves publishing edits, including
        # a deliberately empty description and a custom thumbnail.
        if not preserve_packaging or not isinstance(state.get("description"), str):
            state["description"] = build_description(script, state.get("research") or {},
                                                      video_type=video_type, timeline=state.get("timeline"))
        if "video" in steps and (not preview_only or schedule_id):
            try:
                if preview_only:
                    archive_project(allow_draft=True)
                else:
                    archive_project()
            except Exception as exc:
                log(f"Archive failed; working files are still available: {exc}", "warn")
        complete()
    except InterruptedError as exc:
        log(str(exc), "warn")
        for name, status in list(state["stages"].items()):
            if status == "running":
                stage(name, "pending")
        log_queue.put(("stopped", ""))
    except Exception as exc:
        import traceback
        traceback.print_exc(file=orig_stdout)
        state["error"] = str(exc)
        for name, status in list(state["stages"].items()):
            if status == "running":
                stage(name, "error")
        log(f"ERROR: {exc}", "error")
        log_queue.put(("error", str(exc)))
    finally:
        checkpoint_workspace(output)
        with job_lock:
            if release_job:
                state["running"] = False
                state.update(job_profile_id=None, job_profile_name=None, job_profile_values=None, job_output_dir=None)
        sys.stdout = orig_stdout
