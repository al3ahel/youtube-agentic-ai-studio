"""Keep cached media bound to its channel when the studio process restarts."""
import json
import os
from pathlib import Path
import re
import tempfile


MANIFEST_NAME = "project_profile.json"
_PROFILE_ID = re.compile(r"(?:default|[a-f0-9]{32})\Z")
_MAX_MANIFEST_BYTES = 8192


def _profile_id(value):
    if not isinstance(value, str) or not _PROFILE_ID.fullmatch(value):
        raise ValueError("The saved project has an invalid channel profile identity.")
    return value


def _read_identity(path):
    try:
        if path.is_symlink() or path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ValueError("Invalid identity file")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid identity object")
        owner = _profile_id(value.get("profile_id"))
        name = value.get("profile_name")
        if not isinstance(name, str) or len(name) > 200 or "\x00" in name:
            raise ValueError("Invalid profile label")
        return owner
    except (OSError, ValueError) as exc:
        raise ValueError(
            "The saved project identity is unreadable. Reload its archived project, "
            "or start a new video with Research enabled before reusing this workspace."
        ) from exc


def cached_profile_owner(output_dir):
    """Read the durable owner, including legacy work, without loading media."""
    output = Path(output_dir)
    manifest = output / MANIFEST_NAME
    # Broken symlinks are present identities, not permission to adopt a legacy
    # project. Keep the same fail-closed behavior for running and deleting.
    if os.path.lexists(manifest):
        return _read_identity(manifest)
    if any(os.path.lexists(output / name) for name in ("script.json", "research.json")):
        return "default"
    return None


def validate_cached_profile(profile_id, steps, output_dir, state) -> None:
    """Reject partial runs that would adopt another channel's cached project.

    A live script/research object can supply the current owner. After restart,
    the manifest supplies it; projects predating channel profiles belong to the
    default profile. A fresh Research run replaces the old project and is safe
    even when the old manifest is corrupt.
    """
    requested = _profile_id(profile_id)
    if "research" in steps:
        return

    live_project = bool(state.get("script") or state.get("research"))
    # Parsing happens even with a live owner to fail closed on
    # an unreadable cached identity before any partial job changes its files.
    saved_owner = cached_profile_owner(output_dir)
    if not live_project and saved_owner is None:
        return
    live_owner = state.get("profile_id") if live_project else None
    owner = _profile_id(live_owner) if live_owner is not None else saved_owner or "default"
    if owner != requested:
        raise ValueError(
            "This cached project belongs to another channel profile. Select its channel, "
            "reload its archived project, or start a new video with Research enabled."
        )


def write_project_identity(output_dir, profile_id, profile_name) -> None:
    """Atomically persist the project owner alongside script and narration."""
    owner = _profile_id(profile_id)
    if not isinstance(profile_name, str) or len(profile_name) > 200 or "\x00" in profile_name:
        raise ValueError("Enter a valid channel profile label for the saved project.")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    target = output / MANIFEST_NAME
    fd, temporary = tempfile.mkstemp(prefix=".project-profile-", suffix=".json", dir=output)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"profile_id": owner, "profile_name": profile_name}, handle,
                      indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
