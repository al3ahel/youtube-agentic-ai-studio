"""An explicit, short voice audition using the profile editor's unsaved values."""
import asyncio
from pathlib import Path
import re
import uuid

from flask import Blueprint, abort, jsonify, request, send_file

import config
from studio.state import job_lock, state

bp = Blueprint("audio", __name__)
SAMPLE_TEXT = "This is a short preview of your channel's voice. Take a moment to listen, and choose the pace that feels right for your next video."


async def _record(path, settings):
    import edge_tts
    voice = edge_tts.Communicate(SAMPLE_TEXT, settings["voice_id"], rate=settings["voice_rate"],
                                pitch=settings["voice_pitch"], connect_timeout=10, receive_timeout=20)
    await asyncio.wait_for(voice.save(str(path)), timeout=35)


@bp.route("/api/voice-preview", methods=["POST"])
def preview_voice():
    from studio.channels import is_connecting, validate_defaults
    body = request.get_json()
    allowed = {"voice_id", "voice_rate", "voice_pitch"}
    if not isinstance(body, dict) or set(body) - allowed:
        return jsonify({"ok": False, "error": "Choose a voice, speed and pitch for the preview."}), 400
    settings = {"voice_id": config.VOICE_ID, "voice_rate": config.VOICE_RATE, "voice_pitch": config.VOICE_PITCH, **body}
    try:
        validate_defaults(settings)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    with job_lock:
        if any(state.get(key) for key in ("running", "uploading", "visual_editing")) or is_connecting():
            return jsonify({"ok": False, "error": "Wait for the current job to finish before previewing a voice."}), 409
        state["visual_editing"] = True
    folder = Path(config.DATA_DIR) / "voice-previews"
    path = folder / (uuid.uuid4().hex + ".mp3")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        asyncio.run(_record(path, settings))
        if not path.is_file() or path.stat().st_size < 1000:
            raise RuntimeError("The voice service returned no usable audio.")
        # These are disposable app-generated auditions, not user recordings.
        for old in sorted(folder.glob("*.mp3"), key=lambda item: item.stat().st_mtime, reverse=True)[20:]:
            old.unlink(missing_ok=True)
        return jsonify({"ok": True, "url": "/api/voice-preview/" + path.stem})
    except Exception as exc:
        path.unlink(missing_ok=True)
        message = "The voice service could not create a preview. Check your connection or try another voice."
        if "certificate" in str(exc).lower():
            message = "The secure connection to the voice service could not be verified. Check your system certificates or network proxy."
        return jsonify({"ok": False, "error": message}), 502
    finally:
        with job_lock:
            state["visual_editing"] = False


@bp.route("/api/voice-preview/<identifier>")
def voice_file(identifier):
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        abort(404)
    path = Path(config.DATA_DIR) / "voice-previews" / (identifier + ".mp3")
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="audio/mpeg", conditional=True, max_age=0)
