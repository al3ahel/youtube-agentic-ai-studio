"""An explicit, short voice audition using the profile editor's unsaved values."""
import asyncio
import json
from pathlib import Path
import re
import uuid

from flask import Blueprint, abort, g, jsonify, request, send_file

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


@bp.get("/api/audio-library")
def audio_library_list():
    from studio.audio_library import AudioLibraryError, list_audio_assets
    try:
        response = jsonify({"ok": True, "assets": list_audio_assets(g.profile_id)})
        response.headers["Cache-Control"] = "no-store"
        return response
    except AudioLibraryError as exc:
        return jsonify({"ok": False, "error": str(exc)}), exc.status


@bp.post("/api/audio-library/import")
def audio_library_import():
    from werkzeug.exceptions import BadRequest
    from studio import audio_library
    try:
        if request.content_length is not None and request.content_length > audio_library.MAX_REQUEST_BYTES:
            raise audio_library.AudioLibraryError("Audio files must be 20 MiB or smaller.", 413)
        raw = request.stream.read(audio_library.MAX_REQUEST_BYTES + 1)
        if len(raw) > audio_library.MAX_REQUEST_BYTES:
            raise audio_library.AudioLibraryError("Audio files must be 20 MiB or smaller.", 413)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError, TypeError) as exc:
            raise audio_library.AudioLibraryError("Send the audio file and details as a JSON object.") from exc
        asset = audio_library.import_audio_asset(g.profile_id, body)
        return jsonify({"ok": True, "asset": asset}), 201
    except audio_library.AudioLibraryError as exc:
        return jsonify({"ok": False, "error": str(exc)}), exc.status
    except BadRequest:
        return jsonify({"ok": False, "error": "The audio upload was incomplete. Choose the file again."}), 400


@bp.get("/api/audio-library/<identifier>/preview")
def audio_library_preview(identifier):
    from studio.audio_library import AudioLibraryError, resolve_audio_asset
    try:
        asset = resolve_audio_asset(g.profile_id, identifier)
        mime = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
                ".m4a": "audio/mp4", ".flac": "audio/flac"}[Path(asset["filename"]).suffix.lower()]
        response = send_file(asset["path"], mimetype=mime, conditional=True,
                             as_attachment=False, download_name=asset["filename"])
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except AudioLibraryError as exc:
        return jsonify({"ok": False, "error": str(exc)}), exc.status
    except OSError:
        return jsonify({"ok": False, "error": "That audio file is no longer available in this channel's library."}), 404
