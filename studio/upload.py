"""Upload blueprint: YouTube sign-in status and publishing (see uploader/youtube.py)."""
import os
from copy import deepcopy
import threading

from flask import Blueprint, jsonify, request

import config
from studio.library import sync_library
from studio.state import job_lock, log, state
from uploader import youtube as yt_uploader

bp = Blueprint("upload", __name__)

def secret_ok():
    try:
        with open(config.YOUTUBE_CLIENT_SECRET, encoding="utf-8") as f:
            return "YOUR_CLIENT_ID" not in f.read()
    except Exception:
        return False

@bp.route("/api/yt-status")
def yt_status():
    return jsonify({"token": yt_uploader.has_token(), "secret": secret_ok()})

@bp.route("/api/upload", methods=["POST"])
def upload_route():
    body    = request.get_json(force=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "Expected upload settings as an object."}), 400
    privacy = body.get("privacy", config.VIDEO_PRIVACY)
    publish_at = body.get("publish_at", "")
    if not isinstance(privacy, str) or privacy not in {"public", "unlisted", "private"} or not isinstance(publish_at, str) or len(publish_at) > 100:
        return jsonify({"ok": False, "error": "Choose a valid visibility and publication time."}), 400
    publish_at = publish_at.strip()
    with job_lock:
        if state.get("uploading") or state.get("running") or state.get("visual_editing"):
            return jsonify({"ok": False, "error": "Wait for the current job to finish before uploading."}), 409
        if state.get("preview_only") or (state.get("visual_plan") and state.get("stages", {}).get("video") != "done"):
            return jsonify({"ok": False, "error": "Render a final video from the current storyboard before uploading."}), 409
        video_path = state.get("video_path")
        if not video_path or not os.path.isfile(video_path):
            return jsonify({"ok": False, "error": "No final video to upload"})
        if os.path.basename(video_path) == "motion_preview.mp4" or (state.get("preview_path") and os.path.abspath(video_path) == os.path.abspath(state["preview_path"])):
            return jsonify({"ok": False, "error": "A draft preview cannot be uploaded as a final video."}), 409
        if not secret_ok():
            return jsonify({"ok": False, "error": "client_secret.json is missing — download it from Google Cloud and save it in the project folder (SETUP.md step 4)"})
        script = deepcopy(state.get("script") or {})
        description = state.get("description", script.get("description", ""))
        thumb_path = state.get("thumb_path")
        state.update(uploading=True, upload_pct=0, yt_url=None, upload_warning=None, error=None)

    def _do_upload():
        try:
            result = yt_uploader.upload_video(
                video_path,
                title=script.get("title", ""),
                description=description,
                tags=script.get("tags", []),
                privacy=privacy,
                publish_at=publish_at,
                thumb_path=thumb_path,
                on_progress=lambda pct: state.__setitem__("upload_pct", pct),
                log=lambda msg: log(msg, "warn"),
            )
            state["yt_url"]         = result["url"]
            state["upload_warning"] = result["thumbnail_error"]
            state["upload_pct"]     = 100
            sync_library()
            log(f"Uploaded! {state['yt_url']}")

        except Exception as e:
            log(f"Upload error: {e}", "error")
            state["error"] = str(e)
        finally:
            with job_lock:
                state["uploading"] = False

    try:
        threading.Thread(target=_do_upload, daemon=True).start()
    except Exception:
        with job_lock:
            state["uploading"] = False
        raise
    return jsonify({"ok": True})
