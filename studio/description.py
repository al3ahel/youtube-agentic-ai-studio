"""YouTube description builder: hook, chapters, key points, sources, credits, hashtags."""
import json
import math
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import config


def research_sources(research):
    """Older projects can have source links without today's optional labels."""
    grounding = research.get("grounding") if isinstance(research, dict) else None
    records = grounding.get("sources") if isinstance(grounding, dict) else None
    if not isinstance(records, list):
        return []
    sources = []
    for row in records[:50]:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        if not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
        except ValueError:
            continue
        source = row.get("source")
        title = row.get("title")
        sources.append({
            "url": url,
            "source": source.strip() if isinstance(source, str) and source.strip() else parsed.hostname,
            "title": title.strip() if isinstance(title, str) and title.strip() else url,
        })
    return sources

def _ts(secs):
    secs = max(0, int(secs))
    h, m, s = secs//3600, (secs%3600)//60, secs%60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def load_credits():
    credits = []
    from studio.footage_assets import current_video_credits
    for name in ("credits.json", "video_credits.json"):
        try:
            if name == "video_credits.json":
                records = current_video_credits(config.OUTPUT_DIR)
            else:
                with open(os.path.join(config.OUTPUT_DIR, name), encoding="utf-8") as f:
                    records = json.load(f)
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                item = dict(record)
                if item.get("provider") in ("pexels", "pixabay"):
                    item.setdefault("source", "Pixabay video" if item["provider"] == "pixabay" else "Pexels video")
                    item.setdefault("link", item.get("source_url", ""))
                if item.get("source") and item not in credits:
                    credits.append(item)
        except (OSError, ValueError):
            pass
    return credits


def _safe_audio_url(source):
    try:
        parsed = urlsplit(source) if isinstance(source, str) else None
        valid = (parsed and len(source) <= 4096 and parsed.scheme in {"http", "https"}
                 and parsed.hostname and parsed.username is None and parsed.password is None
                 and not any(character.isspace() or ord(character) < 32 for character in source))
        if valid:
            parsed.port
        return source if valid else ""
    except ValueError:
        return ""


def _current_soundtrack(timeline=None, *, strict=False):
    """Validate the authoritative audio commit; older two-field receipts still work."""
    output = Path(config.OUTPUT_DIR).resolve()
    path = output / "render_audio.json"
    if not path.exists():
        return None
    try:
        if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Invalid soundtrack metadata")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("Invalid soundtrack metadata")
        voice_hash = manifest.get("audio_sha256")
        if not isinstance(voice_hash, str) or not voice_hash:
            raise ValueError("Missing narration identity")
        if isinstance(timeline, dict) and timeline.get("audio_sha256") != voice_hash:
            return None
        from studio.workspace import _fingerprint, _local_file
        media = _local_file(output, manifest.get("path"), {".wav", ".mp3", ".m4a", ".ogg"}, relative=True)
        if not media:
            raise ValueError("The committed soundtrack is unavailable")
        if manifest.get("version") == 2:
            signature = manifest.get("soundtrack_sha256")
            if not isinstance(signature, str) or _fingerprint(output, media)["sha256"] != signature:
                raise ValueError("The committed soundtrack has changed")
        return manifest
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        if strict:
            raise ValueError("The current soundtrack and its credits could not be verified. Apply music and effects again before publishing.") from exc
        return None


def load_audio_credits(timeline=None, *, strict=False):
    """Read the audio actually mixed into this project, never channel defaults."""
    path = os.path.join(config.OUTPUT_DIR, "audio_credits.json")
    try:
        soundtrack = _current_soundtrack(timeline, strict=strict)
        if soundtrack is None:
            return []
        if soundtrack.get("version") == 2:
            assets = soundtrack.get("credits")
        else:
            if not os.path.isfile(path):
                return []
            if os.path.getsize(path) > 1024 * 1024:
                raise ValueError("Audio credits are too large to verify")
            with open(path, encoding="utf-8") as handle:
                manifest = json.load(handle)
            if (not isinstance(manifest, dict) or manifest.get("version") != 1
                    or manifest.get("path") != soundtrack.get("path")
                    or manifest.get("audio_sha256") != soundtrack.get("audio_sha256")):
                return []
            assets = manifest.get("assets", [])
        if not isinstance(assets, list):
            raise ValueError("Invalid applied audio credits")
        if len(assets) > 64 and strict:
            raise ValueError("There are too many applied audio credits to verify")
        result = []
        for asset in assets[:64]:
            if not isinstance(asset, dict) or asset.get("kind") not in {"music", "sfx"}:
                continue
            clean = {"kind": asset["kind"]}
            for key, limit in (("id", 32), ("title", 500), ("filename", 255),
                               ("creator", 500), ("license_note", 2000), ("content_id", 32), ("mode", 32),
                               ("provider", 80), ("provider_id", 200), ("license", 160),
                               ("license_url", 4096), ("attribution", 10000),
                               ("selection_source", 80), ("changes", 2000)):
                value = asset.get(key, "")
                if strict and key in {"title", "creator", "license", "license_note", "attribution"} and isinstance(value, str) and len(value) > limit:
                    raise ValueError("Applied audio attribution exceeds the supported metadata length")
                if key in asset or key in {"id", "title", "filename", "creator", "license_note", "content_id", "mode"}:
                    clean[key] = value[:limit] if isinstance(value, str) else ""
            clean["source_url"] = _safe_audio_url(asset.get("source_url", ""))
            if "license_url" in clean:
                clean["license_url"] = _safe_audio_url(asset.get("license_url", ""))
            volume = asset.get("volume")
            if isinstance(volume, (int, float)) and not isinstance(volume, bool) and math.isfinite(volume) and 0 <= volume <= 1:
                clean["volume"] = volume
            result.append(clean)
        return result
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        if strict:
            raise ValueError("The applied audio credits could not be verified. Apply music and effects again before publishing.") from exc
        return []


def load_sound_report(name, timeline=None):
    """Expose bounded applied reports only while their exact soundtrack is current."""
    if name not in {"sound_design.json", "audio_master.json"}:
        return None
    output = Path(config.OUTPUT_DIR).resolve()
    try:
        manifest = _current_soundtrack(timeline)
        if manifest is None:
            return None
        if manifest.get("version") == 2:
            value = manifest.get(name.removesuffix(".json"))
        else:
            path = output / name
            if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("version") != 1 or not isinstance(manifest, dict)
                or not isinstance(value.get("path"), str) or value["path"] != manifest.get("path")
                or not value.get("audio_sha256") or value["audio_sha256"] != manifest.get("audio_sha256")
                or isinstance(timeline, dict) and timeline.get("audio_sha256") != value["audio_sha256"]):
            return None
        def public(item, depth=0):
            if depth > 6:
                return None
            if isinstance(item, dict):
                return {str(k)[:100]: public(v, depth + 1) for k, v in list(item.items())[:50]
                        if k not in {"path", "audio_sha256", "script_signature"}}
            if isinstance(item, list):
                return [public(row, depth + 1) for row in item[:40]]
            if isinstance(item, str):
                return item[:2048]
            if item is None or isinstance(item, bool):
                return item
            if isinstance(item, (int, float)) and math.isfinite(item):
                return item
            return None
        return public(value)
    except (OSError, ValueError, TypeError, RuntimeError):
        return None


AUDIO_CREDIT_START = "Audio credits (updated by YAAS)"
AUDIO_CREDIT_END = "End audio credits"
_MANAGED_AUDIO = re.compile(r"(?m)^" + re.escape(AUDIO_CREDIT_START) + r"\r?\n[\s\S]*?^"
                            + re.escape(AUDIO_CREDIT_END) + r"[ \t]*(?:\r?\n|$)")
FOOTAGE_CREDIT_START = "Footage credits (updated by YAAS)"
FOOTAGE_CREDIT_END = "End footage credits"
_MANAGED_FOOTAGE = re.compile(r"(?m)^" + re.escape(FOOTAGE_CREDIT_START) + r"\r?\n[\s\S]*?^"
                              + re.escape(FOOTAGE_CREDIT_END) + r"[ \t]*(?:\r?\n|$)")
_CURRENT_PLAN = object()


def _is_footage_credit(record):
    return isinstance(record, dict) and (record.get("provider") in ("pixabay", "pexels")
        or record.get("source") in ("Pixabay video", "Pexels video"))


def _footage_credits(plan=_CURRENT_PLAN):
    """An explicit empty plan has no clips, regardless of old files on disk."""
    from apis.stock_video import LICENSES, PROVIDER_SOURCE_HOSTS
    from studio.footage_assets import credits_for_plan, current_video_credits
    records = current_video_credits(config.OUTPUT_DIR) if plan is _CURRENT_PLAN else credits_for_plan(plan)
    result, seen = [], set()
    for record in records:
        if not _is_footage_credit(record):
            continue
        source = _safe_audio_url(record.get("source_url") or record.get("link"))
        parsed = urlsplit(source)
        provider = next((name for name, hosts in PROVIDER_SOURCE_HOSTS.items() if parsed.hostname in hosts), None)
        if (not provider or parsed.scheme != "https" or parsed.query or parsed.fragment
                or not parsed.path.startswith("/videos/" if provider == "pixabay" else "/video/")):
            raise ValueError("A used video clip is missing its valid provider source link. Rebuild Visuals to restore its credits before publishing.")
        license_name, license_url = LICENSES[provider]
        if (record.get("license") not in (None, "", license_name)
                or record.get("license_url") not in (None, "", license_url)):
            raise ValueError("A used video clip has inconsistent license metadata. Rebuild Visuals to restore its credits before publishing.")
        label = "Pixabay" if provider == "pixabay" else "Pexels"
        credit = record.get("credit") or f"Video from {label}"
        if not isinstance(credit, str) or len(credit) > 500:
            raise ValueError("A used video clip has invalid attribution. Rebuild Visuals before publishing.")
        key = (provider, source)
        if key in seen:
            continue
        seen.add(key)
        result.append({"provider": provider, "source": f"{label} video", "source_url": source,
                       "credit": " ".join(credit.split()), "license": license_name, "license_url": license_url})
    return result


def _footage_credit_block(credits):
    if not credits:
        return ""
    return "\n".join([FOOTAGE_CREDIT_START, *(
        f"Video: {record['credit']} · {record['source_url']} · {record['license']} · {record['license_url']}"
        for record in credits), FOOTAGE_CREDIT_END])


def _remove_known_legacy_footage_lines(description, current, previous=None):
    """Migrate exact generated lines backed by known receipts, never free prose."""
    known_lines, summaries = set(), {}
    for records in (previous or [], current):
        if not isinstance(records, list):
            continue
        used = [row for row in records if _is_footage_credit(row)]
        if not used:
            continue
        for row in used:
            # Both released formats are exact generated text: old lines did
            # not include the license; newer ones include its name and URL.
            for variant in (row, {key: value for key, value in row.items() if key not in {"license", "license_url"}}):
                known_lines.update(_credit_lines([variant], detailed=True)[1:])
        sources = sorted({str(row.get("source", "")) for row in records if isinstance(row, dict) and row.get("source")})
        remaining = sorted({str(row.get("source", "")) for row in records
                            if isinstance(row, dict) and row.get("source") and not _is_footage_credit(row)})
        if sources:
            summaries["Visuals: " + ", ".join(sources)] = "Visuals: " + ", ".join(remaining) if remaining else ""
    lines = []
    for line in description.splitlines(keepends=True):
        text = line.rstrip("\r\n")
        if text in known_lines:
            continue
        if text in summaries:
            if summaries[text]:
                lines.append(summaries[text] + line[len(text):])
            continue
        lines.append(line)
    return "".join(lines).rstrip()


def _with_media_credits(description, footage, audio_assets, *, previous_credits=None):
    if not isinstance(description, str):
        raise ValueError("The video description must be text.")
    prose = _MANAGED_FOOTAGE.sub("", _MANAGED_AUDIO.sub("", description)).rstrip()
    prose = _remove_known_legacy_footage_lines(prose, footage, previous_credits)
    blocks = [block for block in (_footage_credit_block(footage), _audio_credit_block(audio_assets)) if block]
    attribution = "\n\n".join(blocks)
    result = "\n\n".join(part for part in (prose, attribution) if part)
    if len(result) > 5000:
        if len(attribution) > 5000:
            raise ValueError("The applied media credits alone exceed YouTube's 5,000-character description limit. Use fewer clips or attribution-required recordings and rebuild; credits cannot be omitted.")
        raise ValueError(f"The description and applied media credits exceed YouTube's 5,000-character limit by {len(result) - 5000} characters. Shorten your description and try again; the credits have not been removed.")
    return result


def ensure_media_credits(description, timeline=None, *, plan=_CURRENT_PLAN, previous_credits=None):
    """Refresh actual footage and audio attribution without rewriting user prose.

    Omit ``plan`` to read the current committed plan. Explicit ``None`` means
    no footage (Classic or graphics-only), even when older files remain on disk.
    Previous receipts allow exact generated legacy stock lines to be migrated.
    """
    return _with_media_credits(description, _footage_credits(plan),
                               load_audio_credits(timeline, strict=True), previous_credits=previous_credits)


def _audio_credit_block(assets):
    if not assets:
        return ""
    lines = [AUDIO_CREDIT_START]
    for asset in assets:
        label = "Music" if asset["kind"] == "music" else "Sound effect"
        license_name = asset.get("license", "")
        if license_name.upper().startswith("CC BY") and not all(asset.get(key) for key in ("creator", "source_url", "license_url")):
            raise ValueError("A used CC BY recording is missing its creator, source or license link. Apply music and effects again to restore its full credits before publishing.")
        fields = (asset.get("title") or asset.get("filename"), asset.get("creator"), asset.get("source_url"),
                  license_name, asset.get("license_url"))
        attribution = " ".join(asset.get("attribution", "").split())
        if attribution:
            # Preserve extra attribution requirements from the source, including
            # those not expressible as an artist/title/license tuple.
            missing = [value for value in fields if value and value not in attribution]
            detail = " · ".join([attribution, *missing])
        else:
            detail = " · ".join(value for value in fields if value)
        if asset.get("license_note") and not license_name:
            detail += (" · " if detail else "") + asset["license_note"]
        if not detail:
            detail = "Imported recording"
        lines.append(f"{label}: " + " ".join(detail.split()))
    lines.append("Audio adapted: excerpted or looped, faded and mixed beneath narration.")
    lines.append(AUDIO_CREDIT_END)
    return "\n".join(lines)


def _with_audio_credits(description, assets):
    if not isinstance(description, str):
        raise ValueError("The video description must be text.")
    prose = _MANAGED_AUDIO.sub("", description).rstrip()
    block = _audio_credit_block(assets)
    result = prose + ("\n\n" if prose and block else "") + block
    if len(result) > 5000:
        if len(block) > 5000:
            raise ValueError("The applied audio credits alone exceed YouTube's 5,000-character description limit. Choose fewer attribution-required recordings and apply music and effects again; credits cannot be omitted.")
        raise ValueError(f"The description and required audio credits exceed YouTube's 5,000-character limit by {len(result) - 5000} characters. Shorten your description and try again; the audio credits have not been removed.")
    return result


def ensure_audio_credits(description, timeline=None):
    """Refresh only the managed credit block, keeping the creator's prose intact.

    This is used for preserved descriptions and again immediately before upload.
    It never truncates an attribution or silently shortens user-written text.
    """
    return _with_audio_credits(description, load_audio_credits(timeline, strict=True))


def audio_credit_text(timeline=None):
    """The same complete attribution block used for the description and export."""
    return _audio_credit_block(load_audio_credits(timeline, strict=True))


def _credit_lines(credits, detailed=True):
    """Attribution block. CC images (Openverse) are listed individually — that's a licence requirement."""
    if not credits: return []
    sources = sorted({str(c.get("source", "")) for c in credits if c.get("source")})
    lines = ["Visuals: " + ", ".join(sources)]
    cc = [c for c in credits if c.get("source") == "Openverse"]
    if detailed:
        seen = set()
        for c in cc:
            key = (c.get("author"), c.get("link"))
            if key in seen: continue
            seen.add(key)
            lines.append(f"• “{c.get('author','Unknown')}” — {c.get('license','CC')} — {c.get('link','')}")
        for c in credits:
            if c.get("provider") not in ("pexels", "pixabay") and c.get("source") not in ("Pexels video", "Pixabay video"):
                continue
            source_url = c.get("source_url") or c.get("link") or ""
            key = (c.get("credit"), source_url)
            if key in seen:
                continue
            seen.add(key)
            provider = "Pixabay" if c.get("provider") == "pixabay" else "Pexels"
            credit = c.get("credit") or f"Video by {c.get('author', provider + ' contributor')} on {provider}"
            license_note = f" — {c['license']}" if c.get("license") else ""
            license_link = f" ({c['license_url']})" if c.get("license_url") else ""
            lines.append(f"• {credit} — {source_url}{license_note}{license_link}")
    if any(c.get("source") == "NASA" for c in credits):
        lines.append("Space imagery courtesy of NASA.")
    if any(c.get("source") == "Pollinations AI" for c in credits):
        lines.append("Some visuals are AI-generated.")
    return lines

CREDIT_LINE = "🎬 Made with YAAS by Raunak Patil · https://raunakpatil.com"

def with_credit(desc):
    """Add the credit to descriptions written before it existed."""
    if getattr(config, "CREDIT_YAAS", True) and "raunakpatil.com" not in (desc or ""):
        return (desc or "").rstrip() + "\n\n" + CREDIT_LINE
    return desc

def _chapters(script, timeline):
    """Chapter marks require a matching measured timeline; legacy guesses are omitted."""
    if not isinstance(timeline, dict) or timeline.get("timing_source") != "measured_sections":
        return []
    from video.timeline import script_signature
    if timeline.get("script_signature") != script_signature(script):
        return []
    sections, measured = script.get("sections", []), timeline.get("sections", [])
    if not isinstance(measured, list) or len(measured) != len(sections) or len(measured) < 3:
        return []
    chapters, previous = [], -1
    try:
        for index, (section, timing) in enumerate(zip(sections, measured)):
            start = float(timing["start"])
            if not math.isfinite(start) or start < 0 or start <= previous or (index == 0 and start > .05):
                return []
            if str(timing.get("id")) != str(section.get("id", index + 1)):
                return []
            chapters.append({"ts": _ts(start), "title": str(section.get("title", ""))})
            previous = start
    except (KeyError, TypeError, ValueError):
        return []
    return chapters


def build_description(script, research, video_type: str = "normal", timeline=None):
    sections = script.get("sections", [])
    is_shorts = video_type == "shorts" or script.get("video_type") == "shorts"

    lines = []
    if research.get("hook_question"): lines += [research["hook_question"], ""]
    if script.get("description"):     lines += [script["description"], ""]
    if research.get("why_now"):       lines += [research["why_now"], ""]

    # Chapters only make sense for videos > ~2 min — never for Shorts
    if not is_shorts:
        chapters = _chapters(script, timeline)

        if len(chapters) >= 3:
            lines += ["─"*40, "⏱ CHAPTERS", "─"*40]
            for c in chapters: lines.append(f"{c['ts']} {c['title']}")
            lines.append("")

    if research.get("key_points"):
        lines += ["─"*40, "🧠 WHAT YOU'LL LEARN", "─"*40]
        for p in research["key_points"]: lines.append(f"• {p}")
        lines.append("")

    if is_shorts:
        lines += ["👆 Follow for more mind-bending facts!",
                  "💬 Comment: did this surprise you?", ""]
    else:
        lines += ["─"*40, "🔔 Subscribe for a new mind-bending video every week!",
                  "👍 Like if this made you think differently.",
                  "💬 Drop a comment — we read every one.", ""]

    # Sources from fact grounding (Wikipedia / arXiv)
    sources = research_sources(research)
    if sources and not is_shorts:
        lines += ["─"*40, "📚 SOURCES", "─"*40]
        for s in sources[:6]:
            lines.append(f"• {s['title']} ({s['source']}) {s['url']}")
        lines.append("")

    tail = []
    tags = script.get("tags", [])
    if tags: tail.append(" ".join(f"#{t.replace(' ','')}" for t in tags[:8]))
    if is_shorts: tail.append("#Shorts #YouTubeShorts")
    if getattr(config, "CREDIT_YAAS", True):
        tail.insert(0, CREDIT_LINE + "\n")

    credits = load_credits()
    footage = _footage_credits()
    picture_credits = [record for record in credits if not _is_footage_credit(record)]
    audio_assets = load_audio_credits(timeline, strict=True)
    block = "\n\n".join(part for part in (_footage_credit_block(footage), _audio_credit_block(audio_assets)) if part)
    # Generated prose may be shortened, but complete attribution is reserved
    # first. A user-written description goes through ensure_media_credits and
    # receives an actionable error instead of being shortened automatically.
    prose_limit = min(4900, 5000 - len(block) - (2 if block else 0))
    if prose_limit < 0:
        return _with_media_credits("", footage, audio_assets)  # raises a clear length error
    for detailed in (True, False):
        credit = _credit_lines(picture_credits, detailed)
        text = _MANAGED_FOOTAGE.sub("", _MANAGED_AUDIO.sub("", "\n".join(lines + (["─"*40] + credit + [""] if credit else []) + tail))).rstrip()
        text = _remove_known_legacy_footage_lines(text, footage, credits)
        if len(text) <= prose_limit:
            return _with_media_credits(text, footage, audio_assets)
    return _with_media_credits(text[:prose_limit], footage, audio_assets)
