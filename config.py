import ast
import copy
import json
import os
import re

# ─────────────────────────────────────────────────────────────────────────────
#  YAAS — youtube-agentic-ai-studio · Configuration (defaults)
#  This file holds the DEFAULTS and is never written by the app.
#  · API keys live in .env (git-ignored): copy .env.example to .env, or paste
#    them into the GUI's Settings page, which writes .env for you.
#  · Everything you change on the Settings page is saved to settings.json
#    (git-ignored) and wins over the defaults below, so `git pull` never
#    clashes with your settings. To change a default by hand, edit it here;
#    a value saved in settings.json still takes priority.
# ─────────────────────────────────────────────────────────────────────────────

ROOT_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR      = os.path.join(ROOT_DIR, ".yaas")
ENV_PATH      = os.path.join(ROOT_DIR, ".env")
SETTINGS_PATH = os.path.join(ROOT_DIR, "settings.json")


def _load_env(path: str = ENV_PATH) -> None:
    """Minimal .env reader (no extra dependency). Real env vars win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def save_env(values: dict, path: str = ENV_PATH) -> None:
    """Update KEY=value pairs in .env, keeping comments and unrelated keys."""
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    remaining = dict(values)
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and k in remaining:
            lines[i] = f"{k}={remaining.pop(k)}"
    lines += [f"{k}={v}" for k, v in remaining.items()]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


_load_env()

# ─────────────────────────────────────────
#  Optional hosted API keys — use accounts with free-tier access and quota.
#  An installed local Ollama model can handle text agents without a hosted key.
# ─────────────────────────────────────────
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY",     "")   # aistudio.google.com/apikey
PEXELS_API_KEY     = os.getenv("PEXELS_API_KEY",     "")   # pexels.com/api
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")   # optional, unused by default

# Optional free keys — each one unlocks an extra provider
PIXABAY_API_KEY    = os.getenv("PIXABAY_API_KEY",    "")   # pixabay.com/api/docs — stock photos + videos
GROQ_API_KEY       = os.getenv("GROQ_API_KEY",       "")   # console.groq.com — fast LLM fallback
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")   # openrouter.ai — free LLM fallback
YOUTUBE_API_KEY    = os.getenv("YOUTUBE_API_KEY",    "")   # console.cloud.google.com — channel stats
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID", "")   # UC… id or @handle

# Settings the GUI stores in .env rather than in this file
ENV_KEYS = [
    "GEMINI_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "GROQ_API_KEY",
    "OPENROUTER_API_KEY", "YOUTUBE_API_KEY", "YOUTUBE_CHANNEL_ID",
]

# ─────────────────────────────────────────
#  Free API stack  (keyless — work out of the box)
# ─────────────────────────────────────────
# Ground every script in real facts from Wikipedia + arXiv, and cite them
FACT_GROUNDING = True

# Local Ollama uses installed models only; hosted routes require free-tier keys.
# Kept for legacy settings imports; the retired keyless Pollinations route is unused.
KEYLESS_LLM_FALLBACK = False
LLM_ROUTE           = "auto"       # auto: configured local model, then free cloud; local: Ollama only
OLLAMA_BASE_URL     = "http://127.0.0.1:11434"
OLLAMA_MODEL        = ""
SCRIPT_REVIEW_ENABLED = True
VOICE_CACHE_ENABLED = True
VOICE_CACHE_DIR     = ""           # default: DATA_DIR/voice-cache
GROQ_MODEL           = "openai/gpt-oss-120b"
OPENROUTER_MODEL     = "openrouter/free"

# Motion scenes are drawn locally from a validated storyboard. Existing saved
# preferences override these defaults in the load_settings call below.
VISUAL_MODE = "motion"                # motion | hybrid | classic
VISUAL_AI_ENABLED = True
VISUAL_PLANNER_MODEL = "openrouter/free"
VISUAL_REVIEW_MODEL = "openrouter/free"
VISUAL_REVIEW_ENABLED = False          # explicit opt-in before uploading frames
OPENROUTER_FREE_ONLY = True
MOTION_QUALITY = "standard"            # draft | standard | high
MOTION_WORKERS = 1                     # conservative on Windows/laptops

# Image sources tried after Pexels, in order:
#   pixabay (key) · nasa (public domain, space topics) · openverse (CC)
IMAGE_SOURCES = ["pixabay", "nasa", "openverse"]

# Trend Radar — Google Trends region and arXiv fields to watch
TRENDS_GEO       = "US"
ARXIV_CATEGORIES = ["astro-ph.CO", "gr-qc", "quant-ph", "cs.AI", "q-bio.NC"]

# ─────────────────────────────────────────
#  Your Channel Identity
#  The more specific you are, the better the AI's topic suggestions.
# ─────────────────────────────────────────
CHANNEL_DESCRIPTION = """
A mind-bending science and technology channel in the style of Vsauce, Veritasium, and Kurzgesagt.
We focus heavily on deep, existential "What If" scenarios, the extreme future of Artificial Intelligence,
theoretical physics, and mind-expanding thought experiments.
Every video should explore the absolute limits of science, space, or tech, making the viewer question reality.
Target audience: highly curious thinkers who want their minds blown by deep scientific and philosophical dives.
""".strip()
CHANNEL_NAME = "My AI Channel"   # shown on-screen and in upload metadata
CHANNEL_PRESET = "general"      # general | news | asmr; each channel profile has its own
TARGET_DURATION_MINUTES = 8.0    # approximate script target; recorded audio sets the real duration

# On-video branding: the "▶ CHANNEL NAME" tag in the hook and on Shorts,
# plus the channel line on thumbnails. Leave BRAND_TEXT empty to use CHANNEL_NAME.
SHOW_CHANNEL_BRAND = True
BRAND_TEXT         = ""

# Default thumbnail style: "impact" | "cinematic" | "split" | "studio"
# (you can switch per video on the Review page)
THUMB_STYLE        = "impact"

# Adds "Made with YAAS by Raunak Patil · https://raunakpatil.com" to descriptions
CREDIT_YAAS        = True


def brand_text() -> str:
    """The on-video brand label, or "" when branding is switched off."""
    import sys
    current = sys.modules[__name__]
    if not current.SHOW_CHANNEL_BRAND:
        return ""
    return (current.BRAND_TEXT or current.CHANNEL_NAME or "").strip()

# ─────────────────────────────────────────
#  Voice (Edge TTS — free, no API key)
#  Voices confirmed to emit word-level timing (needed for caption sync):
#    en-GB-RyanNeural     — British, deep, cinematic
#    en-US-AndrewNeural   — US, warm, authoritative  ← default
#    en-US-BrianNeural    — US, calm, documentary
#    en-US-GuyNeural      — US, clear, neutral
# ─────────────────────────────────────────
VOICE_ID    = "en-US-AndrewNeural"
VOICE_RATE  = "+0%"     # natural pace; profiles can choose a slower delivery
VOICE_PITCH = "+0Hz"    # preserve the selected voice's natural pitch

# ─────────────────────────────────────────
#  AI Model
#  Starting model for the fallback chain — the system auto-switches
#  through all models below if quota or errors are hit.
#  Change this in the GUI under Settings → AI Model (saved to settings.json).
#  Current free-tier text choices plus 2.5 compatibility for existing projects.
#  Google restricts 2.5 access for new projects; prefer a supported 3.x model.
#  Quotas depend on the provider account; retired model IDs are not retried.
# ─────────────────────────────────────────
GEMINI_MODEL = "gemini-3.8-flash"

# ─────────────────────────────────────────
#  Video Dimensions
# ─────────────────────────────────────────
VIDEO_WIDTH  = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS    = 24

# YouTube Shorts dimensions (9:16 vertical)
SHORTS_WIDTH  = 1080
SHORTS_HEIGHT = 1920
SHORTS_FPS    = 30

# ─── Visual Style ───────────────────────
# Ken Burns effect: how much to zoom/pan each image
KB_ZOOM_START = 1.00    # starting scale (1.0 = no zoom)
KB_ZOOM_END   = 1.10    # ending scale   (1.1 = 10% zoom in)

# Image crossfade duration (seconds)
# 0.0 = instant cut  |  0.5 = snappy  |  0.7 = smooth  |  1.2 = dreamy
CROSSFADE_DURATION = 0.7

# Normal video B-roll cycling
BROLL_INTERVAL  = 10.0   # seconds each image stays on screen
BROLL_XFADE_DUR =  1.2   # crossfade duration (must be < BROLL_INTERVAL)

# Render quality preset (ffmpeg libx264)
# "ultrafast" = fastest/largest  |  "veryfast" = recommended  |  "medium" = best quality
RENDER_PRESET = "ultrafast"

# Overlay opacity: lower = more image visible but text harder to read (0–1)
OVERLAY_OPACITY = 0.62

# Colour palette — customise to match your brand
COLORS = {
    "background": (10,  10,  20),
    "overlay":    (0,   0,   0),
    "primary":    (99,  102, 241),   # indigo
    "accent":     (167, 139, 250),   # purple
    "highlight":  (251, 191,  36),   # amber
    "white":      (255, 255, 255),
    "light":      (199, 210, 254),   # indigo-200
    "success":    (52,  211, 153),
    "red":        (239,  68,  68),
}

# Fonts — add your own .ttf paths for best results; system falls back gracefully
FONT_PATHS = {
    "bold":    [
        "C:/Windows/Fonts/Impact.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "regular": [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "light":   [
        "C:/Windows/Fonts/segoeuil.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}

# ─────────────────────────────────────────
#  YouTube Upload
# ─────────────────────────────────────────
YOUTUBE_CLIENT_SECRET = "client_secret.json"   # OAuth credentials file (see SETUP.md)
YOUTUBE_SCOPES        = ["https://www.googleapis.com/auth/youtube.upload"]
VIDEO_CATEGORY_ID     = "27"       # 27 = Education
VIDEO_PRIVACY         = "public"   # "public" | "unlisted" | "private"

# ─────────────────────────────────────────
#  Background Music
#  Drop MP3/WAV files into the  music library/  folder.
#  Tracks are shuffled and looped automatically to match video length.
# ─────────────────────────────────────────
MUSIC_ENABLED     = False        # clean narration by default; enable a local music bed
MUSIC_VOLUME      = 0.08          # quiet bed, ducked further while narration speaks
MUSIC_LIBRARY_DIR = os.path.join(os.path.dirname(__file__), "music library")
MUSIC_TRACK       = ""           # selected imported music ID; empty uses the local folder
SFX_ENABLED       = False
SFX_TRACK         = ""           # selected imported effect ID; never a random music track
SFX_VOLUME        = 0.08
SFX_MODE          = "transitions"  # brief section transitions | looping ambience
SOUND_MODE        = "auto"         # AI direction | manual selected tracks | off
SOUND_INTENSITY   = "low"          # low | balanced
AUDIO_MASTERING   = True
CAPTION_STYLE     = "highlight"    # highlight measured words | plain | off (Motion/Hybrid)

# ─────────────────────────────────────────
#  Paths  (do not change unless you know what you're doing)
# ─────────────────────────────────────────
OUTPUT_DIR   = "output"
IMAGES_DIR   = "output/images"


# ─────────────────────────────────────────
#  Your saved settings  (settings.json — keep this block at the end)
# ─────────────────────────────────────────
# Settings the GUI may save. Secrets are never stored here; they go to .env.
USER_SETTINGS = (
    "CHANNEL_NAME", "CHANNEL_DESCRIPTION", "CHANNEL_PRESET", "TARGET_DURATION_MINUTES", "SHOW_CHANNEL_BRAND", "BRAND_TEXT",
    "THUMB_STYLE", "CREDIT_YAAS",
    "VOICE_ID", "VOICE_RATE", "VOICE_PITCH",
    "GEMINI_MODEL", "GROQ_MODEL", "OPENROUTER_MODEL", "LLM_ROUTE", "OLLAMA_BASE_URL", "OLLAMA_MODEL",
    "SCRIPT_REVIEW_ENABLED", "VOICE_CACHE_ENABLED",
    "VISUAL_MODE", "VISUAL_AI_ENABLED", "VISUAL_PLANNER_MODEL",
    "VISUAL_REVIEW_MODEL", "VISUAL_REVIEW_ENABLED", "OPENROUTER_FREE_ONLY",
    "MOTION_QUALITY", "MOTION_WORKERS",
    "FACT_GROUNDING", "KEYLESS_LLM_FALLBACK", "IMAGE_SOURCES", "TRENDS_GEO",
    "MUSIC_ENABLED", "MUSIC_VOLUME", "MUSIC_TRACK",
    "SFX_ENABLED", "SFX_TRACK", "SFX_VOLUME", "SFX_MODE",
    "SOUND_MODE", "SOUND_INTENSITY", "AUDIO_MASTERING", "CAPTION_STYLE",
    "OVERLAY_OPACITY", "KB_ZOOM_START", "KB_ZOOM_END",
    "BROLL_INTERVAL", "BROLL_XFADE_DUR", "RENDER_PRESET", "VIDEO_PRIVACY",
)

# The defaults above, before settings.json is applied
DEFAULTS = {k: copy.deepcopy(globals()[k]) for k in USER_SETTINGS}


def _valid_setting(key: str, value) -> bool:
    """A saved value must be a known setting with the same type as its default."""
    if key not in DEFAULTS:
        return False
    choices = {
        "VISUAL_MODE": {"motion", "hybrid", "classic"},
        "MOTION_QUALITY": {"draft", "standard", "high"},
        "CHANNEL_PRESET": {"general", "news", "asmr"},
        "SFX_MODE": {"transitions", "ambience"},
        "SOUND_MODE": {"auto", "manual", "off"},
        "SOUND_INTENSITY": {"low", "balanced"},
        "LLM_ROUTE": {"auto", "local"},
        "CAPTION_STYLE": {"highlight", "plain", "off"},
    }
    if key in choices:
        return isinstance(value, str) and value in choices[key]
    if key == "OLLAMA_BASE_URL":
        from urllib.parse import urlsplit
        try:
            parsed = urlsplit(value) if isinstance(value, str) and 0 < len(value) <= 200 and not any(ord(c) <= 32 or ord(c) == 127 for c in value) else None
            return bool(parsed and parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                        and parsed.username is None and parsed.password is None and not parsed.query
                        and not parsed.fragment and parsed.path in {"", "/"}
                        and 1 <= (parsed.port if parsed.port is not None else 80) <= 65535)
        except ValueError:
            return False
    if key == "OLLAMA_MODEL":
        return (isinstance(value, str) and (not value or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", value) is not None)
                and "cloud" not in value.lower() and "://" not in value and ".." not in value.split("/"))
    if key == "MOTION_WORKERS":
        return type(value) is int and 1 <= value <= 4
    if key in {"MUSIC_TRACK", "SFX_TRACK"}:
        return isinstance(value, str) and (not value or re.fullmatch(r"[a-f0-9]{32}", value) is not None)
    if key == "SFX_VOLUME":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1
    if key == "TARGET_DURATION_MINUTES":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and .25 <= value <= 60
    if key in {"VISUAL_PLANNER_MODEL", "VISUAL_REVIEW_MODEL"}:
        return isinstance(value, str) and 0 < len(value.strip()) <= 200 and not any(c.isspace() for c in value)
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    return False


def load_settings(path: str | None = None) -> dict:
    """Saved settings, skipping unknown keys and wrong types. Missing file → {}."""
    path = path or SETTINGS_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"   ⚠ Ignoring {os.path.basename(path)}: {e}")
        return {}
    if not isinstance(data, dict):
        print(f"   ⚠ Ignoring {os.path.basename(path)}: expected a JSON object")
        return {}
    good = {k: v for k, v in data.items() if _valid_setting(k, v)}
    # Preserve an existing deliberately selected soundtrack on upgrade. An old
    # enabled switch with no selected asset remains eligible for Automatic.
    if "SOUND_MODE" not in data and (
            good.get("MUSIC_ENABLED") and good.get("MUSIC_TRACK")
            or good.get("SFX_ENABLED") and good.get("SFX_TRACK")):
        good["SOUND_MODE"] = "manual"
    for k in data.keys() - good.keys():
        print(f"   ⚠ Ignoring unknown or invalid setting in {os.path.basename(path)}: {k}")
    return good


def save_settings(values: dict, path: str | None = None) -> None:
    """
    Merge values into settings.json. Only values that differ from the defaults
    are kept, so a later change to a default in this file still reaches you.
    Written to a temp file and renamed, so a crash never leaves half a file.
    """
    bad = [k for k, v in values.items() if not _valid_setting(k, v)]
    if bad:
        raise ValueError("Not a valid setting: " + ", ".join(sorted(bad)))
    path = path or SETTINGS_PATH
    merged = {**load_settings(path), **values}
    # Keep the explicit sound choice even when it equals the new default, so
    # legacy selected-track migration cannot undo a switch to Automatic.
    merged = {k: v for k, v in merged.items() if v != DEFAULTS[k] or k == "SOUND_MODE"}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def import_legacy_config(path: str) -> dict:
    """
    Read the settings an older YAAS saved into its config.py, without running it
    (only literal values are parsed). Returns the values that differ from today's
    defaults, ready for save_settings().
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    found = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in DEFAULTS):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                continue                  # not a plain literal (e.g. an expression)
            key = node.targets[0].id
            if key == "CHANNEL_DESCRIPTION" and isinstance(value, str):
                value = value.strip()     # older versions kept surrounding newlines
            if _valid_setting(key, value) and value != DEFAULTS[key]:
                found[key] = value
    return found


globals().update(load_settings())


# Profile settings are local to a request or worker. Never swap module globals
# while another channel's request is reading them. Existing `import config`
# consumers continue to work, and secrets/default settings stay shared.
from contextlib import contextmanager
from contextvars import ContextVar
import types as _types
import sys as _sys

_profile_values = ContextVar("yaas_profile_values", default=None)


@contextmanager
def profile_context(values):
    """Apply a captured profile to this execution context, restoring it on exit."""
    token = _profile_values.set(copy.deepcopy(values or {}))
    try:
        yield
    finally:
        _profile_values.reset(token)


class _ProfileConfig(_types.ModuleType):
    def __getattribute__(self, name):
        if name.isupper():
            values = _types.ModuleType.__getattribute__(self, "_profile_values").get()
            if values is not None and name in values:
                return values[name]
        return _types.ModuleType.__getattribute__(self, name)


_sys.modules[__name__].__class__ = _ProfileConfig
