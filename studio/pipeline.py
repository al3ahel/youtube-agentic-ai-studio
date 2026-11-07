"""Pipeline blueprint: the page itself, starting/stopping runs, live progress, status."""
import json
import os
import queue
import threading

from flask import Blueprint, Response, current_app, jsonify, request

import config
from studio.files import send, slug
from studio.runner import run_pipeline_thread
from studio.state import job_lock, log, log_queue, state, stop_event

bp = Blueprint("pipeline", __name__)
ALLOWED_STEPS = {"research", "script", "images", "narration", "music", "visuals", "video"}
DEFAULT_STEPS = ["research", "script", "images", "narration", "music", "visuals", "video"]


def _run_options(body):
    if not isinstance(body, dict):
        raise ValueError("Expected a JSON object.")
    steps = body.get("steps", DEFAULT_STEPS)
    if not isinstance(steps, list) or not steps or len(steps) > len(ALLOWED_STEPS) or any(not isinstance(s, str) or s not in ALLOWED_STEPS for s in steps):
        raise ValueError("Choose one or more valid pipeline steps.")
    mode = body.get("visual_mode") or config.VISUAL_MODE
    if not isinstance(mode, str) or mode not in {"motion", "hybrid", "classic"}:
        raise ValueError("Choose Motion, Hybrid or Classic.")
    return list(dict.fromkeys(steps)), mode

@bp.route("/")
def index():
    with open(os.path.join(current_app.root_path, "templates", "index.html"), encoding="utf-8") as f:
        return f.read()

@bp.route("/api/stream")
def stream():
    def gen():
        while True:
            try:
                kind, msg = log_queue.get(timeout=30)
                data = json.dumps({"kind": kind, "msg": msg})
                yield f"data: {data}\n\n"
                if kind in ("done", "error", "stopped"):
                    break
            except queue.Empty:
                yield "data: {\"kind\":\"ping\"}\n\n"
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@bp.route("/api/run", methods=["POST"])
def start_run():
    body = request.get_json(force=True) or {}
    try:
        from studio.channels import get_profile, is_connecting
        from studio.profile_context import capture_profile_settings
        if not isinstance(body, dict):
            raise ValueError("Expected a JSON object.")
        profile = get_profile(body.get("profile_id"))
        profile_values = capture_profile_settings(profile["id"])
        with config.profile_context(profile_values):
            steps, visual_mode = _run_options(body)
        topic, focus_angle = body.get("topic", ""), body.get("focus_angle", "")
        if not isinstance(topic, str) or not isinstance(focus_angle, str) or len(topic) > 2000 or len(focus_angle) > 2000:
            raise ValueError("Topic and focus angle must be text of at most 2,000 characters.")
        video_type = body.get("video_type", "normal")
        if not isinstance(video_type, str) or video_type not in {"normal", "shorts"}:
            raise ValueError("Choose a normal video or a Short.")
        preview_only = body.get("preview_only", False)
        use_trends = body.get("use_trends", False)
        if not isinstance(preview_only, bool) or not isinstance(use_trends, bool):
            raise ValueError("Preview and trends options must be true or false.")
        if preview_only and visual_mode == "classic":
            raise ValueError("Draft preview is available in Motion and Hybrid modes.")
    except (TypeError, ValueError, LookupError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    with job_lock:
        if state["running"] or state.get("visual_editing") or state.get("uploading") or is_connecting():
            return jsonify({"ok": False, "error": "A job is already running. Wait for it to finish."}), 409
        try:
            profile = get_profile(profile["id"])
            profile_values = capture_profile_settings(profile["id"])
            with config.profile_context(profile_values):
                steps, visual_mode = _run_options(body)
            if preview_only and visual_mode == "classic":
                raise ValueError("Draft preview is available in Motion and Hybrid modes.")
        except (ValueError, LookupError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        from studio.project_identity import validate_cached_profile
        try:
            validate_cached_profile(profile["id"], steps, config.OUTPUT_DIR, state)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        for name in state["stages"]:
            state["stages"][name] = "pending" if name in steps or name == "preparation" else "skipped"
        state.update(running=True, video_type=video_type, visual_mode=visual_mode,
                     preview_only=preview_only, stop_requested=False, error=None, preflight=None,
                     job_profile_id=profile["id"], job_profile_name=profile["name"], job_profile_values=profile_values)
        if not state.get("profile_id") and (not state.get("script") or profile["id"] == "default"):
            state.update(profile_id=profile["id"], profile_name=profile["name"])
        stop_event.clear()
        thread = threading.Thread(target=run_pipeline_thread,
                                  args=(steps, topic.strip(), video_type, focus_angle.strip(), use_trends),
                                  kwargs={"visual_mode": visual_mode, "preview_only": preview_only,
                                          "profile_id": profile["id"], "profile_values": profile_values}, daemon=True)
        try:
            thread.start()
        except Exception:
            state["running"] = False
            raise
    return jsonify({"ok": True})


@bp.route("/api/preflight", methods=["GET", "POST"])
def preflight_route():
    from studio.preflight import check_environment
    body = request.get_json(silent=True) if request.method == "POST" else {
        "steps": request.args.get("steps", ",".join(DEFAULT_STEPS)).split(","),
        "visual_mode": request.args.get("visual_mode", config.VISUAL_MODE),
    }
    try:
        steps, mode = _run_options(body or {})
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(check_environment(steps, mode))

@bp.route("/api/stop", methods=["POST"])
def stop_pipeline():
    """Request the pipeline to stop after the current stage finishes."""
    stop_event.set()
    state["stop_requested"] = True
    log("⏹ Stop requested — will halt after current step completes…", "warn")
    return jsonify({"ok": True})

@bp.route("/api/status")
def status_route():
    from agents import gemini_client
    from studio.channels import active_profile_id
    return jsonify({
        "running":        state["running"],
        "stop_requested": state.get("stop_requested", False),
        "stages":         state["stages"],
        "has_video":      bool(state.get("video_path") and os.path.exists(state["video_path"])),
        "has_thumb":      bool(state.get("thumb_path") and os.path.exists(state["thumb_path"])),
        "has_script":     bool(state.get("script")),
        "has_audio": bool(state.get("audio_path") and os.path.isfile(state["audio_path"])),
        "photo_count": sum(1 for paths in state.get("image_map", {}).values()
                           for path in (paths if isinstance(paths, list) else [paths]) if path and os.path.isfile(path)),
        "has_images": any(path and os.path.isfile(path) for paths in state.get("image_map", {}).values()
                          for path in (paths if isinstance(paths, list) else [paths])),
        "has_preview":    bool(state.get("preview_path") and os.path.isfile(state["preview_path"])),
        "has_storyboard": bool(state.get("visual_plan")),
        "has_html_preview": bool(state.get("visual_plan") and os.path.isfile(os.path.join(config.OUTPUT_DIR, "motion", "index.html"))),
        "has_captions":   os.path.exists(os.path.join(config.OUTPUT_DIR, "narration.srt")),
        "yt_url":         state.get("yt_url"),
        "error":          state.get("error"),
        "upload_pct":     state.get("upload_pct", 0),
        "uploading":      state.get("uploading", False),
        "upload_warning": state.get("upload_warning"),
        "video_type":     state.get("video_type", "normal"),
        "visual_mode":    state.get("visual_mode") or config.VISUAL_MODE,
        "preview_only":   state.get("preview_only", False),
        "visual_editing": state.get("visual_editing", False),
        "visual_qa":      state.get("visual_qa"),
        "preflight":      state.get("preflight"),
        "library_id":     state.get("library_id"),
        "llm":            gemini_client.last_provider,
        "started_at":     state.get("started_at"),
        "profile_id": state.get("job_profile_id") if state["running"] else state.get("profile_id"),
        "profile_name": state.get("job_profile_name") if state["running"] else state.get("profile_name"),
        "active_profile_id": active_profile_id(),
        "schedule_id": state.get("schedule_id"),
        "timing_source": (state.get("timeline") or {}).get("timing_source"),
        "timing_warnings": (state.get("timeline") or {}).get("warnings", []),
        "image_reuse_note": state.get("image_reuse_note"),
        "channel_name":   config.CHANNEL_NAME,
        "model":          config.GEMINI_MODEL if gemini_client.gemini_configured() else "free fallback",
    })

@bp.route("/video")
def serve_video():
    return send(state.get("video_path"), "video/mp4",
                f"{slug((state.get('script') or {}).get('title'))}.mp4")

@bp.route("/captions")
def serve_captions():
    return send(os.path.join(config.OUTPUT_DIR, "narration.srt"), "text/plain", "captions.srt")
