"""Local channel profiles, isolated OAuth tokens and fetched YouTube identity.

The registry contains preferences and public channel metadata only. Tokens stay
in separate files. Profile activation never mutates global configuration; callers
apply ``config_overrides`` to a request/job context and pin the profile ID to the
project. Network access happens only on Connect/Refresh or a verified upload.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

from flask import Blueprint, jsonify, request

import config
from studio.state import job_lock, state
from uploader import youtube

bp = Blueprint("channels", __name__)
_store_lock = threading.RLock()
_connections = {}
_PROFILE_ID = re.compile(r"(?:default|[a-f0-9]{32})\Z")
_MAX_PROFILES = 50

# Lowercase API settings map onto the existing renderer's configuration keys.
# Provider API keys deliberately remain global and never enter the registry.
DEFAULT_CONFIG_MAP = {
    "channel_name": "CHANNEL_NAME", "channel_desc": "CHANNEL_DESCRIPTION",
    "show_brand": "SHOW_CHANNEL_BRAND", "brand_text": "BRAND_TEXT",
    "thumb_style": "THUMB_STYLE", "credit_yaas": "CREDIT_YAAS",
    "voice_id": "VOICE_ID", "voice_rate": "VOICE_RATE", "voice_pitch": "VOICE_PITCH",
    "music_enabled": "MUSIC_ENABLED", "music_volume": "MUSIC_VOLUME",
    "music_track": "MUSIC_TRACK", "sfx_enabled": "SFX_ENABLED", "sfx_track": "SFX_TRACK",
    "sfx_volume": "SFX_VOLUME", "sfx_mode": "SFX_MODE",
    "sound_mode": "SOUND_MODE", "sound_intensity": "SOUND_INTENSITY",
    "audio_mastering": "AUDIO_MASTERING", "caption_style": "CAPTION_STYLE",
    "script_review_enabled": "SCRIPT_REVIEW_ENABLED",
    "voice_cache_enabled": "VOICE_CACHE_ENABLED", "llm_route": "LLM_ROUTE",
    "ollama_base_url": "OLLAMA_BASE_URL", "ollama_model": "OLLAMA_MODEL",
    "music_library": "MUSIC_LIBRARY_DIR", "visual_mode": "VISUAL_MODE",
    "visual_ai_enabled": "VISUAL_AI_ENABLED", "visual_planner_model": "VISUAL_PLANNER_MODEL",
    "visual_review_model": "VISUAL_REVIEW_MODEL", "visual_review_enabled": "VISUAL_REVIEW_ENABLED",
    "motion_quality": "MOTION_QUALITY", "motion_workers": "MOTION_WORKERS",
    "privacy": "VIDEO_PRIVACY", "fact_grounding": "FACT_GROUNDING",
    "overlay": "OVERLAY_OPACITY", "kb_start": "KB_ZOOM_START", "kb_end": "KB_ZOOM_END",
    "broll_interval": "BROLL_INTERVAL", "broll_xfade": "BROLL_XFADE_DUR",
    "render_preset": "RENDER_PRESET", "image_sources": "IMAGE_SOURCES",
    "trends_geo": "TRENDS_GEO",
    "duration_mins": "TARGET_DURATION_MINUTES",
}
PROFILE_DEFAULT_KEYS = frozenset(DEFAULT_CONFIG_MAP) | {"video_type", "duration_mins"}


class ChannelProfileError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat()


def _data_dir():
    # Resolve at call time so deployments and tests can select their data root.
    return Path(getattr(config, "DATA_DIR", Path(config.ROOT_DIR) / ".yaas")).expanduser().resolve()


def _registry_path():
    return _data_dir() / "channels.json"


def _valid_id(profile_id):
    if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
        raise ChannelProfileError("Unknown channel profile.", 404)
    return profile_id


def _base_defaults():
    # vars(config) reads the module's base values, not a request's temporary
    # profile context. This also preserves legacy Settings changes for default.
    values = vars(config)
    result = {key: deepcopy(values.get(attr, "")) for key, attr in DEFAULT_CONFIG_MAP.items()}
    result.update(video_type="normal", duration_mins=values.get("TARGET_DURATION_MINUTES", 8.0))
    return result


def _fresh_audio_defaults(preset):
    return {"music_track": "", "sfx_track": "", "sfx_enabled": False, "sfx_volume": .08,
            "sfx_mode": "ambience" if preset == "asmr" else "transitions",
            "sound_mode": "auto", "sound_intensity": "low", "audio_mastering": True,
            "caption_style": "plain" if preset == "asmr" else "highlight"}


def _preset_defaults(preset, name):
    result = _base_defaults()
    result["channel_name"] = name
    # Imported assets belong to one profile; another channel's selections are
    # never defaults for a newly created channel.
    result.update(_fresh_audio_defaults(preset))
    if preset == "news":
        result.update(
            channel_desc="A news channel explaining current events with clear sourcing, context and dates. "
                         "Distinguish confirmed facts from uncertainty and avoid sensational claims.",
            voice_id="en-US-AndrewNeural", voice_rate="+0%", voice_pitch="+0Hz",
            music_enabled=False, visual_mode="hybrid", fact_grounding=True,
            video_type="normal", duration_mins=5,
        )
    elif preset == "asmr":
        result.update(
            channel_desc="Calm, low-stimulation sessions with slow visuals, quiet delivery and a restful pace. "
                         "Use suitable licensed recordings selected by the audio director for ambience; do not claim that "
                         "ordinary text-to-speech creates authentic ASMR triggers.",
            voice_id="en-US-BrianNeural", voice_rate="-15%", voice_pitch="+0Hz",
            music_enabled=False, visual_mode="classic", fact_grounding=False,
            kb_start=1.0, kb_end=1.025, broll_interval=20.0, broll_xfade=1.5,
            video_type="normal", duration_mins=10,
        )
    return result


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100:
        raise ChannelProfileError("Enter a channel profile name of 1–100 characters.")
    if any(ord(c) < 32 for c in value):
        raise ChannelProfileError("Profile names cannot contain control characters.")
    return value.strip()


def _local_path(value, label):
    if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 for c in value) or "://" in value:
        raise ChannelProfileError(f"Choose a local path for {label}.")
    return value.strip()


def validate_defaults(values):
    """Return a validated partial defaults update; unknown fields fail closed."""
    if not isinstance(values, dict) or values.keys() - PROFILE_DEFAULT_KEYS:
        raise ChannelProfileError("Unknown or invalid channel default settings.")
    out = {}
    bools = {"show_brand", "credit_yaas", "music_enabled", "sfx_enabled", "visual_ai_enabled", "audio_mastering", "script_review_enabled", "voice_cache_enabled",
             "visual_review_enabled", "fact_grounding"}
    choices = {
        "visual_mode": {"motion", "hybrid", "classic"},
        "video_type": {"normal", "shorts"}, "privacy": {"public", "unlisted", "private"},
        "thumb_style": {"impact", "cinematic", "split", "studio"},
        "motion_quality": {"draft", "standard", "high"},
        "sfx_mode": {"transitions", "ambience"},
        "sound_mode": {"auto", "manual", "off"}, "sound_intensity": {"low", "balanced"},
        "caption_style": {"highlight", "plain", "off"},
        "llm_route": {"auto", "local"},
        "render_preset": {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"},
    }
    numbers = {"music_volume": (0, 1), "sfx_volume": (0, 1), "overlay": (0, .95), "kb_start": (1, 2), "kb_end": (1, 2),
               "broll_interval": (1, 120), "broll_xfade": (0, 10), "duration_mins": (.25, 60)}
    for key, value in values.items():
        if key in bools:
            if type(value) is not bool:
                raise ChannelProfileError(f"Choose true or false for {key}.")
        elif key in choices:
            if not isinstance(value, str) or value not in choices[key]:
                raise ChannelProfileError(f"Choose a valid {key}.")
        elif key in numbers:
            low, high = numbers[key]
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not math.isfinite(value) or not low <= value <= high):
                raise ChannelProfileError(f"{key} must be between {low:g} and {high:g}.")
        elif key == "motion_workers":
            if type(value) is not int or not 1 <= value <= 4:
                raise ChannelProfileError("Motion workers must be an integer from 1 to 4.")
        elif key in {"voice_rate", "voice_pitch"}:
            suffix = "%" if key == "voice_rate" else "Hz"
            limit = 50 if key == "voice_rate" else 30
            if (not isinstance(value, str) or not re.fullmatch(r"[+-]\d+" + re.escape(suffix), value)
                    or abs(int(value[:-len(suffix)])) > limit):
                raise ChannelProfileError(f"Use a signed {suffix} value between -{limit} and +{limit} for {key}.")
        elif key == "voice_id":
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9-]{3,120}", value):
                raise ChannelProfileError("Choose a valid voice identifier.")
        elif key == "music_library":
            value = _local_path(value, "the music library")
        elif key in {"ollama_base_url", "ollama_model"}:
            if not config._valid_setting(DEFAULT_CONFIG_MAP[key], value):
                raise ChannelProfileError("Choose a loopback Ollama address and an installed local model.")
        elif key in {"music_track", "sfx_track"}:
            if not isinstance(value, str) or (value and not re.fullmatch(r"[a-f0-9]{32}", value)):
                raise ChannelProfileError("Choose an imported audio track from this channel's library.")
        elif key == "image_sources":
            if (not isinstance(value, list) or len(value) > 4 or
                    any(not isinstance(v, str) or v not in {"pixabay", "nasa", "openverse", "pollinations"}
                        for v in value)):
                raise ChannelProfileError("Choose supported image sources.")
            value = list(dict.fromkeys(value))
        else:
            maximum = 6000 if key == "channel_desc" else 200
            if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
                raise ChannelProfileError(f"Invalid value for {key}.")
            value = value.strip()
            if key in {"visual_planner_model", "visual_review_model"} and (not value or any(c.isspace() for c in value)):
                raise ChannelProfileError("Choose a valid OpenRouter model identifier.")
        out[key] = deepcopy(value)
    return out


def _validate_combination(defaults):
    if defaults.get("sound_mode", "manual") == "manual" and defaults.get("sfx_enabled") and not defaults.get("sfx_track"):
        raise ChannelProfileError("Choose an imported sound effect before enabling sound effects.")
    if defaults.get("broll_xfade", 0) >= defaults.get("broll_interval", 10):
        raise ChannelProfileError("The image crossfade must be shorter than the image interval.")


def _initial_registry():
    now = _now()
    base = vars(config)
    preset = base.get("CHANNEL_PRESET", "general")
    preset = preset if isinstance(preset, str) and preset in {"general", "news", "asmr"} else "general"
    return {"version": 1, "active_profile_id": "default", "profiles": [{
        "id": "default", "name": " ".join(str(base.get("CHANNEL_NAME", "My channel")).split())[:100] or "My channel",
        "preset": preset, "defaults": {}, "channel": None, "client_secret_path": "",
        "created_at": now, "updated_at": now,
    }]}


def _write_registry(data):
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".channels-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_registry():
    path = _registry_path()
    if not path.exists():
        data = _initial_registry()
        _write_registry(data)
        return data
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("registry too large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("version") != 1 or
                not isinstance(data.get("profiles"), list) or not 1 <= len(data["profiles"]) <= _MAX_PROFILES):
            raise ValueError("invalid registry")
        ids = set()
        for profile in data["profiles"]:
            ident = _valid_id(profile["id"])
            if ident in ids or profile.get("preset") not in {"general", "news", "asmr"}:
                raise ValueError("duplicate or invalid profile")
            ids.add(ident)
            _name(profile["name"])
            validate_defaults(profile.get("defaults", {}))
            _local_path(profile.get("client_secret_path", ""), "the OAuth file")
            if profile.get("channel") is not None and (not isinstance(profile["channel"], dict) or
                    not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", str(profile["channel"].get("id", "")))):
                raise ValueError("invalid channel metadata")
        if data.get("active_profile_id") not in ids or "default" not in ids:
            raise ValueError("invalid active profile")
        return data
    except (ValueError, KeyError, TypeError) as exc:
        raise ChannelProfileError(
            "The saved channel registry is unreadable. Restore channels.json from your backup; it has not been overwritten.", 500
        ) from exc


def _find(data, profile_id):
    _valid_id(profile_id)
    for profile in data["profiles"]:
        if profile["id"] == profile_id:
            return profile
    raise ChannelProfileError("Unknown channel profile.", 404)


def _resolved(profile):
    result = deepcopy(profile)
    defaults = result.get("defaults", {})
    if result["id"] == "default":
        # Legacy Settings and the default profile share one canonical source.
        # Only values that settings.json cannot represent live in the registry.
        defaults = {key: value for key, value in defaults.items()
                    if DEFAULT_CONFIG_MAP.get(key) not in config.USER_SETTINGS}
        result["preset"] = vars(config).get("CHANNEL_PRESET", "general")
    base = _base_defaults()
    if result["id"] != "default":
        # Profiles saved before audio imports existed have no explicit keys yet.
        base.update(_fresh_audio_defaults(result["preset"]))
        if "sound_mode" not in defaults and (
                defaults.get("music_enabled") and defaults.get("music_track")
                or defaults.get("sfx_enabled") and defaults.get("sfx_track")):
            base["sound_mode"] = "manual"
    result["defaults"] = {**base, **defaults}
    return result


def get_profile(profile_id=None):
    """Return a copy of the chosen profile with complete lower-case defaults."""
    with _store_lock:
        data = _read_registry()
        return _resolved(_find(data, profile_id if profile_id is not None else data["active_profile_id"]))


def active_profile_id():
    with _store_lock:
        return _read_registry()["active_profile_id"]


def _profile_dir(profile_id):
    _valid_id(profile_id)
    root = _data_dir()
    result = (root / "channels" / profile_id).resolve()
    if not result.is_relative_to(root):
        raise ChannelProfileError("The channel credential folder must stay inside the studio data folder.")
    return result


def token_path(profile_id):
    """Only the default profile can use the legacy root token; never fall back."""
    _valid_id(profile_id)
    if profile_id == "default":
        path = Path(youtube.TOKEN_FILE).expanduser()
        return path if path.is_absolute() else Path(config.ROOT_DIR) / path
    return _profile_dir(profile_id) / "youtube_token.json"


def client_secret_path(profile_id):
    profile = get_profile(profile_id)
    raw = profile.get("client_secret_path") or vars(config).get("YOUTUBE_CLIENT_SECRET", "client_secret.json")
    path = Path(raw).expanduser()
    return path if path.is_absolute() else Path(config.ROOT_DIR) / path


def _connection_key(profile_id):
    return str(_data_dir()), profile_id


def is_connecting(profile_id=None):
    root = str(_data_dir())
    with _store_lock:
        return any(key[0] == root and (profile_id is None or key[1] == profile_id)
                   and value.get("status") == "connecting" for key, value in _connections.items())


def public_profile(profile):
    if not isinstance(profile, dict):
        profile = get_profile(profile)
    ident = profile["id"]
    present = youtube.has_token(token_path(ident))
    # Return only the public metadata fields we need, even if a local registry
    # was manually edited or contains an older, more verbose provider response.
    raw_channel = profile.get("channel")
    channel = ({key: deepcopy(raw_channel.get(key)) for key in (
        "id", "title", "thumbnail", "url", "handle", "subscribers", "videos", "views", "fetched_at"
    )} if raw_channel else None)
    connected = present and bool(channel)
    with _store_lock:
        connection = deepcopy(_connections.get(_connection_key(ident), {
            "status": "connected" if connected else "idle", "error": None,
        }))
    return {
        "id": ident, "name": profile["name"], "preset": profile["preset"],
        "defaults": deepcopy(profile["defaults"]), "channel": channel,
        "connected": connected, "token_present": present, "connection": connection,
        "client_secret_path": profile.get("client_secret_path", ""),
        "client_secret_configured": client_secret_path(ident).is_file(),
        "created_at": profile.get("created_at"), "updated_at": profile.get("updated_at"),
    }


def list_profiles():
    with _store_lock:
        data = _read_registry()
        profiles = [_resolved(profile) for profile in data["profiles"]]
        active = data["active_profile_id"]
    return {"profiles": [public_profile(profile) for profile in profiles], "active_profile_id": active}


def config_overrides(profile_id=None):
    """Configuration values for a context; does not change config itself."""
    with _store_lock:
        data = _read_registry()
        profile = _find(data, profile_id if profile_id is not None else data["active_profile_id"])
        defaults = profile.get("defaults", {})
        if profile["id"] == "default":
            defaults = {key: value for key, value in defaults.items()
                        if DEFAULT_CONFIG_MAP.get(key) not in config.USER_SETTINGS}
        else:
            # Requests and workers must use the same migrations as the public
            # profile. In particular, an old explicit soundtrack stays Manual
            # before the user has opened or saved Settings after an upgrade.
            defaults = _resolved(profile)["defaults"]
        overrides = {attr: deepcopy(defaults[key]) for key, attr in DEFAULT_CONFIG_MAP.items() if key in defaults}
        overrides["CHANNEL_PRESET"] = (_resolved(profile)["preset"] if profile["id"] == "default" else profile["preset"])
        if profile.get("channel"):
            overrides["YOUTUBE_CHANNEL_ID"] = profile["channel"]["id"]
        elif profile["id"] != "default":
            overrides["YOUTUBE_CHANNEL_ID"] = ""
        return overrides


def run_defaults(profile_id=None):
    defaults = get_profile(profile_id)["defaults"]
    return {key: defaults[key] for key in ("video_type", "duration_mins", "visual_mode")}


def upload_options(profile_id):
    """Capture an explicit publishing identity; scheduled jobs must use this."""
    profile = get_profile(profile_id)
    channel = profile.get("channel")
    if not channel or not youtube.has_token(token_path(profile["id"])):
        raise ChannelProfileError("Connect and verify YouTube for this profile before publishing.", 409)
    return {"token_file": str(token_path(profile["id"])),
            "client_secret_file": str(client_secret_path(profile["id"])),
            "expected_channel_id": channel["id"], "interactive": False}


def _assert_idle():
    if any(state.get(key) for key in ("running", "uploading", "visual_editing")):
        raise ChannelProfileError("Wait for the current job to finish before changing channel profiles.", 409)
    if is_connecting():
        raise ChannelProfileError("Finish the current YouTube connection before changing profiles.", 409)


def create_profile(name, preset="general", defaults=None, *, client_secret="", activate=True):
    name = _name(name)
    if not isinstance(preset, str) or preset not in {"general", "news", "asmr"}:
        raise ChannelProfileError("Choose General, News or ASMR as the profile preset.")
    supplied = validate_defaults({} if defaults is None else defaults)
    values = validate_defaults({**_preset_defaults(preset, name), **supplied})
    _validate_combination(values)
    secret = _local_path(client_secret, "the OAuth file")
    now = _now()
    profile = {"id": uuid.uuid4().hex, "name": name, "preset": preset, "defaults": values,
               "channel": None, "client_secret_path": secret, "created_at": now, "updated_at": now}
    with job_lock, _store_lock:
        _assert_idle()
        data = _read_registry()
        if len(data["profiles"]) >= _MAX_PROFILES:
            raise ChannelProfileError(f"The local studio supports up to {_MAX_PROFILES} channel profiles.")
        data["profiles"].append(profile)
        if activate:
            data["active_profile_id"] = profile["id"]
        _write_registry(data)
    return _resolved(profile)


def update_profile(profile_id, values):
    if not isinstance(values, dict) or values.keys() - {"name", "preset", "defaults", "client_secret_path"}:
        raise ChannelProfileError("Only the profile name, preset, defaults and OAuth file path can be edited.")
    with job_lock, _store_lock:
        _assert_idle()
        data = _read_registry()
        profile = _find(data, profile_id)
        default_updates = validate_defaults(values.get("defaults", {}))
        combined = validate_defaults({**_resolved(profile)["defaults"], **default_updates})
        _validate_combination(combined)
        if "name" in values:
            profile["name"] = _name(values["name"])
        if "preset" in values:
            if not isinstance(values["preset"], str) or values["preset"] not in {"general", "news", "asmr"}:
                raise ChannelProfileError("Choose a supported channel preset.")
            # Editing this label doesn't silently replace individually saved settings.
            profile["preset"] = values["preset"]
        if "client_secret_path" in values:
            profile["client_secret_path"] = _local_path(values["client_secret_path"], "the OAuth file")
        if profile_id == "default":
            global_updates = {DEFAULT_CONFIG_MAP[key]: value for key, value in default_updates.items()
                              if DEFAULT_CONFIG_MAP.get(key) in config.USER_SETTINGS}
            if "preset" in values:
                global_updates["CHANNEL_PRESET"] = values["preset"]
            # All field and cross-field validation above finishes before any
            # settings file is written. A profile name is only a local label;
            # channel branding changes only through defaults.channel_name.
            profile["defaults"] = {key: deepcopy(value) for key, value in
                                   {**profile.get("defaults", {}), **default_updates}.items()
                                   if DEFAULT_CONFIG_MAP.get(key) not in config.USER_SETTINGS}
            if global_updates:
                config.save_settings(global_updates)
                for attr, value in global_updates.items():
                    setattr(config, attr, deepcopy(value))
            profile["preset"] = vars(config).get("CHANNEL_PRESET", "general")
        elif "defaults" in values:
            profile["defaults"] = {**profile.get("defaults", {}), **default_updates}
        profile["updated_at"] = _now()
        _write_registry(data)
        return _resolved(profile)


def update_profile_defaults(profile_id, lower_dict):
    return update_profile(profile_id, {"defaults": lower_dict})


def activate_profile(profile_id):
    with job_lock, _store_lock:
        _assert_idle()
        data = _read_registry()
        profile = _find(data, profile_id)
        data["active_profile_id"] = profile_id
        _write_registry(data)
        return _resolved(profile)


def delete_profile(profile_id):
    """Remove only unused local profiles; preserve every publishing/job binding."""
    from studio import library, schedules
    from studio.project_identity import cached_profile_owner
    with job_lock, _store_lock:
        _assert_idle()
        data = _read_registry()
        profile = _find(data, profile_id)
        if profile_id == "default":
            raise ChannelProfileError("The default profile preserves existing projects and cannot be removed.", 409)
        if profile.get("channel") or youtube.has_token(token_path(profile_id)):
            raise ChannelProfileError("A profile linked to a YouTube channel must be kept for its publishing identity.", 409)
        if state.get("profile_id") == profile_id and any(state.get(key) for key in
                ("script", "research", "audio_path", "video_path", "preview_path", "image_map")):
            raise ChannelProfileError("This profile owns the current project and cannot be removed.", 409)
        try:
            cached_owner = cached_profile_owner(config.OUTPUT_DIR)
        except ValueError as exc:
            raise ChannelProfileError(
                "The saved workspace identity is unreadable; restore its project before removing channel profiles.", 409
            ) from exc
        if cached_owner == profile_id:
            raise ChannelProfileError("This profile owns a saved workspace project and cannot be removed.", 409)
        if schedules.list_schedules(profile_id):
            raise ChannelProfileError("Remove this profile's schedules before deleting an unused profile.", 409)
        for path in Path(library.LIB_DIR).glob("*/meta.json"):
            if path.is_symlink() or path.parent.is_symlink():
                raise ChannelProfileError("Check linked archive folders before removing channel profiles.", 409)
            try:
                metadata = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(metadata, dict):
                    raise ValueError("invalid archive metadata")
            except (ValueError, OSError) as exc:
                raise ChannelProfileError("An archive has unreadable metadata; its channel ownership cannot be checked.", 409) from exc
            if metadata.get("profile_id") == profile_id:
                raise ChannelProfileError("This profile owns archived videos and cannot be removed.", 409)
        data["profiles"] = [item for item in data["profiles"] if item["id"] != profile_id]
        if data["active_profile_id"] == profile_id:
            data["active_profile_id"] = "default"
        _write_registry(data)
        _connections.pop(_connection_key(profile_id), None)
        return {"ok": True, "active_profile_id": data["active_profile_id"]}


def _safe_error(exc):
    if isinstance(exc, (YouTubeConnectionError, youtube.YouTubeAuthRequired, youtube.YouTubeChannelMismatch)):
        return str(exc)[:400]
    if isinstance(exc, FileNotFoundError):
        return "The YouTube OAuth client JSON file is missing. Choose its local path and try Connect again."
    if isinstance(exc, TimeoutError):
        return "YouTube sign-in timed out. Choose Connect YouTube to try again."
    # OAuth and HTTP exception strings can contain credentials or callback URLs.
    return "Could not verify this YouTube connection. Check the OAuth setup, complete the sign-in and try again."


class YouTubeConnectionError(RuntimeError):
    pass


def _set_connection(profile_id, status, error=None):
    with _store_lock:
        _connections[_connection_key(profile_id)] = {"status": status, "error": error, "updated_at": _now()}


def _save_channel(profile_id, channel):
    with _store_lock:
        data = _read_registry()
        profile = _find(data, profile_id)
        previous = profile.get("channel")
        if previous and previous["id"] != channel["id"]:
            raise youtube.YouTubeChannelMismatch("This profile is bound to another YouTube channel. Create a new profile for a different channel.")
        profile["channel"] = deepcopy(channel)
        profile["updated_at"] = _now()
        _write_registry(data)


def _connect_worker(profile_id, secret, expected_channel_id):
    temporary = None
    try:
        temporary = _profile_dir(profile_id) / f".connect-{uuid.uuid4().hex}.json"
        creds = youtube.get_credentials(token_file=str(temporary), client_secret_file=secret,
                                        interactive=True, force=True, scopes=youtube.CHANNEL_SCOPES,
                                        log=lambda message: None)
        channel = youtube.fetch_authenticated_channel(credentials=creds, expected_channel_id=expected_channel_id)
        with _store_lock:
            # Verify the binding before replacing any valid existing token.
            current = get_profile(profile_id)
            if current.get("channel") and current["channel"]["id"] != channel["id"]:
                raise youtube.YouTubeChannelMismatch("Reconnect the channel already bound to this profile.")
            target = token_path(profile_id)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.replace(temporary, target)
            _save_channel(profile_id, channel)
        _set_connection(profile_id, "connected")
    except Exception as exc:
        _set_connection(profile_id, "error", _safe_error(exc))
    finally:
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        finally:
            with job_lock:
                if state.get("channel_connecting") == profile_id:
                    state["channel_connecting"] = None


def connect_profile(profile_id):
    with job_lock:
        _assert_idle()
        profile = get_profile(profile_id)
        secret = str(client_secret_path(profile_id))
        if not Path(secret).is_file():
            raise ChannelProfileError("Choose an existing YouTube OAuth client JSON file before connecting.")
        expected = (profile.get("channel") or {}).get("id")
        _set_connection(profile_id, "connecting")
        state["channel_connecting"] = profile_id
        try:
            threading.Thread(target=_connect_worker, args=(profile_id, secret, expected), daemon=True).start()
        except Exception:
            state["channel_connecting"] = None
            _set_connection(profile_id, "error", "Could not start the YouTube connection. Try again.")
            raise
    return public_profile(get_profile(profile_id))


def refresh_profile(profile_id):
    with job_lock:
        _assert_idle()
        profile = get_profile(profile_id)
        try:
            channel = youtube.fetch_authenticated_channel(
                token_file=str(token_path(profile_id)), client_secret_file=str(client_secret_path(profile_id)),
                expected_channel_id=(profile.get("channel") or {}).get("id"), log=lambda message: None,
            )
            _save_channel(profile_id, channel)
            _set_connection(profile_id, "connected")
        except Exception as exc:
            message = _safe_error(exc)
            _set_connection(profile_id, "error", message)
            raise ChannelProfileError(message, 409) from exc
    return public_profile(get_profile(profile_id))


def disconnect_profile(profile_id):
    with job_lock, _store_lock:
        _assert_idle()
        get_profile(profile_id)
        token_path(profile_id).unlink(missing_ok=True)
        # Keep the original channel binding so old schedules/projects cannot be
        # redirected to a different channel under the same profile ID.
        _set_connection(profile_id, "idle")
    return public_profile(get_profile(profile_id))


@bp.errorhandler(ChannelProfileError)
def profile_error(exc):
    return jsonify({"ok": False, "error": str(exc)}), exc.status


@bp.errorhandler(OSError)
def storage_error(exc):
    return jsonify({"ok": False, "error": "Could not read or save the local channel profile files."}), 500


def _body():
    body = request.get_json(force=True)
    if not isinstance(body, dict):
        raise ChannelProfileError("Expected a channel profile object.")
    return body


@bp.get("/api/channels")
def channels_list_route():
    return jsonify(list_profiles())


@bp.get("/api/channels/<profile_id>")
def channel_detail_route(profile_id):
    return jsonify({"profile": public_profile(get_profile(profile_id)), "active_profile_id": active_profile_id()})


@bp.post("/api/channels")
def channel_create_route():
    body = _body()
    if body.keys() - {"name", "preset", "defaults", "client_secret_path"}:
        raise ChannelProfileError("Unknown channel profile fields.")
    profile = create_profile(body.get("name"), body.get("preset", "general"), body.get("defaults"),
                             client_secret=body.get("client_secret_path", ""))
    return jsonify({"ok": True, "profile": public_profile(profile), "active_profile_id": active_profile_id()}), 201


@bp.patch("/api/channels/<profile_id>")
def channel_update_route(profile_id):
    return jsonify({"ok": True, "profile": public_profile(update_profile(profile_id, _body()))})


@bp.post("/api/channels/<profile_id>/activate")
def channel_activate_route(profile_id):
    profile = activate_profile(profile_id)
    return jsonify({"ok": True, "profile": public_profile(profile), "active_profile_id": profile_id})


@bp.delete("/api/channels/<profile_id>")
def channel_delete_route(profile_id):
    return jsonify(delete_profile(profile_id))


@bp.post("/api/channels/<profile_id>/connect")
def channel_connect_route(profile_id):
    return jsonify({"ok": True, "profile": connect_profile(profile_id)}), 202


@bp.post("/api/channels/<profile_id>/refresh")
def channel_refresh_route(profile_id):
    return jsonify({"ok": True, "profile": refresh_profile(profile_id)})


@bp.post("/api/channels/<profile_id>/disconnect")
def channel_disconnect_route(profile_id):
    return jsonify({"ok": True, "profile": disconnect_profile(profile_id)})
