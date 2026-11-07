"""Thumbnail rendering for the current video (designs live in video/thumbnail.py)."""
import os

import config
from studio.state import is_shorts, log, state

def thumb_backgrounds(image_map):
    """Every usable background photo from the video, in scene order."""
    paths = []
    keys = list(image_map or {})
    # Preserve the familiar numeric chapter order, while named/mixed section
    # IDs follow the script's insertion order instead of failing int(...).
    if all(str(key).isdigit() for key in keys):
        keys.sort(key=lambda key: int(key))
    for k in keys:
        v = image_map[k]
        for p in (v if isinstance(v, list) else [v]):
            if p and os.path.isfile(p) and p not in paths:
                paths.append(p)
    return paths

def thumb_settings(script):
    from video import thumbnail
    style = state.get("thumb_style") or getattr(config, "THUMB_STYLE", thumbnail.DEFAULT_STYLE)
    text = state.get("thumb_text") or thumbnail.auto_text(script.get("title", ""))
    return style, text

def thumb_bg(image_map, bg_override=None):
    if bg_override and os.path.exists(bg_override):
        return bg_override
    bgs = thumb_backgrounds(image_map)
    return bgs[state.get("thumb_bg_index", 0) % len(bgs)] if bgs else None

def make_thumbnail(script, image_map, output_dir, bg_override=None):
    """Render thumbnail.jpg in the chosen style — 16:9 for videos, 9:16 for Shorts."""
    from video import thumbnail
    style, text = thumb_settings(script)
    return thumbnail.render(style, os.path.join(output_dir, "thumbnail.jpg"), text,
                            thumb_bg(image_map, bg_override), config.brand_text(),
                            portrait=is_shorts(script))

def ai_thumbnail_bg(script, research):
    """Keep old clients from silently contacting a retired keyless service."""
    raise ValueError("The former keyless AI-art service is unavailable. Use a saved photo or one of the local graphic thumbnail designs.")
