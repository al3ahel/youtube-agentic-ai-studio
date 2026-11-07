"""Profile-owned local audio imports; source URLs are metadata, never downloads."""
import base64
import binascii
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from urllib.parse import urlencode, urlsplit
import uuid

import config
from studio.state import job_lock

MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_BASE64_CHARS = 4 * ((MAX_AUDIO_BYTES + 2) // 3)
MAX_REQUEST_BYTES = MAX_BASE64_CHARS + 64 * 1024
MAX_DURATION = 7200
_REGISTRY_LIMIT = 8 * 1024 * 1024
_MAX_ASSETS = 1000
_ID = re.compile(r"[a-f0-9]{32}\Z")
_PROFILE_ID = re.compile(r"(?:default|[a-f0-9]{32})\Z")
_FORMATS = {".mp3": "mp3", ".wav": "wav", ".ogg": "ogg", ".m4a": "mov", ".flac": "flac"}
_KINDS = {"music", "sfx"}
_CONTENT_ID = {"unknown", "registered", "not_registered"}
_METADATA_FIELDS = {"filename", "kind", "title", "source_url", "creator", "license_note", "content_id"}
_PUBLIC_FIELDS = ("id", "kind", "title", "filename", "duration", "bytes", "source_url",
                  "creator", "license_note", "content_id")
_store_lock = threading.RLock()


class AudioLibraryError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _profile_id(value):
    from studio.channels import get_profile
    if not isinstance(value, str) or not _PROFILE_ID.fullmatch(value):
        raise AudioLibraryError("Choose an existing channel profile for this audio.", 404)
    try:
        profile = get_profile(value)
    except (ValueError, LookupError) as exc:
        raise AudioLibraryError(str(exc), getattr(exc, "status", 404)) from exc
    return profile["id"]


def _folder(profile_id, *, create=False):
    data = Path(config.DATA_DIR).expanduser().resolve()
    root = data / "audio-library" / profile_id
    try:
        if root.resolve() != root or root.exists() and not root.is_dir():
            raise AudioLibraryError("The audio library folder is unavailable.", 409)
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root
    except (OSError, RuntimeError) as exc:
        raise AudioLibraryError("The audio library folder is unavailable.", 409) from exc


def _text(value, label, maximum, *, multiline=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise AudioLibraryError(f"{label} must be text of at most {maximum:,} characters.")
    if any((ord(character) < 32 and (not multiline or character not in "\n\r\t"))
           or ord(character) == 127 for character in value):
        raise AudioLibraryError(f"{label} contains unsupported characters.")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise AudioLibraryError(f"{label} contains unsupported characters.") from exc
    return value.strip()


def _metadata(values):
    filename = _text(values.get("filename"), "Filename", 255)
    if not filename or any(character in filename for character in "/\\:"):
        raise AudioLibraryError("Choose an audio file by its filename, without a folder path.")
    if Path(filename).suffix.lower() not in _FORMATS:
        raise AudioLibraryError("Import an MP3, WAV, OGG, M4A or FLAC file.")
    kind = values.get("kind")
    if not isinstance(kind, str) or kind not in _KINDS:
        raise AudioLibraryError("Choose music or sfx for the audio type.")
    title = _text(values.get("title", ""), "Title", 200) or Path(filename).stem[:200]
    source_url = _text(values.get("source_url", ""), "Source URL", 2048)
    if source_url:
        try:
            parsed = urlsplit(source_url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or any(character.isspace() for character in source_url)):
                raise ValueError("Unsupported source URL")
            parsed.port  # Reject malformed port values; no source URL is fetched.
        except ValueError as exc:
            raise AudioLibraryError("Source URL must be an ordinary http or https page without sign-in credentials.") from exc
    content_id = values.get("content_id", "unknown")
    if not isinstance(content_id, str) or content_id not in _CONTENT_ID:
        raise AudioLibraryError("Choose unknown, registered or not_registered for Content ID.")
    return {"filename": filename, "kind": kind, "title": title, "source_url": source_url,
            "creator": _text(values.get("creator", ""), "Creator", 200),
            "license_note": _text(values.get("license_note", ""), "License note", 2000, multiline=True),
            "content_id": content_id}


def _read_registry(root):
    path = root / "registry.json"
    try:
        if path.is_symlink():
            raise ValueError("Linked registry")
        if not path.exists():
            return {}
        if not path.is_file() or path.stat().st_size > _REGISTRY_LIMIT:
            raise ValueError("Invalid registry size")
        registry = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(registry, dict) or registry.get("version") != 1
                or not isinstance(registry.get("assets"), dict) or len(registry["assets"]) > _MAX_ASSETS):
            raise ValueError("Invalid registry")
        records = {}
        for identifier, row in registry["assets"].items():
            if not _ID.fullmatch(identifier) or not isinstance(row, dict) or row.get("id") != identifier:
                raise ValueError("Invalid audio identity")
            metadata = _metadata(row)
            duration, size = row.get("duration"), row.get("bytes")
            if (isinstance(duration, bool) or not isinstance(duration, (float, int))
                    or not math.isfinite(duration) or not 0 < duration <= MAX_DURATION
                    or type(size) is not int or not 0 < size <= MAX_AUDIO_BYTES):
                raise ValueError("Invalid audio metadata")
            records[identifier] = {"id": identifier, **metadata, "duration": duration, "bytes": size}
        return records
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise AudioLibraryError("The saved audio library metadata could not be read. Its files have been kept.", 409) from exc


def _write_registry(root, records):
    fd, temporary = tempfile.mkstemp(prefix=".registry-", suffix=".json", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "assets": records}, handle, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, root / "registry.json")
    finally:
        Path(temporary).unlink(missing_ok=True)


def _asset_path(root, record):
    folder = root / record["kind"]
    path = folder / (record["id"] + Path(record["filename"]).suffix.lower())
    try:
        if (folder.resolve() != folder or path.resolve() != path or not path.is_file()
                or path.stat().st_size != record["bytes"]):
            raise AudioLibraryError("That audio file is no longer available in this channel's library.", 404)
        return path
    except (OSError, RuntimeError) as exc:
        raise AudioLibraryError("That audio file is no longer available in this channel's library.", 404) from exc


def _public_asset(profile_id, record):
    result = {key: record[key] for key in _PUBLIC_FIELDS}
    result["preview_url"] = f"/api/audio-library/{record['id']}/preview?" + urlencode({"profile_id": profile_id})
    return result


def list_audio_assets(profile_id):
    """List readable assets owned by one explicit channel, without local paths."""
    with job_lock, _store_lock:
        profile_id = _profile_id(profile_id)
        root = _folder(profile_id)
        assets = []
        for record in _read_registry(root).values():
            try:
                _asset_path(root, record)
            except AudioLibraryError:
                continue
            assets.append(_public_asset(profile_id, record))
        return sorted(assets, key=lambda asset: (asset["kind"], asset["title"].casefold(), asset["id"]))


def resolve_audio_asset(profile_id, identifier, kind=None):
    """Return owned metadata and an absolute local path for trusted internal use."""
    with job_lock, _store_lock:
        profile_id = _profile_id(profile_id)
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            raise AudioLibraryError("That audio asset was not found in this channel's library.", 404)
        if kind is not None and (not isinstance(kind, str) or kind not in _KINDS):
            raise AudioLibraryError("Choose music or sfx for the audio type.")
        root = _folder(profile_id)
        record = _read_registry(root).get(identifier)
        if record is None or kind is not None and record["kind"] != kind:
            raise AudioLibraryError("That audio asset was not found in this channel's library.", 404)
        return {**_public_asset(profile_id, record), "path": str(_asset_path(root, record))}


def _probe_audio(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise AudioLibraryError("Audio import needs FFmpeg's ffprobe. Install FFmpeg and restart the studio, then try again.", 503)
    try:
        # Force an audio container and disallow network protocols, including
        # playlists disguised as an audio filename.
        result = subprocess.run([ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe",
            "-f", _FORMATS[path.suffix.lower()], "-select_streams", "a:0",
            "-show_entries", "stream=codec_type,duration:format=duration", "-of", "json", str(path)],
            check=True, capture_output=True, timeout=10)
        if len(result.stdout) > 65536:
            raise ValueError("Too much audio metadata")
        info = json.loads(result.stdout)
        streams = info.get("streams", [])
        audio = next((stream for stream in streams if isinstance(stream, dict)
                      and stream.get("codec_type") == "audio"), None)
        if audio is None:
            raise ValueError("No audio stream")
        duration = (info.get("format") or {}).get("duration", audio.get("duration"))
        if isinstance(duration, bool):
            raise ValueError("Invalid duration")
        duration = float(duration)
        if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
            raise ValueError("Invalid duration")
        return duration
    except subprocess.TimeoutExpired as exc:
        raise AudioLibraryError("Audio validation took too long. Try a shorter or smaller audio file.") from exc
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
        raise AudioLibraryError("This file is not readable audio, or is longer than two hours. Import a valid MP3, WAV, OGG, M4A or FLAC file.") from exc


def import_audio_asset(profile_id, body):
    """Import user-supplied bytes without selecting audio or changing preferences."""
    if not isinstance(body, dict) or set(body) - (_METADATA_FIELDS | {"data_base64"}):
        raise AudioLibraryError("Send an audio file and its supported source details.")
    metadata = _metadata(body)
    encoded = body.get("data_base64")
    if not isinstance(encoded, str) or not encoded:
        raise AudioLibraryError("Choose a nonempty audio file to import.")
    if len(encoded) > MAX_BASE64_CHARS:
        raise AudioLibraryError("Audio files must be 20 MiB or smaller.", 413)
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AudioLibraryError("The audio file was not valid base64 data. Choose the file again.") from exc
    if not payload:
        raise AudioLibraryError("Choose a nonempty audio file to import.")
    if len(payload) > MAX_AUDIO_BYTES:
        raise AudioLibraryError("Audio files must be 20 MiB or smaller.", 413)
    temporary = None
    try:
        with job_lock, _store_lock:
            profile_id = _profile_id(profile_id)
            root = _folder(profile_id, create=True)
            folder = root / metadata["kind"]
            if folder.resolve() != folder:
                raise AudioLibraryError("The audio library folder is unavailable.", 409)
            folder.mkdir(exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".import-", suffix=Path(metadata["filename"]).suffix.lower(), dir=folder)
            temporary = Path(name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        del payload
        duration = _probe_audio(temporary)
        with job_lock, _store_lock:
            # Profile switching/deletion during probing cannot redirect an import.
            _profile_id(profile_id)
            if _folder(profile_id) != root or folder.resolve() != folder or temporary.resolve() != temporary:
                raise AudioLibraryError("The audio library folder changed during import. Try again.", 409)
            records = _read_registry(root)
            if len(records) >= _MAX_ASSETS:
                raise AudioLibraryError("This channel's audio library has reached its import limit.", 409)
            for _ in range(3):
                identifier = uuid.uuid4().hex
                destination = folder / (identifier + temporary.suffix)
                if identifier not in records and not destination.exists():
                    break
            else:
                raise AudioLibraryError("Could not reserve a new audio file. Try the import again.", 409)
            record = {"id": identifier, **metadata, "duration": duration, "bytes": temporary.stat().st_size}
            os.replace(temporary, destination)
            try:
                _write_registry(root, {**records, identifier: record})
            except Exception:
                destination.unlink(missing_ok=True)
                raise
        return _public_asset(profile_id, record)
    except AudioLibraryError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise AudioLibraryError("The audio file could not be saved. Check the studio's local storage and try again.", 500) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
