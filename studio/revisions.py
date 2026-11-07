"""Small revision tokens for the project currently open in Review."""
import hashlib
import json
import os
import stat


def plan_revision(plan):
    """Identify the exact storyboard, including its narration and media bindings."""
    if not plan:
        return None
    payload = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_revision(path):
    """Change a media URL when the file is replaced, without exposing its path."""
    if not path:
        return None
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return f"{info.st_mtime_ns:x}-{info.st_size:x}"


def validate_expected_revision(value):
    if value is not None and (not isinstance(value, str) or len(value) != 64
                              or any(character not in "0123456789abcdef" for character in value)):
        raise ValueError("The storyboard revision is invalid. Reload Review and try again.")
    return value
