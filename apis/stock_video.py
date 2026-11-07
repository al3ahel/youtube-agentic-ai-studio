"""Bounded Pexels video search/download with attribution and motion fallback.

No key means unavailable, not an attempted network request. Only HTTPS MP4
files hosted on Pexels' video CDN are downloaded, and every redirect is checked.
The API key is sent only to the fixed search endpoint, never to media URLs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

import config
from apis.http import USER_AGENT

SEARCH_URL = "https://api.pexels.com/v1/videos/search"
VIDEO_HOSTS = frozenset({"videos.pexels.com"})
SOURCE_HOSTS = frozenset({"www.pexels.com", "pexels.com"})
MAX_BYTES = 64 * 1024 * 1024
MAX_DOWNLOAD_SECONDS = 60
_credit_lock = threading.Lock()
_status: ContextVar = ContextVar("pexels_video_status", default={
    "available": False, "reason": "not_requested", "provider": "pexels",
})


def _key() -> str:
    key = str(getattr(config, "PEXELS_API_KEY", "") or
              os.getenv("PEXELS_API_KEY", "")).strip()
    return "" if key.upper().startswith("YOUR_") else key


def _set_status(available: bool, reason: str) -> None:
    _status.set({"available": available, "reason": reason, "provider": "pexels"})


def availability_status() -> dict:
    """Report whether optional footage can be requested, without any network IO."""
    return {"available": bool(_key()), "reason": "ready" if _key() else "missing_key",
            "provider": "pexels"}


def get_status() -> dict:
    """Return the last fetch outcome in this request/thread."""
    return copy.deepcopy(_status.get())


def _number(value, default: float = 0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) and not isinstance(value, bool) else default
    except (TypeError, ValueError):
        return default


def _safe_url(value: object, hosts: frozenset[str]) -> bool:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return False
    try:
        parts = urlsplit(value)
        return (
            parts.scheme == "https" and parts.hostname in hosts
            and parts.port in {None, 443}
            and parts.username is None and parts.password is None
            and not parts.fragment
        )
    except (TypeError, ValueError):
        return False


def _source_url(video: dict) -> str:
    url = video.get("url")
    if _safe_url(url, SOURCE_HOSTS) and urlsplit(url).path.startswith("/video/"):
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return f"https://www.pexels.com/video/{int(video['id'])}/"


def _orientation_matches(width: int, height: int, orientation: str) -> bool:
    ratio = width / height if height else 0
    return ((orientation == "landscape" and ratio >= 1.2)
            or (orientation == "portrait" and 0 < ratio <= 0.84)
            or (orientation == "square" and 0.84 < ratio < 1.2))


def _candidates(payload: object, orientation: str, min_duration: float) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("videos"), list):
        return []
    candidates = []
    target_ratio = {"landscape": 16 / 9, "portrait": 9 / 16, "square": 1}[orientation]
    for video in payload["videos"][:20]:
        if not isinstance(video, dict) or not str(video.get("id", "")).isdigit():
            continue
        duration = _number(video.get("duration"))
        if duration < min_duration or not 0 < duration <= 180:
            continue
        files = video.get("video_files")
        if not isinstance(files, list):
            continue
        for asset in files[:30]:
            if not isinstance(asset, dict) or not str(asset.get("id", "")).isdigit():
                continue
            if str(asset.get("file_type", "")).lower() != "video/mp4" or not _safe_url(asset.get("link"), VIDEO_HOSTS):
                continue
            width, height = int(_number(asset.get("width"))), int(_number(asset.get("height")))
            if min(width, height) < 360 or max(width, height) > 2560:
                continue
            if not _orientation_matches(width, height, orientation):
                continue
            # Prefer the requested shape, then a 1080p-sized file over 4K downloads.
            score = (abs(width / height - target_ratio), abs(max(width, height) - 1920))
            candidates.append({"video": video, "asset": asset, "width": width,
                               "height": height, "duration": duration, "score": score})
    candidates.sort(key=lambda item: item["score"])
    return candidates


def _valid_mp4(path: Path) -> bool:
    try:
        if not 16 <= path.stat().st_size <= MAX_BYTES:
            return False
        with path.open("rb") as handle:
            return b"ftyp" in handle.read(64)
    except OSError:
        return False


def _download(url: str, path: Path) -> bool:
    if not _safe_url(url, VIDEO_HOSTS):
        return False
    if path.is_file() and _valid_mp4(path):
        return True
    started = time.monotonic()
    response = None
    temporary = None
    try:
        for redirects in range(4):
            if not _safe_url(url, VIDEO_HOSTS):
                return False
            response = requests.get(
                url, stream=True, headers={"User-Agent": USER_AGENT},
                timeout=(5, 15), allow_redirects=False,
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location", "")
                response.close()
                response = None
                if redirects == 3 or not location:
                    return False
                url = urljoin(url, location)
                continue
            break
        if response is None or response.status_code != 200:
            return False
        media_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if media_type not in {"video/mp4", "application/mp4", "application/octet-stream"}:
            return False
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                if not 0 < int(content_length) <= MAX_BYTES:
                    return False
            except (TypeError, ValueError):
                return False
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pexels-", suffix=".part",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            size = 0
            for chunk in response.iter_content(chunk_size=65536):
                if time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
                    return False
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_BYTES:
                    return False
                handle.write(chunk)
        if not _valid_mp4(temporary):
            return False
        temporary.replace(path)
        temporary = None
        return True
    except (requests.RequestException, OSError, ValueError):
        return False
    finally:
        if response is not None:
            response.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _credits(output_dir: Path, record: dict) -> None:
    path = output_dir / "video_credits.json"
    with _credit_lock:
        records = []
        if path.exists() and path.stat().st_size < 2 * 1024 * 1024:
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(previous, list):
                    records = [r for r in previous if isinstance(r, dict)][:200]
            except (ValueError, OSError):
                pass
        records = [r for r in records if r.get("path") != record["path"]]
        records.append(record)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=output_dir, prefix=".video-credits-",
                                             suffix=".json", mode="w", encoding="utf-8",
                                             delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(records, handle, indent=2, ensure_ascii=False)
            temporary.replace(path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def fetch_video(query: str, output_dir, orientation: str = "landscape",
                min_duration: float = 0) -> dict | None:
    """Get one suitable Pexels clip, or None with an explicit get_status result.

    At most one search and three bounded candidate downloads are attempted.
    Source/author attribution is saved alongside the output, without signed
    CDN URLs or credentials. A caller receiving None should use motion graphics.
    """
    key = _key()
    if not key:
        _set_status(False, "missing_key")
        return None
    from apis.openrouter import _redact
    query = re.sub(r"\s+", " ", _redact(str(query or ""))).strip()[:200]
    if not query or orientation not in {"landscape", "portrait", "square"}:
        _set_status(False, "invalid_query")
        return None
    minimum = _number(min_duration, -1)
    if not 0 <= minimum <= 180:
        _set_status(False, "invalid_duration")
        return None
    response = None
    try:
        response = requests.get(
            SEARCH_URL,
            params={"query": query, "orientation": orientation, "size": "medium",
                    "per_page": 12, "page": 1},
            headers={"Authorization": key, "User-Agent": USER_AGENT},
            timeout=(5, 15), allow_redirects=False,
        )
        if response.status_code == 429:
            _set_status(False, "rate_limited")
            return None
        if response.status_code in {401, 403}:
            _set_status(False, "access_denied")
            return None
        if response.status_code != 200:
            _set_status(False, "provider_unavailable")
            return None
        candidates = _candidates(response.json(), orientation, minimum)
    except (requests.RequestException, ValueError, TypeError):
        _set_status(False, "provider_unavailable")
        return None
    finally:
        if response is not None:
            response.close()
    if not candidates:
        _set_status(False, "no_matching_footage")
        return None
    try:
        destination = Path(output_dir).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        asset_directory = (destination / "assets" / "videos").resolve()
        if destination not in asset_directory.parents:
            _set_status(False, "invalid_output_directory")
            return None
        for candidate in candidates[:3]:
            video, asset = candidate["video"], candidate["asset"]
            path = asset_directory / f"pexels-{int(video['id'])}-{int(asset['id'])}.mp4"
            if path.is_symlink():
                continue
            if not _download(asset["link"], path):
                continue
            user = video.get("user") if isinstance(video.get("user"), dict) else {}
            author = _redact(re.sub(r"<[^>]*>", "", str(user.get("name", "Pexels contributor"))))[:100]
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else _hash_file(handle)
            record = {
                "path": str(path), "source_url": _source_url(video),
                "credit": f"Video by {author} on Pexels",
                "provider": "pexels", "in_point": 0,
                "duration": candidate["duration"],
                "width": candidate["width"], "height": candidate["height"],
                "license_url": "https://www.pexels.com/license/", "sha256": digest,
            }
            _credits(destination, record)
            _set_status(True, "downloaded")
            return record
    except (OSError, ValueError, TypeError):
        _set_status(False, "download_failed")
        return None
    _set_status(False, "download_failed")
    return None


def _hash_file(handle) -> str:
    """Compatibility with Python 3.10, which has no hashlib.file_digest."""
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(65536), b""):
        digest.update(chunk)
    return digest.hexdigest()
