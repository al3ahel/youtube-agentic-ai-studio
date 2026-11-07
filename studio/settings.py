"""Settings blueprint: keys go to .env, everything else to settings.json."""
from flask import Blueprint, g, jsonify, request

import config
from agents.researcher import load_banned_topics, save_banned_topics
from studio.state import job_lock, state

bp = Blueprint("settings", __name__)

_MASK = "••••"

def _mask(v):
    v = v or ""
    if not v or v.upper().startswith("YOUR_"): return ""
    return _MASK + v[-4:] if len(v) > 8 else _MASK

# body key → (config attr, type). "env" types are written to .env; the rest to
# settings.json (config.save_settings). config.py is never written.
_SETTINGS = {
    "gemini_key":       ("GEMINI_API_KEY",     "env"),
    "pexels_key":       ("PEXELS_API_KEY",     "env"),
    "pixabay_key":      ("PIXABAY_API_KEY",    "env"),
    "groq_key":         ("GROQ_API_KEY",       "env"),
    "openrouter_key":   ("OPENROUTER_API_KEY", "env"),
    "youtube_key":      ("YOUTUBE_API_KEY",    "env"),
    "youtube_channel":  ("YOUTUBE_CHANNEL_ID", "env_plain"),
    "music_enabled":    ("MUSIC_ENABLED",      bool),
    "music_volume":     ("MUSIC_VOLUME",       float),
    "music_track":      ("MUSIC_TRACK",        str),
    "sfx_enabled":      ("SFX_ENABLED",        bool),
    "sfx_track":        ("SFX_TRACK",          str),
    "sfx_volume":       ("SFX_VOLUME",         float),
    "sfx_mode":         ("SFX_MODE",           str),
    "sound_mode":       ("SOUND_MODE",         str),
    "sound_intensity":  ("SOUND_INTENSITY",    str),
    "audio_mastering":  ("AUDIO_MASTERING",    bool),
    "caption_style":    ("CAPTION_STYLE",      str),
    "script_review_enabled": ("SCRIPT_REVIEW_ENABLED", bool),
    "voice_cache_enabled": ("VOICE_CACHE_ENABLED", bool),
    "llm_route":        ("LLM_ROUTE",          str),
    "ollama_base_url":  ("OLLAMA_BASE_URL",    str),
    "ollama_model":     ("OLLAMA_MODEL",       str),
    "channel_name":     ("CHANNEL_NAME",       str),
    "show_brand":       ("SHOW_CHANNEL_BRAND", bool),
    "thumb_style":      ("THUMB_STYLE",        str),
    "brand_text":       ("BRAND_TEXT",         str),
    "voice_id":         ("VOICE_ID",           str),
    "voice_rate":       ("VOICE_RATE",         str),
    "voice_pitch":      ("VOICE_PITCH",        str),
    "gemini_model":     ("GEMINI_MODEL",       str),
    "overlay":          ("OVERLAY_OPACITY",    float),
    "kb_start":         ("KB_ZOOM_START",      float),
    "kb_end":           ("KB_ZOOM_END",        float),
    "privacy":          ("VIDEO_PRIVACY",      str),
    "broll_interval":   ("BROLL_INTERVAL",     float),
    "broll_xfade":      ("BROLL_XFADE_DUR",    float),
    "render_preset":    ("RENDER_PRESET",      str),
    "fact_grounding":   ("FACT_GROUNDING",     bool),
    "keyless_llm":      ("KEYLESS_LLM_FALLBACK", bool),
    "credit_yaas":      ("CREDIT_YAAS",        bool),
    "image_sources":    ("IMAGE_SOURCES",      list),
    "trends_geo":       ("TRENDS_GEO",         str),
    "groq_model":       ("GROQ_MODEL",         str),
    "openrouter_model": ("OPENROUTER_MODEL",   str),
    "visual_mode":      ("VISUAL_MODE", str),
    "visual_ai_enabled": ("VISUAL_AI_ENABLED", bool),
    "visual_planner_model": ("VISUAL_PLANNER_MODEL", str),
    "visual_review_model": ("VISUAL_REVIEW_MODEL", str),
    "visual_review_enabled": ("VISUAL_REVIEW_ENABLED", bool),
    "openrouter_free_only": ("OPENROUTER_FREE_ONLY", bool),
    "motion_quality":   ("MOTION_QUALITY", str),
    "motion_workers":   ("MOTION_WORKERS", int),
}

@bp.route("/api/settings")
def get_settings():
    # The client keeps this identity with its form so a second browser tab
    # changing Home's selection cannot redirect the next save.
    out = {"profile_id": g.profile_id}
    for k, (attr, typ) in _SETTINGS.items():
        val = getattr(config, attr, "")
        out[k] = _mask(val) if typ == "env" else val
    out["channel_desc"]  = config.CHANNEL_DESCRIPTION.strip()
    out["music_library"] = getattr(config, "MUSIC_LIBRARY_DIR", "")
    out["banned_topics"] = load_banned_topics()
    from apis.llm import effective_groq_model
    out["groq_model"] = effective_groq_model()
    return jsonify(out)

@bp.route("/api/save-settings", methods=["POST"])
def save_settings_route():
    from studio.channels import is_connecting
    with job_lock:
        if state.get("running") or state.get("visual_editing") or state.get("uploading") or is_connecting():
            return jsonify({"ok": False, "error": "Wait for the current job to finish before changing settings."}), 409
        return _save_settings()


def _save_settings():
    body = request.get_json(force=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "Expected a settings object."}), 400
    env_updates, src_updates = {}, {}

    for k, (attr, typ) in _SETTINGS.items():
        if k not in body or body[k] is None:
            continue
        val = body[k]
        if typ in ("env", "env_plain"):
            val = str(val).strip()
            if val.startswith(_MASK):      # untouched masked key
                continue
            env_updates[attr] = val
        elif typ is bool:
            val = val if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes", "on")
            src_updates[attr] = val
        elif typ is float:
            try: val = float(val)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": f"Invalid value for {k}."}), 400
            src_updates[attr] = val
        elif typ is int:
            try:
                if isinstance(val, bool) or str(val) != str(int(val)):
                    raise ValueError()
                val = int(val)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": f"Invalid value for {k}."}), 400
            src_updates[attr] = val
        elif typ is list:
            val = [str(v) for v in val] if isinstance(val, list) else [v.strip() for v in str(val).split(",") if v.strip()]
            src_updates[attr] = val
        else:
            src_updates[attr] = str(val)

    if body.get("channel_desc") is not None:
        cd = str(body["channel_desc"]).strip()
        src_updates["CHANNEL_DESCRIPTION"] = cd

    invalid = [key for key, value in src_updates.items() if not config._valid_setting(key, value)]
    if invalid:
        return jsonify({"ok": False, "error": "Invalid setting: " + ", ".join(invalid)}), 400

    from studio.channels import DEFAULT_CONFIG_MAP, active_profile_id, get_profile, update_profile_defaults, validate_defaults
    profile_id = getattr(g, "profile_id", None) or active_profile_id()
    profile_updates = {key: src_updates[attr] for key, attr in DEFAULT_CONFIG_MAP.items() if attr in src_updates}
    try:
        validate_defaults(profile_updates)
        previous = get_profile(profile_id)["defaults"]
        combined = {**previous, **profile_updates}
        if combined.get("sound_mode", "manual") == "manual" and combined.get("sfx_enabled") and not combined.get("sfx_track"):
            raise ValueError("Choose an imported sound effect before enabling sound effects.")
        for key, kind, enabled in (("music_track", "music", "music_enabled"), ("sfx_track", "sfx", "sfx_enabled")):
            identifier = combined.get(key)
            changed = key in profile_updates and identifier != previous.get(key)
            used = combined.get("sound_mode", "manual") == "manual" and combined.get(enabled)
            # A full form carries hidden manual selections when switching to
            # Automatic/Off. A missing, unused old file must not trap that
            # switch. New selections and enabled Manual layers remain checked.
            if identifier and (changed or used):
                from studio.audio_library import resolve_audio_asset
                resolve_audio_asset(profile_id, identifier, kind=kind)
        if combined.get("broll_xfade", 0) >= combined.get("broll_interval", 10):
            raise ValueError("The image crossfade must be shorter than the image interval.")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if profile_id != "default":
        # Provider settings are shared; voice/brand/visual settings belong to the
        # channel being edited and must not leak into another profile.
        src_updates = {key: value for key, value in src_updates.items() if key not in DEFAULT_CONFIG_MAP.values()}
        # This legacy field supplies public statistics for the default profile.
        # Other profiles use their verified connection's channel ID, so saving
        # their full Settings form must not overwrite the legacy identity.
        env_updates.pop("YOUTUBE_CHANNEL_ID", None)

    try:
        if env_updates:
            config.save_env(env_updates)
        if src_updates:
            config.save_settings(src_updates)
        if profile_updates and profile_id != "default":
            update_profile_defaults(profile_id, profile_updates)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not save settings: {e}"})

    for attr, value in {**env_updates, **src_updates}.items():
        setattr(config, attr, value)

    # Settings that change API behaviour invalidate the cached results
    from apis import trends as tr
    tr.get_trends.cache_clear()
    from apis import youtube_stats
    youtube_stats.channel_stats.cache_clear()
    return jsonify({"ok": True, "profile_id": profile_id})


@bp.post("/api/local-models")
def local_models():
    """Discover installed local models without pulling or running one."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - {"base_url"}:
        return jsonify({"ok": False, "error": "Choose a local Ollama endpoint."}), 400
    base_url = body.get("base_url", config.OLLAMA_BASE_URL)
    if not config._valid_setting("OLLAMA_BASE_URL", base_url):
        return jsonify({"ok": False, "error": "Use an Ollama endpoint on localhost, for example http://127.0.0.1:11434."}), 400
    from apis.local_llm import LocalLLMError, list_models
    try:
        return jsonify({"ok": True, "models": list_models(base_url=base_url)})
    except LocalLLMError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

@bp.route("/api/banned-topics")
def get_banned_topics():
    return jsonify({"topics": load_banned_topics()})

@bp.route("/api/save-banned-topics", methods=["POST"])
def post_banned_topics():
    if state.get("running") or state.get("visual_editing"):
        return jsonify({"ok": False, "error": "Wait for the current job to finish before editing topics."}), 409
    body   = request.get_json(force=True) or {}
    topics = body.get("topics", [])
    try:
        save_banned_topics(topics)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

def import_settings(path):
    """`python gui.py --import-settings old_config.py`: carry over settings an older
    YAAS saved into config.py. The file is parsed, never run."""
    values = config.import_legacy_config(path)
    if not values:
        print(f"No changed settings found in {path}; nothing to import.")
        return
    config.save_settings(values)
    print(f"Imported {len(values)} setting(s) into {config.SETTINGS_PATH}:")
    for k in sorted(values):
        print(f"  {k}")
