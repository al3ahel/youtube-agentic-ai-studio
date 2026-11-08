"""Automatic, keyless audio from Wikimedia Commons' documented public API.

Each selected file must carry a supported free license. Search metadata is
untrusted: URLs never come from the language model, downloads stay on Wikimedia's
media host, redirects/DNS/bytes/duration are checked, and source credits survive
in a channel-owned cache. Pixabay scraping is deliberately not implemented.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import queue
import re
import socket
import tempfile
import threading
import time
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit

import config
from studio.state import job_lock

SEARCH_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "YAAS/1.0 (https://github.com/al3ahel/youtube-agentic-ai-studio)"
MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_OPERATION_SECONDS = 45
MAX_CANDIDATES = 8
MAX_DOWNLOADS = 3
MAX_SEARCHES = 3
MAX_CACHE_ENTRIES = 128
_MUSIC_SELECTION_VERSION = 1
_CATALOG_SELECTION_VERSION = 1
_BUNDLED_ROOT = Path(__file__).resolve().parents[1] / "assets" / "audio" / "bundled"
_SEARCH_FILLER = frozenset("a an the of for gentle quiet soft subtle calm background audio recording recordings sound sounds effect effects".split())
_MUSIC_EVIDENCE = re.compile(
    r"\binstrumentals?\b|\b(?:ambient|electronic|orchestral|chamber|classical|piano|guitar|jazz)\s+music\b|"
    r"\b(?:symphon(?:y|ies)|sonatas?|concertos?|nocturnes?|preludes?|[ée]tudes?)\b|"
    r"\b(?:piano|guitar|violin|cello|flute|saxophone|harp)\s+(?:solo|duet|piece|improvisation|performance)\b|"
    r"\b(?:solo|duet)\s+(?:piano|guitar|violin|cello|flute|saxophone|harp)\b")
_MUSIC_EXCLUSIONS = re.compile(
    r"\b(?:speech|speeches|spoken|narration|dialogue|interviews?|lectures?|podcasts?|audiobooks?|"
    r"voices?|voiceover|vocals?|vocalists?|vocali[sz](?:ation|ations|ing)|singers?|sing|sings|singing|sung|choirs?|choral|chorus|a\s+cappella|acappella|"
    r"chants?|chanting|lyrics?|rap|rapping|opera|operatic|hymns?|anthems?)\b|"
    r"\b(?:non|not)\s+(?:purely\s+)?instrumental\b|"
    r"\b(?:field\s+recordings?|sound\s+effects?|environmental\s+recordings?|nature\s+sounds?)\b")
_MEDIA_TYPES = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/ogg": ".ogg",
                "application/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav",
                "audio/flac": ".flac", "audio/x-flac": ".flac", "audio/mp4": ".m4a"}
_ID = re.compile(r"[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_PROFILE = re.compile(r"(?:default|[a-f0-9]{32})\Z")
_cache_lock = threading.RLock()
_status = ContextVar("automatic_audio_status", default={"available": False, "provider": "wikimedia",
                                                       "reason": "not_requested", "message": ""})


class AudioSourceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _set_status(available, reason, message, *, provider="wikimedia"):
    _status.set({"available": available, "provider": provider, "reason": reason, "message": message})


def get_status():
    """Safe diagnostic for this worker/request, never another channel's fetch."""
    return dict(_status.get())


def _guard(stop_event, deadline):
    if stop_event is not None and stop_event.is_set():
        raise AudioSourceError("cancelled", "Automatic audio selection was stopped.")
    if time.monotonic() >= deadline:
        raise AudioSourceError("timeout", "The free audio catalogue took too long; the added audio layer was omitted.")


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "br"}:
            self.parts.append(" ")
    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def _plain(value, limit=1000):
    if not isinstance(value, str) or len(value) > 20000:
        return ""
    parser = _PlainText()
    try:
        parser.feed(value)
        text = " ".join("".join(parser.parts).split())
        if len(text) > limit or any(ord(c) < 32 or ord(c) == 127 for c in text):
            return ""
        text.encode("utf-8")
        return text
    except (ValueError, UnicodeError):
        return ""


def _metadata_text(metadata, key, limit=1000):
    value = metadata.get(key)
    return _plain(value.get("value"), limit) if isinstance(value, dict) else ""


def _safe_url(value, host, prefix):
    if (not isinstance(value, str) or not value or len(value) > 4096
            or "\\" in value or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and parts.hostname == host and parts.port in {None, 443}
                and parts.username is None and parts.password is None and not parts.fragment
                and parts.path.startswith(prefix)
                and not any(part in {".", ".."} for part in unquote(parts.path).split("/")))
    except ValueError:
        return False


def _license(metadata, source_url):
    """Whitelist exact licenses; never infer permission from a filename or tag."""
    value = _metadata_text(metadata, "LicenseUrl", 1000)
    labels = (_metadata_text(metadata, "License") + " " + _metadata_text(metadata, "LicenseShortName")).lower()
    if re.search(r"(?:^|[\s-])(?:nc|nd|sa)(?:$|[\s-])", labels):
        return None
    if value:
        try:
            parts = urlsplit(value)
            if (parts.scheme not in {"http", "https"} or parts.hostname not in {"creativecommons.org", "www.creativecommons.org"}
                    or parts.port not in {None, 80, 443} or parts.username is not None or parts.password is not None or parts.query or parts.fragment):
                return None
            path = re.sub(r"/(?:deed(?:\.[A-Za-z_-]+)?|legalcode(?:\.[A-Za-z_-]+)?)/?$", "", parts.path).rstrip("/")
            if path == "/publicdomain/zero/1.0":
                return "CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/"
            if path == "/publicdomain/mark/1.0":
                return "Public domain", "https://creativecommons.org/publicdomain/mark/1.0/"
            match = re.fullmatch(r"/licenses/by/(1\.0|2\.0|2\.5|3\.0|4\.0)", path)
            if match:
                return "CC BY " + match.group(1), f"https://creativecommons.org/licenses/by/{match.group(1)}/"
        except ValueError:
            pass
        return None
    # Commons public-domain declarations often have no external LicenseUrl.
    # Preserve the actual file's declaration URL instead of inventing a CC license.
    if (_metadata_text(metadata, "LicenseShortName").lower() == "public domain"
            and _metadata_text(metadata, "Copyrighted").lower() == "false"
            and _metadata_text(metadata, "License").lower() in {"pd", "pd-author", "pd-self"}):
        return "Public domain", source_url
    return None


def _query(value):
    if not isinstance(value, str) or len(value) > 500:
        raise AudioSourceError("invalid_query", "Provide a short audio search description.")
    # Plain words cannot inject CirrusSearch operators that broaden the request.
    words = re.findall(r"[^\W_]+", value, flags=re.UNICODE)
    return " ".join(words[:12])[:100]


def _broader_query(terms, kind):
    """One shorter subject query, never a generic substitute or new subject."""
    filler = _SEARCH_FILLER | ({"ambient", "ambience"} if kind == "sfx" else set())
    kept = [word for word in terms.split() if word.casefold() not in filler]
    broader = " ".join(kept)
    generic = {"music", "track", "tracks", "piece", "pieces", "noise"}
    if not broader or broader.casefold() == terms.casefold() or all(word.casefold() in generic for word in kept):
        return None
    return broader


def _search_queries(value, kind):
    """Turn artistic directions into bounded literal searches of the same sound.

    Commons search treats ordinary words as required terms. Usage instructions
    and absence declarations therefore cannot be searched as positive words:
    ``without vocals, suitable for narration`` would demand vocals/narration.
    Instrumental eligibility is still checked against each file's metadata.
    """
    _query(value)  # Validate before stripping any text or search punctuation.
    text = value.casefold().replace("_", " ")
    text = re.sub(r"\b(?:suitable|appropriate|ideal|intended|designed)\s+for\b.*", " ", text)
    text = re.sub(r"\b(?:for|under|behind|beneath)\s+(?:(?:a|the|documentary|video|spoken|clear)\s+)*"
                  r"(?:narration|voice\s*over|speech|dialogue|documentary|video)\b.*", " ", text)
    if kind == "sfx":
        text = re.sub(r"\bfor\s+(?:(?:a|the|scene|chapter|visual|gentle|soft)\s+)*"
                      r"(?:transitions?|cuts?)\b.*", " ", text)
        text = re.sub(r"\bpages\b", "page", text)
        text = re.sub(r"\b(?:swoosh|whooshes)\b", "whoosh", text)
    if kind == "music":
        voice = r"(?:vocals?|voices?|singing|speech|narration|lyrics?)"
        text = re.sub(r"\b(?:no|without)\s+(?:any\s+)?" + voice
                      + r"(?:\s*(?:,|and|or)\s*(?:any\s+)?" + voice + r")*\b", " ", text)
        text = re.sub(r"\b(?:vocal|voice|speech|lyric)[ -]free\b", " ", text)
        text = re.sub(r"\b(?:low|slow|moderate)[\s-]+tempo\b", " ", text)
    text = re.sub(r"\b(?:royalty[ -]free|copyright[ -]free|creative commons|cc0)\b", " ", text)
    words = _query(text)
    filler = _SEARCH_FILLER | ({"ambient", "ambience", "transition", "transitions", "accent"} if kind == "sfx" else set())
    terms = " ".join(word for word in words.split() if word not in filler)
    # With no acoustic subject, don't silently search for an unrelated noise.
    if not terms or (kind == "sfx" and set(terms.split()) <= {"noise", "track", "tracks", "clip", "clips"}):
        raise AudioSourceError("invalid_query", "Describe the actual instrument, music style or sound to find.")
    result = [terms]
    if kind == "music":
        # Instrumental is independently enforced; removing that redundant word
        # retains the requested genre/instrument and makes catalog titles usable.
        if "instrumental" in terms.split() and re.search(
                r"\b(?:ambient|electronic|orchestral|chamber|classical|piano|guitar|jazz|harp|cello|violin|flute)\b", terms):
            variant = " ".join(word for word in terms.split() if word != "instrumental")
            if "music" not in variant.split():
                variant += " music"
            result.append(variant)
    else:
        if re.search(r"\b(?:paper|page)\s+(?:turn|turning|flip|flipping)\b", terms):
            for replacement in ("page turn", "paper rustle"):
                result.append(re.sub(r"\b(?:paper|page)\s+(?:turn|turning|flip|flipping)\b", replacement, terms))
        elif re.search(r"\bwhoosh(?:ing)?\b", terms):
            result.append(re.sub(r"\bwhoosh(?:ing)?\b", "swish", terms))
        elif re.search(r"\bswish(?:ing)?\b", terms):
            result.append(re.sub(r"\bswish(?:ing)?\b", "whoosh", terms))
    return list(dict.fromkeys(result))[:MAX_SEARCHES]


def _bundled_candidate(query, kind, minimum, maximum, mood=""):
    """Choose only an exact compatible, pinned recording in our licensed set.

    This is trusted, shipped content, not a downloader for arbitrary model URLs.
    Unknown genre/instrument/material constraints stay with the live search.
    """
    terms = set(_search_queries(query, kind)[0].split())
    mood_terms = set(_query(mood).casefold().split())
    quiet_words = {"subtle", "calm", "quiet", "gentle", "soft"}
    raw = query.casefold()
    if re.search(r"\b(?:no|not|without|avoid|exclude)\b", raw):
        # Only the enforced no-vocals condition is safe to remove here. Other
        # negations must never accidentally select the very sound being excluded.
        voice = r"(?:vocals?|voices?|singing|speech|narration|lyrics?)"
        raw = re.sub(r"\b(?:no|without)\s+(?:any\s+)?" + voice
                     + r"(?:\s*(?:,|and|or)\s*(?:any\s+)?" + voice + r")*\b", " ", raw)
        if re.search(r"\b(?:no|not|without|avoid|exclude)\b", raw):
            return None
    family = None
    if kind == "music" and terms & {"music", "instrumental", "ambient", "piano", "guitar", "harp"}:
        family = "ambient_music"
    elif kind == "sfx":
        paper = {"paper", "page", "book", "turn", "turning", "turns", "flip", "flipping", "rustle", "rustling"}
        if terms & {"paper", "page", "book"} and terms & {"turn", "turning", "turns", "flip", "flipping", "rustle", "rustling"} and terms <= paper:
            family = "paper_turn"
        elif terms & {"whoosh", "whooshing", "swish", "swishing"} and terms <= {"whoosh", "whooshing", "swish", "swishing", "short", "small", "brief"}:
            family = "whoosh"
    if family is None:
        return None
    if kind == "sfx" and not mood_terms <= quiet_words:
        return None
    try:
        root = _BUNDLED_ROOT.resolve()
        manifest = root / "catalog.json"
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 131072:
            return None
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assets = data.get("assets")
        if data.get("version") != 1 or not isinstance(assets, list) or len(assets) > 32:
            return None
        for asset in assets:
            if not isinstance(asset, dict) or asset.get("family") != family or asset.get("kind") != kind:
                continue
            if family == "ambient_music":
                tags = asset.get("tags", [])
                if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                    continue
                described = set(" ".join(tags).casefold().split())
                aliases = {"relaxing": "calm", "relaxation": "calm", "calming": "calm", "soothing": "calm", "meditative": "calm",
                           "minimalist": "minimal", "mellow": "calm"}
                constraints = {aliases.get(term, term) for term in terms - {"music", "instrumental", "bed", "track", "piece", "loop"}}
                constraints.update(aliases.get(term, term) for term in mood_terms - quiet_words)
                # Tempo is unnecessary in an AND-style catalog search, but is
                # still an explicit musical requirement for a shipped choice.
                tempo = re.search(r"\b(low|slow|moderate)[\s-]+tempo\b", raw)
                if tempo:
                    constraints.add("slow" if tempo.group(1) in {"low", "slow"} else "moderate")
                if "calm" in mood_terms or set(_query(query).casefold().split()) & (quiet_words - {"subtle"}):
                    constraints.add("calm")
                if not constraints <= described:
                    continue
            filename, digest = asset.get("filename"), asset.get("sha256")
            if not isinstance(filename, str) or not isinstance(digest, str) or not _HASH.fullmatch(digest):
                continue
            media = root / filename
            suffix = media.suffix.lower()
            if (suffix not in set(_MEDIA_TYPES.values()) or media.resolve() != media or not media.is_file()
                    or not media.is_relative_to(root) or media == root):
                continue
            size, duration = asset.get("bytes"), asset.get("duration")
            if (type(size) is not int or not 0 < size <= MAX_AUDIO_BYTES or media.stat().st_size != size
                    or isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration)
                    or not 0 < duration <= maximum or duration < minimum):
                continue
            source = asset.get("source_url")
            source_parts = urlsplit(source) if isinstance(source, str) else None
            if not source_parts or source_parts.hostname not in {"github.com", "kenney.nl", "chriszabriskie.com", "opengameart.org"}:
                continue
            if not _safe_url(source, source_parts.hostname, "/"):
                continue
            allowed = _license({"LicenseUrl": {"value": asset.get("license_url", "")}}, source)
            if allowed != (asset.get("license"), asset.get("license_url")):
                continue
            if any(not _plain(asset.get(key), 10000 if key == "attribution" else 500) for key in ("title", "creator", "attribution", "provider_id")):
                continue
            if hashlib.sha256(media.read_bytes()).hexdigest() != digest:
                continue
            return {key: asset.get(key, "") for key in ("provider_id", "kind", "title", "creator", "source_url",
                    "license", "license_url", "attribution", "changes")} | {
                "provider": "bundled", "selection_source": "bundled_catalog", "content_id": "unknown",
                "license_note": f"{asset['license']}; {asset['license_url']}", "local_path": str(media),
                "expected_bytes": size, "expected_sha256": digest, "extension": suffix}
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
        return None
    return None


def _is_instrumental_candidate(context):
    """Require positive music evidence; metadata cannot certify what we hear."""
    text = re.sub(r"[_-]+", " ", context.casefold())
    # Literal absence declarations are useful evidence, but do not erase other
    # warnings: 'no vocals, with a spoken introduction' still fails below.
    voice = r"(?:vocals?|voices?|singing|speech|narration|lyrics?)"
    text = re.sub(r"\b(?:no|without)\s+(?:any\s+)?" + voice
                  + r"(?:\s*(?:,|and|or)\s*(?:any\s+)?" + voice + r")*\b", " ", text)
    text = re.sub(r"\b(?:vocal|voice|speech|lyric)\s+free\b", " ", text)
    return bool(_MUSIC_EVIDENCE.search(text)) and not _MUSIC_EXCLUSIONS.search(text)


def _candidates(payload, kind):
    query = payload.get("query") if isinstance(payload, dict) else None
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, (dict, list)):
        return []
    rows = list(pages.values()) if isinstance(pages, dict) else pages
    rows = sorted((row for row in rows[:MAX_CANDIDATES] if isinstance(row, dict)),
                  key=lambda row: row.get("index") if type(row.get("index")) is int else MAX_CANDIDATES)
    result = []
    for page in rows:
        page_id, title = page.get("pageid"), page.get("title")
        infos = page.get("imageinfo")
        if type(page_id) is not int or page_id <= 0 or not isinstance(title, str) or not title.startswith("File:") or len(title) > 500:
            continue
        if not isinstance(infos, list) or not infos or not isinstance(infos[0], dict):
            continue
        info = infos[0]
        mime, size = info.get("mime"), info.get("size")
        if (mime not in _MEDIA_TYPES or info.get("mediatype") != "AUDIO" or type(size) is not int
                or not 0 < size <= MAX_AUDIO_BYTES or not _safe_url(info.get("url"), "upload.wikimedia.org", "/wikipedia/commons/")):
            continue
        metadata = info.get("extmetadata")
        if not isinstance(metadata, dict):
            continue
        # Commons audio searches also return dictionary pronunciation clips.
        # They are not suitable music/effects even when their license is CC0.
        context = " ".join((title, _metadata_text(metadata, "ObjectName", 200),
                            _metadata_text(metadata, "ImageDescription", 10000),
                            _metadata_text(metadata, "Categories", 10000)))
        if re.search(r"pronunciation|spoken[ _-]wikipedia|wiktionary|lingua[ _-]libre|\bLL-Q\d", context, re.I):
            continue
        if kind == "music" and not _is_instrumental_candidate(context):
            continue
        source_url = "https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_"), safe=":()_,.-")
        allowed = _license(metadata, source_url)
        if allowed is None:
            continue
        license_name, license_url = allowed
        creator = _metadata_text(metadata, "Artist", 500)
        if license_name.startswith("CC BY") and not creator:
            continue
        name = _metadata_text(metadata, "ObjectName", 200) or _plain(title[5:], 500)
        if not name:
            continue
        attribution = f"{name}" + (f" — {creator}" if creator else "") + f". {license_name} ({license_url}). Source: {source_url}"
        extra = _metadata_text(metadata, "Attribution", 1500)
        if extra:
            attribution += ". " + extra
        result.append({"provider": "wikimedia", "provider_id": str(page_id), "kind": kind, "title": name,
                       "creator": creator, "source_url": source_url, "license": license_name, "license_url": license_url,
                       "attribution": attribution, "license_note": f"{license_name}; {license_url}",
                       "content_id": "unknown", "download_url": info["url"], "extension": _MEDIA_TYPES[mime],
                       "expected_bytes": size, "mime": mime})
    return result


def _public_address(host, stop_event, deadline):
    """Bound DNS resolution and pin a public address before TLS/application I/O."""
    resolved = queue.Queue(maxsize=1)
    def lookup():
        try:
            resolved.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError:
            resolved.put(None)
    threading.Thread(target=lookup, daemon=True, name="audio-catalogue-dns").start()
    dns_deadline = min(deadline, time.monotonic() + 3)
    while True:
        _guard(stop_event, dns_deadline)
        try:
            addresses = resolved.get(timeout=.05)
            break
        except queue.Empty:
            continue
    if not addresses:
        raise AudioSourceError("network_unavailable", "The free audio catalogue is unreachable; the added audio layer was omitted.")
    ips = [row[4][0] for row in addresses]
    if not ips or any(not ipaddress.ip_address(address).is_global for address in ips):
        raise AudioSourceError("unsafe_address", "The free audio host resolved to a non-public address; the download was skipped.")
    # Prefer IPv4 when available; Windows installations may have DNS AAAA
    # records without a usable IPv6 route. The address remains pinned below.
    return sorted(ips, key=lambda address: ipaddress.ip_address(address).version != 4)[0]


@contextmanager
def _response(url, *, host, prefix, stop_event, deadline):
    if not _safe_url(url, host, prefix):
        raise AudioSourceError("unsafe_url", "The audio catalogue returned an unsupported media address; the download was skipped.")
    _guard(stop_event, deadline)
    address = _public_address(host, stop_event, deadline)
    connection = http.client.HTTPSConnection(host, 443, timeout=min(5, max(.05, deadline - time.monotonic())))
    # HTTPSConnection still verifies the Wikimedia hostname and uses it as SNI.
    # Only the resolved socket address is pinned, preventing a second DNS lookup.
    connection._create_connection = lambda _target, timeout, source_address=None: socket.create_connection((address, 443), timeout, source_address)
    finished = threading.Event()
    live_socket = [None]
    response = None
    def monitor():
        while not finished.wait(.05):
            if (stop_event is not None and stop_event.is_set()) or time.monotonic() >= deadline:
                if live_socket[0] is not None:
                    try:
                        live_socket[0].shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                return
    threading.Thread(target=monitor, daemon=True, name="audio-download-deadline").start()
    try:
        connection.connect()
        live_socket[0] = connection.sock
        _guard(stop_event, deadline)
        connection.sock.settimeout(max(.05, min(12, deadline - time.monotonic())))
        parts = urlsplit(url)
        target = parts.path + ("?" + parts.query if parts.query else "")
        connection.request("GET", target, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
        response = connection.getresponse()
        _guard(stop_event, deadline)
        yield response
        _guard(stop_event, deadline)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        if isinstance(exc, AudioSourceError):
            raise
        _guard(stop_event, deadline)
        raise AudioSourceError("network_unavailable", "The free audio catalogue is unavailable; the added audio layer was omitted.") from None
    finally:
        finished.set()
        if response is not None:
            response.close()
        connection.close()


def _read(response, limit, stop_event, deadline, target=None):
    length = response.getheader("Content-Length")
    try:
        if length is not None and not 0 < int(length) <= limit:
            raise ValueError()
    except ValueError:
        raise AudioSourceError("file_too_large", "An audio response exceeded the size limit and was skipped.") from None
    size = 0
    output = bytearray() if target is None else None
    while True:
        _guard(stop_event, deadline)
        block = response.read(min(65536, limit + 1 - size))
        if not block:
            break
        size += len(block)
        if size > limit:
            raise AudioSourceError("file_too_large", "An audio response exceeded the size limit and was skipped.")
        if target is None:
            output.extend(block)
        else:
            target.write(block)
    _guard(stop_event, deadline)
    if length is not None and size != int(length):
        raise AudioSourceError("incomplete_download", "An audio download was incomplete and was skipped.")
    return bytes(output) if target is None else size


def _search(query, kind, stop_event, deadline):
    params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": f"{query} filetype:audio",
              "gsrnamespace": 6, "gsrlimit": MAX_CANDIDATES, "prop": "imageinfo", "maxlag": 5,
              "iiprop": "url|size|mime|mediatype|extmetadata", "iiextmetadatalanguage": "en",
              "iiextmetadatafilter": "Artist|ObjectName|License|LicenseShortName|LicenseUrl|Copyrighted|Attribution|ImageDescription|Categories"}
    with _response(SEARCH_URL + "?" + urlencode(params), host="commons.wikimedia.org", prefix="/w/api.php",
                   stop_event=stop_event, deadline=deadline) as response:
        if response.status != 200:
            reason = "rate_limited" if response.status == 429 else "catalogue_unavailable"
            raise AudioSourceError(reason, "The free audio catalogue is temporarily unavailable; the added audio layer was omitted.")
        try:
            data = json.loads(_read(response, MAX_METADATA_BYTES, stop_event, deadline))
        except (ValueError, UnicodeError):
            raise AudioSourceError("invalid_catalogue", "The free audio catalogue returned unreadable metadata; the added audio layer was omitted.") from None
    if not isinstance(data, dict) or data.get("error"):
        raise AudioSourceError("catalogue_unavailable", "The free audio catalogue is busy; the added audio layer was omitted.")
    return _candidates(data, kind)


def _download(candidate, path, stop_event, deadline):
    url = candidate["download_url"]
    for redirect in range(3):
        with _response(url, host="upload.wikimedia.org", prefix="/wikipedia/commons/",
                       stop_event=stop_event, deadline=deadline) as response:
            if response.status in {301, 302, 303, 307, 308}:
                destination = response.getheader("Location")
                if redirect == 2 or not destination:
                    raise AudioSourceError("redirect_rejected", "An audio download redirected too many times and was skipped.")
                url = urljoin(url, destination)
                continue
            if response.status != 200:
                raise AudioSourceError("download_unavailable", "A matching free audio file could not be downloaded.")
            mime = str(response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            if mime not in {*_MEDIA_TYPES, "application/octet-stream"}:
                raise AudioSourceError("invalid_media", "A catalogue item did not return an audio file and was skipped.")
            with path.open("wb") as handle:
                size = _read(response, MAX_AUDIO_BYTES, stop_event, deadline, target=handle)
            if not size or size != candidate["expected_bytes"]:
                raise AudioSourceError("incomplete_download", "An audio download did not match its catalogue size and was skipped.")
            return


def _profile_root(profile_id, kind, create=False):
    from studio.channels import get_profile
    if not isinstance(profile_id, str) or not _PROFILE.fullmatch(profile_id):
        raise AudioSourceError("profile_unavailable", "Choose an existing channel profile before selecting automatic audio.")
    try:
        get_profile(profile_id)
    except (ValueError, LookupError):
        raise AudioSourceError("profile_unavailable", "The channel profile for automatic audio is no longer available.") from None
    root = Path(config.DATA_DIR).expanduser().resolve() / "audio-auto" / profile_id / kind
    if root.resolve() != root or root.exists() and not root.is_dir():
        raise AudioSourceError("cache_unavailable", "This channel's automatic audio cache is unavailable.")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _cached(root, key, profile_id, kind, minimum, maximum, bundled=None):
    path = root / (key + ".json")
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 32768:
            return None
        saved = json.loads(path.read_text(encoding="utf-8"))
        row = saved.get("asset")
        if saved.get("version") != 1 or saved.get("profile_id") != profile_id or not isinstance(row, dict):
            return None
        if kind == "music" and (type(saved.get("music_selection_version")) is not int
                                or saved["music_selection_version"] != _MUSIC_SELECTION_VERSION):
            # Earlier caches recorded licensing but did not establish musical
            # suitability. Re-search rather than relabel a cached field sound.
            return None
        if (row.get("provider") not in {"wikimedia", "bundled"} or row.get("kind") != kind or not isinstance(row.get("id"), str)
                or not _ID.fullmatch(row["id"]) or not isinstance(row.get("sha256"), str) or not _HASH.fullmatch(row["sha256"])):
            return None
        suffix = Path(str(row.get("filename", ""))).suffix
        if suffix not in set(_MEDIA_TYPES.values()) or row["filename"] != row["id"] + suffix:
            return None
        duration, size = row.get("duration"), row.get("bytes")
        if (isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration)
                or not (0 < duration <= maximum and duration >= minimum) or type(size) is not int or not 0 < size <= MAX_AUDIO_BYTES):
            return None
        if row["provider"] == "bundled":
            if (bundled is None or row["sha256"] != bundled["expected_sha256"]
                    or any(row.get(field) != bundled.get(field) for field in
                           ("provider_id", "title", "creator", "source_url", "license", "license_url", "attribution", "changes", "selection_source"))):
                return None
        elif not _safe_url(row.get("source_url"), "commons.wikimedia.org", "/wiki/File:"):
            return None
        license_metadata = {"LicenseUrl": {"value": row.get("license_url", "")}}
        license_ok = _license(license_metadata, row["source_url"])
        declared_pd = row.get("license") == "Public domain" and row.get("license_url") == row["source_url"]
        if not declared_pd and license_ok != (row.get("license"), row.get("license_url")):
            return None
        if not isinstance(row.get("attribution"), str) or not row["attribution"]:
            return None
        media = root / row["filename"]
        if media.resolve() != media or not media.is_file() or media.stat().st_size != size:
            return None
        if hashlib.sha256(media.read_bytes()).hexdigest() != row["sha256"]:
            return None
        return {**row, "path": str(media)}
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
        return None


def _save_cache(root, key, profile_id, asset):
    fd, name = tempfile.mkstemp(prefix=".metadata-", suffix=".json", dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            saved = {"version": 1, "profile_id": profile_id, "asset": asset}
            if asset.get("kind") == "music":
                saved["music_selection_version"] = _MUSIC_SELECTION_VERSION
            json.dump(saved, handle, ensure_ascii=False, allow_nan=False)
        temporary.replace(root / (key + ".json"))
    finally:
        temporary.unlink(missing_ok=True)


def acquire_audio(query, *, kind, profile_id, mood="", min_duration=0, max_duration=None, stop_event=None):
    """Find/cache one licensed asset, or return None with get_status() diagnostics.

    Only plain search terms are accepted, never an arbitrary download URL. The
    caller omits an unavailable added audio layer; this adapter never synthesizes
    a replacement. Neither manual selections nor profile preferences change.
    """
    temporary = None
    deadline = time.monotonic() + MAX_OPERATION_SECONDS
    _set_status(False, "searching", "Searching Wikimedia Commons for matching free audio.")
    try:
        if kind not in {"music", "sfx"}:
            raise AudioSourceError("invalid_kind", "Choose music or sfx for automatic audio.")
        words, mood_words = _query(query), _query(mood)
        if not words:
            raise AudioSourceError("invalid_query", "Provide a short audio search description.")
        queries = _search_queries(query, kind)
        maximum = 7200 if max_duration is None else max_duration
        if (any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (min_duration, maximum))
                or not 0 <= min_duration < maximum <= 7200):
            raise AudioSourceError("invalid_duration", "Automatic audio durations must fall between zero and two hours.")
        minimum = min_duration
        _guard(stop_event, deadline)
        with job_lock, _cache_lock:
            root = _profile_root(profile_id, kind)
        bundled = _bundled_candidate(query, kind, minimum, maximum, mood_words.casefold())
        # A catalog revision or replaced recording cannot reuse an older choice.
        # Mood is an acoustic constraint/cache identity, never an extra AND word.
        identity = [_CATALOG_SELECTION_VERSION, query.casefold(), mood_words.casefold(), kind, minimum, maximum,
                    bundled.get("expected_sha256") if bundled else None]
        key = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        with job_lock, _cache_lock:
            root = _profile_root(profile_id, kind)
            cached = _cached(root, key, profile_id, kind, minimum, maximum, bundled)
            if cached is not None:
                _guard(stop_event, deadline)
                _set_status(True, "cached", "Using matching free audio already saved for this channel.", provider=cached["provider"])
                return cached

        def candidates():
            if bundled is not None:
                yield bundled
            # Try each distinct literal query's best file before spending the
            # rest of the shared download budget on lower-ranked alternatives.
            # A licensed result with bad bytes/duration must not suppress the
            # next useful spelling of the same requested sound.
            pending, seen = [], set()
            for terms in queries:
                _guard(stop_event, deadline)
                if remote_downloads >= MAX_DOWNLOADS:
                    break
                fresh = []
                found = _search(terms, kind, stop_event, deadline)
                _guard(stop_event, deadline)
                for candidate in found[:MAX_CANDIDATES]:
                    identity = (candidate["provider"], candidate["provider_id"], candidate.get("download_url"))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    fresh.append(candidate)
                if fresh:
                    yield fresh[0]
                    pending.extend(fresh[1:])
            yield from pending

        from studio.audio_library import _probe_audio
        last_error = None
        remote_downloads = 0
        for candidate in candidates():
            _guard(stop_event, deadline)
            if candidate["provider"] != "bundled":
                if remote_downloads >= MAX_DOWNLOADS:
                    break
                remote_downloads += 1
            with job_lock, _cache_lock:
                root = _profile_root(profile_id, kind, create=True)
                if len(list(root.glob("*.json"))) >= MAX_CACHE_ENTRIES and not (root / (key + ".json")).is_file():
                    raise AudioSourceError("cache_full", "This channel's automatic audio cache is full; the added audio layer was omitted.")
                fd, name = tempfile.mkstemp(prefix=".download-", suffix=candidate["extension"], dir=root)
                os.close(fd)
                temporary = Path(name)
            try:
                if candidate["provider"] == "bundled":
                    with Path(candidate["local_path"]).open("rb") as source, temporary.open("wb") as target:
                        while block := source.read(65536):
                            _guard(stop_event, deadline)
                            target.write(block)
                else:
                    _download(candidate, temporary, stop_event, deadline)
                _guard(stop_event, deadline)
                duration = _probe_audio(temporary)
                if not (0 < duration <= maximum and duration >= minimum):
                    raise AudioSourceError("duration_mismatch", "A matching audio file had an unsuitable duration and was skipped.")
                _guard(stop_event, deadline)
                digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
                if candidate["provider"] == "bundled" and (digest != candidate["expected_sha256"]
                        or temporary.stat().st_size != candidate["expected_bytes"]):
                    raise AudioSourceError("invalid_bundled_audio", "A licensed collection recording could not be verified and was skipped.")
                identifier = hashlib.sha256(f"{candidate['provider']}:{candidate['provider_id']}:{digest}".encode()).hexdigest()[:32]
                filename = identifier + candidate["extension"]
                row = {key: candidate[key] for key in ("provider", "provider_id", "kind", "title", "creator", "source_url",
                       "license", "license_url", "attribution", "license_note", "content_id")}
                row.update({key: candidate[key] for key in ("changes", "selection_source") if key in candidate})
                row.update(id=identifier, filename=filename, duration=duration, bytes=temporary.stat().st_size,
                           sha256=digest, acquired_at=datetime.now(timezone.utc).isoformat())
                with job_lock, _cache_lock:
                    _guard(stop_event, deadline)
                    if _profile_root(profile_id, kind) != root or temporary.resolve() != temporary:
                        raise AudioSourceError("cache_changed", "The channel audio cache changed during download; the file was skipped.")
                    destination = root / filename
                    if destination.resolve() != destination:
                        raise AudioSourceError("cache_unavailable", "The channel audio cache contains an unavailable file.")
                    existed = destination.exists()
                    if existed and (not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest):
                        raise AudioSourceError("cache_unavailable", "An existing automatic audio file could not be verified.")
                    if not existed:
                        temporary.replace(destination)
                    try:
                        _save_cache(root, key, profile_id, row)
                    except Exception:
                        if not existed:
                            destination.unlink(missing_ok=True)
                        raise
                message = ("Selected a matching recording from the bundled licensed collection and saved its source and credits."
                           if candidate["provider"] == "bundled" else
                           "Selected matching free audio from Wikimedia Commons and saved its license and credits.")
                _set_status(True, "bundled" if candidate["provider"] == "bundled" else "downloaded", message,
                            provider=candidate["provider"])
                return {**row, "path": str(destination)}
            except (ValueError, OSError) as exc:
                if isinstance(exc, AudioSourceError) and exc.code in {"cancelled", "timeout", "profile_unavailable", "cache_changed"}:
                    raise
                last_error = exc
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                    temporary = None
        _guard(stop_event, deadline)
        if isinstance(last_error, AudioSourceError):
            raise last_error
        if last_error is None:
            raise AudioSourceError("no_licensed_match", "No matching audio with an allowed free license was found; the added audio layer was omitted.")
        raise AudioSourceError("unusable_audio", "Matching free audio could not be validated; the added audio layer was omitted.")
    except AudioSourceError as exc:
        _set_status(False, exc.code, str(exc))
        print(f"   → {exc}")
        return None
    except (OSError, ValueError, TypeError, RuntimeError):
        message = "Automatic audio could not be prepared; the added audio layer was omitted."
        _set_status(False, "unavailable", message)
        print("   → " + message)
        return None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
