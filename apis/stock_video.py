"""Bounded, attributed Pixabay/Pexels footage tied to the current scene.

Pixabay uses the existing image API key and its documented /api/videos/ route.
Search metadata is cached for 24 hours; original media stays local. A scene
brief qualifies the subject before appearance can influence selection. No key,
no suitable clip, or an undecodable download is an explicit graphics fallback.
"""
from __future__ import annotations

import copy
from contextvars import ContextVar
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import threading
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import requests

import config
from apis.http import USER_AGENT
from video.stock_media import (MIN_CROP_FRACTION, StockMediaError, check_stop,
                               crop_fraction, inspect_clip)

SEARCH_URL = "https://api.pexels.com/v1/videos/search"
PIXABAY_SEARCH_URL = "https://pixabay.com/api/videos/"
PROVIDER_VIDEO_HOSTS = {
    "pexels": frozenset({"videos.pexels.com"}),
    "pixabay": frozenset({"cdn.pixabay.com"}),
}
PROVIDER_SOURCE_HOSTS = {
    "pexels": frozenset({"www.pexels.com", "pexels.com"}),
    "pixabay": frozenset({"www.pixabay.com", "pixabay.com"}),
}
VIDEO_HOSTS = frozenset().union(*PROVIDER_VIDEO_HOSTS.values())
SOURCE_HOSTS = frozenset().union(*PROVIDER_SOURCE_HOSTS.values())
LICENSES = {
    "pexels": ("Pexels License", "https://www.pexels.com/license/"),
    "pixabay": ("Pixabay Content License", "https://pixabay.com/service/license-summary/"),
}
MAX_BYTES = 64 * 1024 * 1024
MAX_DOWNLOAD_SECONDS = 60
MAX_SEARCH_BYTES = 4 * 1024 * 1024
CACHE_SECONDS = 24 * 60 * 60
_SCHEMA_VERSION = 1
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_PROFILE = re.compile(r"(?:default|[a-f0-9]{32})\Z")
_SCENE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}\Z")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
_STOP_WORDS = frozenset("a an the and or in on of to with at for from video footage clip stock scene shot cinematic close up beautiful".split())
_credit_lock = threading.Lock()
_status = ContextVar("stock_video_status", default={"available": False, "reason": "not_requested", "provider": "pexels"})


def _key(provider="pexels"):
    setting = provider.upper() + "_API_KEY"
    key = str(getattr(config, setting, "") or os.getenv(setting, "")).strip()
    return "" if key.upper().startswith("YOUR_") else key


def _providers():
    return [name for name in ("pixabay", "pexels") if _key(name)]


def _set_status(available, reason, provider="pexels", **details):
    _status.set({"available": bool(available), "reason": reason, "provider": provider, **details})


def availability_status():
    providers = _providers()
    return {"available": bool(providers), "reason": "ready" if providers else "missing_key",
            "provider": providers[0] if providers else "pexels", "providers": providers}


def get_status():
    return copy.deepcopy(_status.get())


def _number(value, default=0):
    try:
        result = float(value)
        return result if math.isfinite(result) and not isinstance(value, bool) else default
    except (TypeError, ValueError):
        return default


def _text(value, limit=200):
    from apis.openrouter import _redact
    if not isinstance(value, str):
        return ""
    value = _redact(re.sub(r"<[^>]*>", "", value)).replace("[credential removed]", "")
    for provider in ("pexels", "pixabay"):
        key = _key(provider)
        if key:
            value = value.replace(key, "")
    value = re.sub(r"\s+", " ", "".join(c for c in value if c.isprintable() or c.isspace())).strip()
    return value[:limit]


def _safe_url(value, hosts):
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and parts.hostname in hosts and parts.port in {None, 443}
                and parts.username is None and parts.password is None and not parts.fragment)
    except (TypeError, ValueError):
        return False


def _source_url(video, provider="pexels"):
    url = video.get("pageURL") if provider == "pixabay" else video.get("url")
    prefix = "/videos/" if provider == "pixabay" else "/video/"
    if (_safe_url(url, PROVIDER_SOURCE_HOSTS[provider]) and urlsplit(url).path.startswith(prefix)
            and not (_key(provider) and _key(provider) in unquote(url))):
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    identifier = int(video["id"])
    return (f"https://pixabay.com/videos/id-{identifier}/" if provider == "pixabay"
            else f"https://www.pexels.com/video/{identifier}/")


def _safe_video_url(value, provider):
    if not _safe_url(value, PROVIDER_VIDEO_HOSTS[provider]):
        return False
    # Provider keys belong on the fixed search request only. Reject a returned
    # or redirected CDN URL that tries to carry that credential in its URL.
    key = _key(provider)
    return not key or key not in unquote(value)


def _words(value):
    result = set()
    for word in re.findall(r"[^\W_]+", str(value).casefold(), flags=re.UNICODE):
        if word in _STOP_WORDS or word.isdigit():
            continue
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us")):
            word = word[:-1]
        result.add(word)
    return result


def _phrase_matches(phrase, words):
    tokens = _words(phrase)
    return bool(tokens) and tokens <= words


def _brief(value):
    if value is None:
        return {"subject_terms": [], "exclude_terms": [], "theme": "", "palette": []}
    if not isinstance(value, dict) or set(value) - {"subject_terms", "exclude_terms", "theme", "palette"}:
        raise ValueError("The footage brief has unsupported fields.")
    result = {}
    for name, maximum in (("subject_terms", 6), ("exclude_terms", 12)):
        supplied = value.get(name, [])
        if not isinstance(supplied, (list, tuple)) or len(supplied) > maximum:
            raise ValueError("The footage brief has too many subject terms.")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in supplied):
            raise ValueError("The footage brief needs short subject phrases.")
        result[name] = list(dict.fromkeys(_text(item, 100).casefold() for item in supplied))
    if not result["subject_terms"] or any(not _words(item) for item in result["subject_terms"]):
        raise ValueError("The footage brief needs a concrete subject.")
    theme = value.get("theme", "")
    if not isinstance(theme, str) or len(theme) > 160:
        raise ValueError("The footage theme must be short text.")
    result["theme"] = _text(theme, 160)
    palette = value.get("palette", [])
    if not isinstance(palette, (list, tuple)) or len(palette) > 6 or any(not isinstance(color, str) or not _COLOR.fullmatch(color) for color in palette):
        raise ValueError("The footage palette needs up to six hex colors.")
    result["palette"] = list(dict.fromkeys(color.lower() for color in palette))
    return result


def _binding(script_signature, profile_id, scene_id):
    values = (script_signature, profile_id, scene_id)
    if all(value is None for value in values):
        return {"script_signature": None, "profile_id": None, "scene_id": None}
    if (not isinstance(script_signature, str) or not _HASH.fullmatch(script_signature)
            or not isinstance(profile_id, str) or not _PROFILE.fullmatch(profile_id)
            or not isinstance(scene_id, str) or not _SCENE.fullmatch(scene_id)):
        raise ValueError("Footage must be bound to the current script, channel and scene.")
    return {"script_signature": script_signature, "profile_id": profile_id, "scene_id": scene_id}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _metadata(payload, provider):
    rows = payload.get("hits" if provider == "pixabay" else "videos") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    candidates = []
    for video in rows[:20]:
        if (not isinstance(video, dict) or not re.fullmatch(r"[0-9]{1,20}", str(video.get("id", "")))
                or int(video["id"]) <= 0):
            continue
        if provider == "pixabay" and video.get("type") not in {None, "film"}:
            continue
        source = _source_url(video, provider)
        tags = _text(video.get("tags", ""), 1000)
        title = _text(unquote(urlsplit(source).path).replace("-", " ").replace("/", " "), 300)
        user = video.get("user")
        author = _text(user if provider == "pixabay" else user.get("name", "") if isinstance(user, dict) else "", 100)
        files = video.get("videos") if provider == "pixabay" else video.get("video_files")
        if provider == "pixabay":
            files = [{**value, "id": name, "file_type": "video/mp4", "link": value.get("url")}
                     for name, value in files.items() if name in {"large", "medium", "small", "tiny"} and isinstance(value, dict)] if isinstance(files, dict) else []
        if not isinstance(files, list):
            continue
        for asset in files[:30]:
            if not isinstance(asset, dict) or str(asset.get("file_type", "")).lower() != "video/mp4":
                continue
            url = asset.get("link")
            if not _safe_video_url(url, provider) or not urlsplit(url).path.lower().endswith(".mp4"):
                continue
            width, height = int(_number(asset.get("width"))), int(_number(asset.get("height")))
            candidates.append({"provider": provider, "provider_id": str(int(video["id"])),
                "file_id": _text(str(asset.get("id", "")), 80), "url": url,
                "source_url": source, "author": author or ("Pixabay contributor" if provider == "pixabay" else "Pexels contributor"),
                "tags": tags, "title": title, "duration": _number(video.get("duration")),
                "width": width, "height": height, "bytes": int(_number(asset.get("size")))})
    return candidates


def _qualified(candidates, orientation, minimum, brief, query, exclude_ids):
    rejected = {}
    found = {}
    query_words, theme_words = _words(query), _words(brief["theme"])
    for row in candidates[:600]:
        if not isinstance(row, dict):
            continue
        provider = row.get("provider")
        if (provider not in PROVIDER_VIDEO_HOSTS or not re.fullmatch(r"[0-9]{1,20}", str(row.get("provider_id", "")))
                or int(row["provider_id"]) <= 0):
            continue
        identifier = f"{provider}:{row['provider_id']}"
        def reject(reason):
            rejected[reason] = rejected.get(reason, 0) + 1
        if identifier in exclude_ids:
            reject("already_used")
            continue
        if not _safe_video_url(row.get("url"), provider):
            reject("untrusted_media_url")
            continue
        width, height = int(_number(row.get("width"))), int(_number(row.get("height")))
        if min(width, height) < 360 or max(width, height) > 2560:
            reject("unsupported_video_dimensions")
            continue
        duration = _number(row.get("duration"))
        if not minimum <= duration <= 180 or duration <= 0:
            reject("clip_too_short")
            continue
        if _number(row.get("bytes")) > MAX_BYTES:
            reject("file_too_large")
            continue
        retained = crop_fraction(width, height, orientation)
        if retained < MIN_CROP_FRACTION:
            reject("excessive_crop")
            continue
        words = _words(row.get("tags", "")) | _words(row.get("title", ""))
        matched = [term for term in brief["subject_terms"] if _phrase_matches(term, words)]
        if brief["subject_terms"] and not matched:
            reject("subject_mismatch")
            continue
        if any(_phrase_matches(term, words) for term in brief["exclude_terms"]):
            reject("excluded_subject")
            continue
        coverage = len(query_words & words) / max(1, len(query_words))
        theme = len(theme_words & words) / max(1, len(theme_words))
        # Color may rank relevant subjects later, but cannot admit an unrelated
        # clip. At most one sensible rendition per source video is downloaded.
        item = {**row, "matched_subjects": matched, "semantic_score": coverage,
                "theme_score": theme, "crop_fraction": retained}
        item["score"] = (coverage, theme, retained, -abs(max(width, height) - 1920))
        if identifier not in found or item["score"] > found[identifier]["score"]:
            found[identifier] = item
    return sorted(found.values(), key=lambda row: row["score"], reverse=True), rejected


def _candidates(payload, orientation, min_duration):
    """Legacy private Pexels filter, kept for integrations using the adapter."""
    rows, _ = _qualified(_metadata(payload, "pexels"), orientation, min_duration, _brief(None), "", set())
    return [{**row, "video": {"id": int(row["provider_id"])}, "asset": {"id": row["file_id"], "link": row["url"]}}
            for row in rows]


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".stock-", suffix=".json",
                                         mode="w", encoding="utf-8", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _metadata_cache(provider, key, params):
    root = Path(getattr(config, "DATA_DIR", ".yaas")).resolve()
    folder = root / "video-search-cache"
    if folder.resolve() != folder.absolute():
        return None
    identity = {"version": _SCHEMA_VERSION, "provider": provider,
                "account": hashlib.sha256(key.encode()).hexdigest(), "params": params}
    return folder / (_digest(identity) + ".json")


def _search(provider, query, orientation, stop_event, deadline):
    check_stop(stop_event)
    key = _key(provider)
    params = ({"q": query[:100].strip(), "video_type": "film", "safesearch": "true",
               "per_page": 12, "page": 1, "min_width": 360, "min_height": 360, "order": "popular"}
              if provider == "pixabay" else {"query": query, "orientation": orientation,
                                            "size": "medium", "per_page": 12, "page": 1})
    cache = _metadata_cache(provider, key, params)
    if cache is not None:
        try:
            if not cache.is_symlink() and cache.is_file() and cache.stat().st_size <= MAX_SEARCH_BYTES:
                saved = json.loads(cache.read_text(encoding="utf-8"))
                age = time.time() - saved["created"]
                if (saved.get("version") == _SCHEMA_VERSION and 0 <= age < CACHE_SECONDS
                        and isinstance(saved.get("records"), list)):
                    return saved["records"][:600], "cached_search"
        except (OSError, ValueError, TypeError, KeyError):
            pass
    if time.monotonic() >= deadline:
        return [], "request_timeout"
    response = None
    try:
        headers = {"User-Agent": USER_AGENT}
        request_params = dict(params)
        if provider == "pixabay":
            request_params["key"] = key
        else:
            headers["Authorization"] = key
        response = requests.get(PIXABAY_SEARCH_URL if provider == "pixabay" else SEARCH_URL,
            params=request_params, headers=headers, stream=True,
            timeout=(5, min(10, max(.1, deadline - time.monotonic()))), allow_redirects=False)
        check_stop(stop_event)
        if response.status_code == 429:
            return [], "rate_limited"
        if response.status_code in {401, 403}:
            return [], "access_denied"
        if response.status_code != 200:
            return [], "provider_unavailable"
        data = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            check_stop(stop_event)
            if time.monotonic() >= deadline:
                return [], "request_timeout"
            if chunk:
                data.extend(chunk)
                if len(data) > MAX_SEARCH_BYTES:
                    return [], "invalid_search_response"
        rows = _metadata(json.loads(data), provider)
        if cache is not None:
            try:
                _atomic_json(cache, {"version": _SCHEMA_VERSION, "created": time.time(), "records": rows})
            except OSError:
                pass  # A read-only cache must not prevent the requested clip.
        return rows, "searched"
    except InterruptedError:
        raise
    except (requests.RequestException, OSError, ValueError, TypeError):
        return [], "provider_unavailable"
    finally:
        if response is not None:
            response.close()


def _valid_mp4(path):
    try:
        if path.is_symlink() or not 16 <= path.stat().st_size <= MAX_BYTES:
            return False
        with path.open("rb") as handle:
            return b"ftyp" in handle.read(64)
    except OSError:
        return False


def _download(url, path, *, provider=None, stop_event=None, deadline=None):
    path = Path(path)
    provider = provider or next((name for name, hosts in PROVIDER_VIDEO_HOSTS.items() if _safe_url(url, hosts)), None)
    if provider not in PROVIDER_VIDEO_HOSTS or not _safe_video_url(url, provider):
        return False
    check_stop(stop_event)
    if path.is_file() and _valid_mp4(path):
        return True
    deadline = min(deadline or float("inf"), time.monotonic() + MAX_DOWNLOAD_SECONDS)
    response, temporary = None, None
    try:
        for redirect in range(4):
            check_stop(stop_event)
            if time.monotonic() >= deadline or not _safe_video_url(url, provider):
                return False
            response = requests.get(url, stream=True, headers={"User-Agent": USER_AGENT},
                timeout=(5, min(10, max(.1, deadline - time.monotonic()))), allow_redirects=False)
            check_stop(stop_event)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location", "")
                response.close()
                response = None
                if redirect == 3 or not location:
                    return False
                url = urljoin(url, location)
                continue
            break
        if response is None or response.status_code != 200:
            return False
        media_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if media_type not in {"video/mp4", "application/mp4", "application/octet-stream"}:
            return False
        length = response.headers.get("Content-Length")
        if length is not None and (not str(length).isdigit() or not 0 < int(length) <= MAX_BYTES):
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".stock-", suffix=".part", delete=False) as handle:
            temporary = Path(handle.name)
            size = 0
            for chunk in response.iter_content(chunk_size=65536):
                check_stop(stop_event)
                if time.monotonic() >= deadline:
                    return False
                if chunk:
                    size += len(chunk)
                    if size > MAX_BYTES:
                        return False
                    handle.write(chunk)
        if not _valid_mp4(temporary) or length is not None and size != int(length):
            return False
        check_stop(stop_event)
        temporary.replace(path)
        temporary = None
        return True
    except InterruptedError:
        raise
    except (requests.RequestException, OSError, ValueError):
        return False
    finally:
        if response is not None:
            response.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _hash_file(handle):
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _file_identity(path):
    data = path.stat()
    return data.st_dev, data.st_ino, data.st_size, data.st_mtime_ns, data.st_ctime_ns


@lru_cache(maxsize=128)
def _checked_hash(filename, identity):
    path = Path(filename)
    with path.open("rb") as handle:
        digest = _hash_file(handle)
    if _file_identity(path) != identity:
        raise ValueError("Footage changed during verification.")
    return digest


def validate_selection(selection, *, script_signature=None, profile_id=None, scene_id=None):
    """Validate a path-independent receipt; caller supplies trusted ownership."""
    fields = {"version", "provider", "provider_id", "profile_id", "script_signature", "scene_id", "request_sha256",
              "sha256", "bytes", "width", "height", "duration", "query", "subject_terms", "matched_subjects",
              "exclude_terms", "theme", "palette", "palette_score", "crop_fraction", "author", "license", "license_url",
              "validation"}
    if not isinstance(selection, dict) or set(selection) != fields or type(selection.get("version")) is not int or selection["version"] != 1:
        raise ValueError("Invalid footage selection receipt.")
    saved = _binding(selection["script_signature"], selection["profile_id"], selection["scene_id"])
    for name, expected in (("script_signature", script_signature), ("profile_id", profile_id), ("scene_id", scene_id)):
        if expected is not None and saved[name] != expected:
            raise ValueError("Footage belongs to a different script, channel or scene.")
    provider = selection["provider"]
    if (provider not in LICENSES or not isinstance(selection["provider_id"], str)
            or not re.fullmatch(r"[0-9]{1,20}", selection["provider_id"]) or int(selection["provider_id"]) <= 0):
        raise ValueError("Invalid footage provider identity.")
    if (selection["license"], selection["license_url"]) != LICENSES[provider]:
        raise ValueError("Invalid footage license receipt.")
    for field in ("sha256", "request_sha256"):
        if not isinstance(selection[field], str) or not _HASH.fullmatch(selection[field]):
            raise ValueError("Invalid footage fingerprint.")
    for field, minimum, maximum in (("bytes", 16, MAX_BYTES), ("width", 360, 2560), ("height", 360, 2560)):
        if type(selection[field]) is not int or not minimum <= selection[field] <= maximum:
            raise ValueError("Invalid measured footage dimensions or size.")
    for field, minimum, maximum in (("duration", .001, 180), ("crop_fraction", MIN_CROP_FRACTION, 1)):
        if not minimum <= _number(selection[field], -1) <= maximum:
            raise ValueError("Invalid measured footage duration or crop.")
    palette_score = selection["palette_score"]
    if palette_score is not None and not 0 <= _number(palette_score, -1) <= 1:
        raise ValueError("Invalid footage color evidence.")
    if selection["validation"] != "metadata_and_decoded_frames":
        raise ValueError("Footage was not decoded before selection.")
    for name, maximum in (("author", 100), ("query", 200), ("theme", 160)):
        if not isinstance(selection[name], str) or len(selection[name]) > maximum or selection[name] != _text(selection[name], maximum):
            raise ValueError("Invalid footage text metadata.")
    if not selection["query"]:
        raise ValueError("A footage receipt needs its search query.")
    subject_terms = selection["subject_terms"]
    if saved["script_signature"] is not None and not subject_terms:
        raise ValueError("Bound footage needs a concrete subject brief.")
    if subject_terms:
        _brief({key: selection[key] for key in ("subject_terms", "exclude_terms", "theme", "palette")})
    elif subject_terms != [] or selection["exclude_terms"] != [] or selection["palette"] != [] or selection["theme"]:
        raise ValueError("Invalid legacy footage brief.")
    matched = selection["matched_subjects"]
    if not isinstance(matched, list) or len(matched) > 6 or any(not isinstance(term, str) or term not in subject_terms for term in matched):
        raise ValueError("Invalid footage subject evidence.")
    if subject_terms and not matched:
        raise ValueError("Footage does not match the requested subject.")
    return copy.deepcopy(selection)


def _local_asset(root, supplied):
    if not isinstance(supplied, str) or not supplied or "://" in supplied or "\x00" in supplied:
        raise ValueError("Footage must be a confined local MP4.")
    raw = Path(supplied)
    if raw.is_absolute():
        candidate = raw
        if ".." in raw.parts:
            raise ValueError("Footage cannot traverse its project.")
    else:
        portable = PurePosixPath(supplied.replace("\\", "/"))
        if ":" in supplied or ".." in portable.parts or portable.is_absolute():
            raise ValueError("Footage cannot traverse its project.")
        candidate = root.joinpath(*portable.parts)
    resolved = candidate.resolve()
    if (resolved != candidate.absolute() or not resolved.is_relative_to(root)
            or resolved.suffix.lower() != ".mp4" or not resolved.is_file()):
        raise ValueError("Footage is missing, linked or outside its project.")
    return resolved


def verify_footage(footage, output_dir, *, script_signature=None, profile_id=None, scene_id=None):
    """True verified / None legacy without receipt / False invalid or changed."""
    if not isinstance(footage, dict):
        return False
    if "selection" not in footage:
        return None
    try:
        receipt = validate_selection(footage["selection"], script_signature=script_signature,
                                     profile_id=profile_id, scene_id=scene_id)
        provider = receipt["provider"]
        if not _safe_url(footage.get("source_url"), PROVIDER_SOURCE_HOSTS[provider]):
            return False
        source = urlsplit(footage["source_url"])
        if source.query or not source.path.startswith("/videos/" if provider == "pixabay" else "/video/"):
            return False
        path = _local_asset(Path(output_dir).resolve(), footage.get("path"))
        identity = _file_identity(path)
        return (identity[2] == receipt["bytes"] and 16 <= identity[2] <= MAX_BYTES
                and _checked_hash(str(path), identity) == receipt["sha256"])
    except (OSError, ValueError, TypeError, RuntimeError):
        return False


def _credits(output_dir, record):
    """Compatibility ledger for callers outside the project-bound director."""
    path = output_dir / "video_credits.json"
    with _credit_lock:
        records = []
        if path.is_file() and not path.is_symlink() and path.stat().st_size < 2 * 1024 * 1024:
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(previous, list):
                    records = [row for row in previous if isinstance(row, dict)][:200]
            except (OSError, ValueError):
                pass
        records = [row for row in records if row.get("path") != record["path"]]
        records.append(record)
        _atomic_json(path, records)


def fetch_video(query, output_dir, orientation="landscape", min_duration=0, *, brief=None,
                script_signature=None, profile_id=None, scene_id=None, exclude_ids=(), stop_event=None):
    """Acquire one qualified local clip; no generic-subject or paid fallback.

    The four positional arguments remain compatible with old Pexels callers.
    New scene callers supply a concrete brief plus all three binding fields.
    At most one cached search per configured provider and three total candidate
    downloads are attempted. Cancellation always propagates InterruptedError.
    """
    check_stop(stop_event)
    providers = _providers()
    if not providers:
        _set_status(False, "missing_key")
        return None
    provider = providers[0]
    query = _text(query, 200)
    minimum = _number(min_duration, -1)
    if not query or orientation not in {"landscape", "portrait", "square"}:
        _set_status(False, "invalid_query", provider)
        return None
    if not 0 <= minimum <= 180:
        _set_status(False, "invalid_duration", provider)
        return None
    try:
        requirements = _brief(brief)
        owner = _binding(script_signature, profile_id, scene_id)
        if owner["script_signature"] is not None and not requirements["subject_terms"]:
            raise ValueError("Bound footage needs a concrete subject brief.")
        if not isinstance(exclude_ids, (set, frozenset, list, tuple)) or len(exclude_ids) > 160 or any(not isinstance(item, str) or len(item) > 100 for item in exclude_ids):
            raise ValueError("Invalid used-footage list")
        excluded = set(exclude_ids)
        request = {"version": _SCHEMA_VERSION, "query": query, "orientation": orientation,
                   "min_duration": minimum, "brief": requirements, **owner}
        request_hash = _digest(request)
        root = Path(output_dir).resolve()
        directory = root / "assets" / "videos" / request_hash[:32]
        if directory.resolve() != directory.absolute() or not directory.resolve().is_relative_to(root):
            _set_status(False, "invalid_output_directory", provider)
            return None
        receipt_path = directory / "selection.json"
    except (OSError, ValueError, TypeError):
        _set_status(False, "invalid_request_binding", provider)
        return None
    try:
        if receipt_path.is_file() and not receipt_path.is_symlink() and receipt_path.stat().st_size < 16384:
            saved = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (isinstance(saved, dict) and isinstance(saved.get("selection"), dict)
                    and saved["selection"].get("request_sha256") == request_hash
                    and f"{saved.get('provider')}:{saved.get('provider_id')}" not in excluded
                    and saved.get("provider") in providers
                    and verify_footage(saved, root, **owner) is True):
                check_stop(stop_event)
                _set_status(True, "reused_verified_clip", saved["provider"], rejected=0, rejection_reasons={})
                return saved
    except InterruptedError:
        raise
    except (OSError, ValueError, TypeError):
        pass  # A corrupt cache is not permission to reuse it, nor a repair trap.
    deadline = time.monotonic() + MAX_DOWNLOAD_SECONDS
    rejected, downloads, best = {}, 0, None
    last_reason = "no_matching_footage"
    for provider in providers:
        check_stop(stop_event)
        metadata, search_reason = _search(provider, query, orientation, stop_event, deadline)
        candidates, reasons = _qualified(metadata, orientation, minimum, requirements, query, excluded)
        for name, amount in reasons.items():
            rejected[name] = rejected.get(name, 0) + amount
        if not candidates:
            last_reason = search_reason if search_reason not in {"searched", "cached_search"} else (
                "subject_mismatch" if rejected.get("subject_mismatch") else "no_matching_footage")
            continue
        for candidate in candidates:
            check_stop(stop_event)
            if downloads >= 3 or time.monotonic() >= deadline:
                break
            downloads += 1
            temporary = None
            try:
                directory.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix=".candidate-", suffix=".mp4", dir=directory)
                os.close(fd)
                temporary = Path(name)
                if not _download(candidate["url"], temporary, provider=provider, stop_event=stop_event, deadline=deadline):
                    last_reason = "download_failed"
                    rejected[last_reason] = rejected.get(last_reason, 0) + 1
                    continue
                measured = inspect_clip(temporary, orientation, minimum, palette=requirements["palette"],
                                        stop_event=stop_event, deadline=deadline)
                check_stop(stop_event)
                with temporary.open("rb") as handle:
                    digest = _hash_file(handle)
                final = directory / f"{provider}-{candidate['provider_id']}-{digest[:20]}.mp4"
                if final.is_symlink():
                    raise ValueError("A clip cannot replace a linked file")
                temporary.replace(final)
                temporary = None
                license_name, license_url = LICENSES[provider]
                selection = {"version": 1, "provider": provider, "provider_id": candidate["provider_id"], **owner,
                    "request_sha256": request_hash, "sha256": digest, "bytes": final.stat().st_size,
                    "width": measured["width"], "height": measured["height"], "duration": measured["duration"],
                    "query": query, **requirements, "matched_subjects": candidate["matched_subjects"],
                    "palette_score": measured["palette_score"], "crop_fraction": measured["crop_fraction"],
                    "author": candidate["author"], "license": license_name, "license_url": license_url,
                    "validation": "metadata_and_decoded_frames"}
                validate_selection(selection, **owner)
                record = {"path": str(final), "source_url": candidate["source_url"],
                    "credit": f"Video by {candidate['author']} on {provider.title()}", "in_point": 0,
                    "provider": provider, "provider_id": candidate["provider_id"], "author": candidate["author"],
                    "license": license_name, "license_url": license_url, "sha256": digest,
                    "duration": measured["duration"], "width": measured["width"], "height": measured["height"],
                    "selection": selection}
                score = (candidate["semantic_score"], candidate["theme_score"],
                         measured["palette_score"] or 0, measured["crop_fraction"])
                if best is None or score > best[0]:
                    best = (score, record)
                # With no color preference, the metadata ranking already chose
                # the most relevant usable clip. Do not download extras for luck.
                if not requirements["palette"]:
                    break
            except InterruptedError:
                raise
            except StockMediaError as exc:
                last_reason = exc.code
                rejected[last_reason] = rejected.get(last_reason, 0) + 1
                if exc.code in {"ffprobe_unavailable", "ffmpeg_unavailable"}:
                    break
            except (OSError, ValueError, TypeError):
                last_reason = "media_validation_failed"
                rejected[last_reason] = rejected.get(last_reason, 0) + 1
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        if best is not None or downloads >= 3 or time.monotonic() >= deadline:
            break
    check_stop(stop_event)
    if best is None:
        _set_status(False, last_reason, provider, rejected=sum(rejected.values()), rejection_reasons=rejected,
                    candidates_downloaded=downloads)
        return None
    record = best[1]
    try:
        _atomic_json(receipt_path, record)
        if owner["script_signature"] is None:
            _credits(root, record)
    except OSError:
        _set_status(False, "receipt_write_failed", record["provider"])
        return None
    check_stop(stop_event)
    _set_status(True, "downloaded", record["provider"], rejected=sum(rejected.values()),
                rejection_reasons=rejected, candidates_downloaded=downloads,
                matched_subjects=record["selection"]["matched_subjects"])
    return record
