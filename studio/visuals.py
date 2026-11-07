"""Storyboard review and an isolated, local-only motion preview."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile

from flask import Blueprint, abort, jsonify, request, send_file

import config
from studio.revisions import file_revision, plan_revision, validate_expected_revision
from studio.state import job_lock, state
from studio.workspace import checkpoint_workspace, has_valid_storyboard

bp = Blueprint("visuals", __name__)
_ASSET_TYPES = {".html", ".js", ".mjs", ".json", ".css", ".svg", ".png", ".jpg",
                ".jpeg", ".webp", ".woff", ".woff2", ".ttf", ".otf", ".mp3",
                ".wav", ".m4a", ".ogg", ".mp4"}


def _output_root():
    return Path(config.OUTPUT_DIR).resolve()


def _motion_file(filename):
    """Resolve beneath output/motion, rejecting traversal and symlink escapes."""
    if not filename or "\\" in filename or ":" in filename or "\x00" in filename:
        abort(404)
    relative = Path(filename)
    if relative.is_absolute() or any(part in {"..", "."} or part.startswith(".") for part in relative.parts):
        abort(404)
    output = _output_root()
    root = output / "motion"
    resolved_root = root.resolve()
    if resolved_root != root or not resolved_root.is_relative_to(output):
        abort(404)
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or candidate.suffix.lower() not in _ASSET_TYPES or not candidate.is_file():
        abort(404)
    return candidate


@bp.get("/api/visuals")
def storyboard():
    plan = state.get("visual_plan")
    if not has_valid_storyboard(plan):
        plan = None
    return jsonify({
        "ok": True, "plan": plan, "timeline": state.get("timeline"),
        "qa": state.get("visual_qa"), "editing": state.get("visual_editing", False),
        "visual_mode": state.get("visual_mode") or config.VISUAL_MODE,
        "revision": state.get("visual_revision", 0),
        "plan_revision": plan_revision(plan),
        "preview_revision": file_revision(_output_root() / "motion" / "index.html") if plan else None,
        "preview_url": "/api/visuals/preview/index.html?preview=1" if plan and (_output_root() / "motion" / "index.html").is_file() else None,
    })


@bp.get("/api/visuals/preview/<path:filename>")
def preview_asset(filename):
    response = send_file(_motion_file(filename), conditional=True)
    # A sandboxed iframe has an opaque origin. Only generated preview assets
    # receive this CORS header; the studio's API never does.
    response.headers["Access-Control-Allow-Origin"] = "null"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self'; media-src 'self' blob:; connect-src 'none'; "
        "frame-ancestors 'self'; base-uri 'none'; form-action 'none'; sandbox allow-scripts"
    )
    return response


@bp.get("/preview-video")
def preview_video():
    path = state.get("preview_path")
    output = _output_root()
    candidate = Path(path).resolve() if path else None
    if not candidate or not candidate.is_relative_to(output) or candidate.suffix.lower() != ".mp4" or not candidate.is_file():
        abort(404)
    response = send_file(candidate, mimetype="video/mp4", conditional=True,
                         download_name="motion-draft-preview.mp4", as_attachment=request.args.get("download") == "1")
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/api/visuals/models")
def models():
    purpose = request.args.get("purpose", "planner")
    query = request.args.get("q", "").strip()
    try:
        limit = int(request.args.get("limit", "30"))
    except ValueError:
        return jsonify({"ok": False, "error": "Model limit must be an integer."}), 400
    if purpose not in {"planner", "reviewer"} or not 1 <= limit <= 40 or len(query) > 80:
        return jsonify({"ok": False, "error": "Choose planner or reviewer, a limit of 1–40 and a search of at most 80 characters."}), 400
    try:
        from apis.openrouter import discover_models
        records = discover_models()
        matches = [model for model in records
                   if model.get("free") is True and model.get("supports_schema")
                   and (purpose != "reviewer" or model.get("supports_vision"))
                   and (not query or query.lower() in (model.get("id", "") + " " + model.get("name", "")).lower())]
        # The response is deliberately bounded and excludes arbitrary provider
        # metadata. This endpoint retrieves a catalogue; it never runs a model.
        fields = ("id", "name", "supports_schema", "supports_vision", "input_modalities", "context_length", "free")
        return jsonify({"ok": True, "models": [{key: model.get(key) for key in fields} for model in matches[:limit]],
                        "total": len(matches), "limit": limit, "purpose": purpose})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Could not load the free model catalogue: {exc}", "models": []}), 503


def _save_plan(plan, output):
    path = output / "scene_plan.json"
    temporary = output / "scene_plan.json.tmp"
    temporary.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@bp.post("/api/visuals/scenes/<scene_id>/regenerate")
def regenerate(scene_id):
    body = request.get_json(force=True) or {}
    if not isinstance(body, dict) or set(body) - {"instructions", "replace_image", "expected_plan_revision"}:
        return jsonify({"ok": False, "error": "Only scene instructions, the saved-photo option and the storyboard revision may be supplied."}), 400
    instructions = body.get("instructions", "")
    if not isinstance(instructions, str) or len(instructions) > 1000:
        return jsonify({"ok": False, "error": "Scene instructions must be text of at most 1,000 characters."}), 400
    replace_image = body.get("replace_image", False)
    if not isinstance(replace_image, bool):
        return jsonify({"ok": False, "error": "The saved-photo option must be true or false."}), 400
    try:
        expected_revision = validate_expected_revision(body.get("expected_plan_revision"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    with job_lock:
        if state.get("running") or state.get("visual_editing") or state.get("uploading"):
            return jsonify({"ok": False, "error": "Wait for the current job to finish before editing a scene."}), 409
        plan = deepcopy(state.get("visual_plan"))
        if expected_revision is not None and expected_revision != plan_revision(plan):
            return jsonify({"ok": False, "error": "The storyboard changed in another tab. Reload Review before changing this scene."}), 409
        if not plan or not any(str(scene.get("id")) == scene_id for scene in plan.get("scenes", [])):
            return jsonify({"ok": False, "error": "That storyboard scene was not found."}), 404
        script = deepcopy(state.get("script") or {})
        image_map = deepcopy(state.get("image_map") or {})
        output = _output_root()
        binding = output / "image_binding.json"
        if any(scene.get("image") for scene in plan.get("scenes", [])) and (binding.exists() or binding.is_symlink()):
            from studio.visual_assets import validate_binding
            try:
                saved_map = json.loads((output / "image_map.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved_map = {}
            if validate_binding(output, saved_map, script, state.get("profile_id") or "default") is not True:
                return jsonify({"ok": False, "error": "The saved photos no longer match this project. Rebuild as Motion graphics, or regenerate Images and Visuals, before editing this photo storyboard."}), 409
        audio_path = state.get("render_audio_path") or state.get("audio_path")
        audio = Path(audio_path).resolve() if audio_path else None
        if not audio or not audio.is_relative_to(output) or not audio.is_file():
            return jsonify({"ok": False, "error": "Generate narration before rebuilding the preview."}), 409
        state["visual_editing"] = True
    try:
        from agents.visual_director import regenerate_scene
        from video.renderers.html import prepare_motion_project
        options = {"replace_image": True, "image_map": image_map, "output_dir": str(output)} if replace_image else {}
        updated = regenerate_scene(plan, scene_id, script, instructions=instructions.strip(), **options)
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".motion-edit-", dir=output) as directory:
            staging = Path(directory)
            prepared = Path(prepare_motion_project(updated, str(audio), str(staging))).resolve()
            staged_motion = staging / "motion"
            if prepared != staged_motion / "index.html" or not prepared.is_file():
                raise ValueError("Renderer did not produce a local motion preview.")
            destination, backup = output / "motion", staging / "previous"
            if destination.is_symlink():
                raise ValueError("Motion output must be a local directory.")
            had_previous = destination.exists()
            if had_previous:
                os.replace(destination, backup)
            try:
                os.replace(staged_motion, destination)
                _save_plan(updated, output)
            except Exception:
                if destination.exists():
                    shutil.rmtree(destination)
                if had_previous:
                    os.replace(backup, destination)
                raise
        with job_lock:
            state.update(visual_plan=updated, visual_qa=None, video_path=None,
                         preview_path=None, library_id=None, yt_url=None, error=None,
                         visual_revision=state.get("visual_revision", 0) + 1)
            state["stages"]["visuals"] = "done"
            state["stages"]["video"] = "pending"
        checkpoint_workspace(output)
        # QA belongs to the previous frames. A later render will write its own
        # result; reopening the project must not resurrect this stale report.
        try:
            (output / "visual_qa.json").unlink(missing_ok=True)
        except OSError:
            pass
        return jsonify({"ok": True, "plan": updated, "revision": state["visual_revision"],
                        "changes": updated.get("last_edit", {}).get("changes", []),
                        "plan_revision": plan_revision(updated),
                        "preview_revision": file_revision(output / "motion" / "index.html"),
                        "preview_url": "/api/visuals/preview/index.html?preview=1"})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Could not rebuild this scene: {exc}"}), 422
    finally:
        with job_lock:
            state["visual_editing"] = False
