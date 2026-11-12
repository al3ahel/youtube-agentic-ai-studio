"""Fast, offline checks for the stages about to run.

This module deliberately imports no rendering or speech SDKs. A missing optional
dependency must produce a preparation error, not prevent the studio from opening.
"""
from importlib.util import find_spec
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import config


def validate_topic_steps(steps, topic):
    """A new topic must reach research instead of silently rendering old work."""
    if topic.strip() and "research" not in steps:
        raise ValueError("Enable Research to generate a new topic, or clear the topic to redo the current project.")


def _available(module):
    try:
        return find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _pip_fix(packages):
    # PowerShell needs & before a quoted executable; the same interpreter is
    # essential when several Python installations or virtualenvs coexist.
    prefix = "& " if os.name == "nt" else ""
    executable = str(sys.executable).replace('"', '\\"')
    return f'{prefix}"{executable}" -m pip install ' + " ".join(packages)


def _node_version():
    node = shutil.which("node")
    if not node:
        return None
    try:
        result = subprocess.run([node, "--version"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=5, check=False)
        match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", result.stdout or "")
        return tuple(map(int, match.groups())) if result.returncode == 0 and match else None
    except (OSError, subprocess.SubprocessError):
        return None


def check_environment(steps, visual_mode=None):
    """Return stage-specific, actionable checks without making network calls."""
    selected = set(steps or [])
    mode = visual_mode or getattr(config, "VISUAL_MODE", "motion")
    checks = []

    def add(name, ok, message, fix=None):
        item = {"name": name, "ok": bool(ok), "message": message}
        if fix and not ok:
            item["fix"] = fix
        checks.append(item)

    add("visual_mode", mode in {"motion", "hybrid", "classic"},
        f"Visual mode: {mode}", "Choose Motion, Hybrid or Classic in the studio.")
    add("python", (3, 10) <= sys.version_info[:2] < (3, 13),
        f"Python {sys.version.split()[0]} · {sys.executable}",
        "Use Python 3.11 or 3.12 in a project virtual environment.")

    dependencies = {}
    if selected & {"research", "script", "images", "visuals"}:
        dependencies["requests"] = "requests>=2.31.0"
    if "narration" in selected:
        dependencies["edge_tts"] = "edge-tts>=7.3.1"
    if "images" in selected or "video" in selected:
        dependencies["PIL"] = "Pillow>=10.0.0"
    if "video" in selected and mode == "classic":
        dependencies["moviepy"] = "moviepy==1.0.3"
        dependencies["numpy"] = "numpy>=1.24.0,<2.0.0"
    for module, package in dependencies.items():
        ok = _available(module)
        add(module, ok, f"{module}: {'available' if ok else 'not installed in this Python environment'}",
            _pip_fix([f'"{package}"']))

    motion = mode in {"motion", "hybrid"} and bool(selected & {"visuals", "video"})
    mixing = "music" in selected and (getattr(config, "SOUND_MODE", "auto") == "auto"
        or config.MUSIC_ENABLED or getattr(config, "SFX_ENABLED", False) or getattr(config, "AUDIO_MASTERING", True))
    if "narration" in selected or motion or mixing:
        path = shutil.which("ffmpeg")
        add("ffmpeg", bool(path), f"ffmpeg: {path or 'not on PATH'}",
            "Install FFmpeg including ffprobe, add its bin directory to PATH, then restart YAAS.")
    if motion or mixing:
        path = shutil.which("ffprobe")
        add("ffprobe", bool(path), f"ffprobe: {path or 'not on PATH'}",
            "Install FFmpeg including ffprobe, add its bin directory to PATH, then restart YAAS.")
    if motion:
        version = _node_version()
        add("node", bool(version and version[0] >= 22),
            "Node.js " + (".".join(map(str, version)) if version else "not found") + " (22 or later required)",
            "Install Node.js 22 or later, then reopen the terminal and restart YAAS.")
        renderer = Path(config.ROOT_DIR) / "renderers" / "hyperframes"
        try:
            manifest = json.loads((renderer / "package.json").read_text(encoding="utf-8"))
            required = list(manifest.get("dependencies", {}))
        except (OSError, ValueError, TypeError):
            required = ["hyperframes", "gsap", "@fontsource-variable/inter"]
        missing = [name for name in required if not (renderer / "node_modules" / name / "package.json").is_file()]
        add("renderer_dependencies", not missing,
            "Motion renderer dependencies " + ("available" if not missing else "missing: " + ", ".join(missing)),
            'npm ci --prefix renderers/hyperframes')

    return {"ok": all(item["ok"] for item in checks), "checks": checks}
