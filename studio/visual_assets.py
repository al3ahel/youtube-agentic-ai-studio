"""Bind saved photo maps to their script, channel and local file contents.

The existing image_map.json stays a simple section-to-paths mapping. A missing
binding is legacy data, not proof that those photos belong to a new project.
Callers decide whether an explicit legacy-photo action permits that case.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile

from studio.project_identity import _profile_id
from video.timeline import script_signature


BINDING_NAME = "image_binding.json"
_PICTURES = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_BINDING_BYTES = 4 * 1024 * 1024
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024 * 1024


def _signature(script):
    sections = script.get("sections") if isinstance(script, dict) else None
    if (not isinstance(sections, list) or len(sections) > 80
            or any(not isinstance(row, dict) or not isinstance(row.get("narration", ""), str)
                   for row in sections)):
        raise ValueError("Saved photos need a valid current script.")
    return script_signature(script)


def _canonical_map(output, image_map):
    root = Path(output).resolve()
    if not isinstance(image_map, dict) or len(image_map) > 80:
        raise ValueError("The saved photo map is invalid or too large.")
    result = {}
    for key, values in image_map.items():
        if isinstance(key, bool) or not isinstance(key, (str, int)):
            raise ValueError("Saved photo sections must have stable identifiers.")
        section = str(key)
        if not section or len(section) > 200 or any(ord(char) < 32 for char in section) or section in result:
            raise ValueError("The saved photo section identity is invalid or duplicated.")
        values = [values] if isinstance(values, (str, Path)) else values
        if not isinstance(values, (list, tuple)) or len(values) > 20:
            raise ValueError("A saved photo section must contain at most 20 local pictures.")
        result[section] = []
        for value in values:
            if not isinstance(value, (str, Path)) or not str(value) or len(str(value)) > 4096:
                raise ValueError("A saved photo path is invalid.")
            text = str(value)
            if any(ord(char) < 32 for char in text) or "://" in text:
                raise ValueError("Saved photos must be local project files.")
            supplied = Path(value)
            if supplied.is_absolute():
                candidate = supplied
                if ".." in supplied.parts:
                    raise ValueError("Saved photos must stay inside their project.")
            else:
                # Old Windows maps used native separators. New bindings always
                # hash portable POSIX paths, including after an archive moves.
                portable = PurePosixPath(text.replace("\\", "/"))
                if portable.is_absolute() or ":" in text or ".." in portable.parts:
                    raise ValueError("Saved photos must stay inside their project.")
                candidate = root.joinpath(*portable.parts)
            resolved = candidate.resolve()
            if (resolved != candidate.absolute() or not resolved.is_relative_to(root)
                    or resolved.suffix.lower() not in _PICTURES or not resolved.is_file()):
                raise ValueError("A saved photo is missing, linked or outside its project.")
            result[section].append(resolved.relative_to(root).as_posix())
    return result


def _map_hash(image_map):
    encoded = json.dumps(image_map, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _fingerprints(root, image_map):
    files = {}
    total = 0
    for relative in sorted({path for paths in image_map.values() for path in paths}):
        source = root / relative
        before = source.stat()
        total += before.st_size
        if not 0 < before.st_size <= _MAX_FILE_BYTES or total > _MAX_TOTAL_BYTES:
            raise ValueError("Saved photos exceed the local verification size limit.")
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                size += len(block)
                if size > _MAX_FILE_BYTES:
                    raise ValueError("A saved photo changed during verification.")
                digest.update(block)
        after = source.stat()
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if size != before.st_size or identity(before) != identity(after):
            raise ValueError("A saved photo changed during verification.")
        files[relative] = {"bytes": size, "sha256": digest.hexdigest()}
    return files


def save_binding(output, image_map, script, profile_id):
    """Atomically bind a newly saved/trusted map; never modify the map itself.

    The caller must already have established the project owner and the source
    of these photos. This must not be used to bless a failed existing binding.
    Absolute local paths and portable relative paths describe the same map.
    """
    root = Path(output).resolve()
    owner, signature = _profile_id(profile_id), _signature(script)
    canonical = _canonical_map(root, image_map)
    value = {"version": 1, "script_signature": signature, "profile_id": owner,
             "map_sha256": _map_hash(canonical), "files": _fingerprints(root, canonical)}
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > _MAX_BINDING_BYTES:
        raise ValueError("The saved photo binding exceeds the local metadata size limit.")
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".image-binding-", suffix=".json", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
        os.replace(temporary, root / BINDING_NAME)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return value


def validate_binding(output, image_map, script, profile_id):
    """Return True for verified photos, None for legacy, False for invalid.

    A malformed, broken-link, wrong-owner, stale-script or changed-file binding
    is invalid, never a legacy opt-in. The function neither rewrites files nor
    discovers photos from section filenames.
    """
    try:
        root = Path(output).resolve()
        owner, signature = _profile_id(profile_id), _signature(script)
        canonical = _canonical_map(root, image_map)
        path = root / BINDING_NAME
        if not os.path.lexists(path):
            return None
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_BINDING_BYTES:
            return False
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1
                or value.get("profile_id") != owner or value.get("script_signature") != signature
                or value.get("map_sha256") != _map_hash(canonical)):
            return False
        return value.get("files") == _fingerprints(root, canonical)
    except (OSError, ValueError, TypeError, RuntimeError):
        return False
