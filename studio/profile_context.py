"""Bind per-channel preferences without changing another request's settings."""
from copy import deepcopy
from flask import g, jsonify, request

import config
from studio.state import state


def capture_profile_settings(profile_id):
    from studio.channels import DEFAULT_CONFIG_MAP, config_overrides
    values = {attr: deepcopy(vars(config)[attr]) for attr in DEFAULT_CONFIG_MAP.values() if attr in vars(config)}
    values.update(config_overrides(profile_id))
    return values


def init_app(app):
    @app.before_request
    def bind_channel():
        if request.endpoint in {"static", "build_info"} or request.blueprint in {"channels", "schedules"}:
            return None
        from studio.channels import get_profile, config_overrides
        try:
            profile_id = request.args.get("profile_id")
            project_scope = request.blueprint in {"review", "visuals", "upload"} or request.endpoint in {
                "pipeline.status_route", "pipeline.serve_video", "pipeline.serve_captions"}
            if project_scope and request.endpoint != "upload.yt_status":
                owner = (state.get("job_profile_id") if state.get("running") else state.get("profile_id")) or "default"
                if profile_id and profile_id != owner:
                    raise ValueError("This project belongs to another channel profile.")
                profile_id = owner
            elif not profile_id and request.endpoint == "upload.yt_status":
                profile_id = state.get("profile_id")
            profile = get_profile(profile_id)
            g.profile_id = profile["id"]
            values = config_overrides(profile["id"])
            if project_scope and state.get("running") and state.get("job_profile_id") == profile["id"]:
                values = deepcopy(state.get("job_profile_values") or values)
                if state.get("job_output_dir"):
                    values.update(OUTPUT_DIR=state["job_output_dir"], IMAGES_DIR=state["job_output_dir"] + "/images")
            context = config.profile_context(values)
            context.__enter__()
            g.channel_context = context
        except (ValueError, LookupError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.teardown_request
    def release_channel(error):
        context = g.pop("channel_context", None)
        if context is not None:
            context.__exit__(None, None, None)
