"""YouTube description builder: hook, chapters, key points, sources, credits, hashtags."""
import json
import math
import os

import config

def _ts(secs):
    secs = max(0, int(secs))
    h, m, s = secs//3600, (secs%3600)//60, secs%60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def load_credits():
    credits = []
    for name in ("credits.json", "video_credits.json"):
        try:
            with open(os.path.join(config.OUTPUT_DIR, name), encoding="utf-8") as f:
                records = json.load(f)
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                item = dict(record)
                if item.get("provider") == "pexels":
                    item.setdefault("source", "Pexels video")
                    item.setdefault("link", item.get("source_url", ""))
                if item.get("source") and item not in credits:
                    credits.append(item)
        except (OSError, ValueError):
            pass
    return credits

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
            if c.get("provider") != "pexels" and c.get("source") != "Pexels video":
                continue
            source_url = c.get("source_url") or c.get("link") or ""
            key = (c.get("credit"), source_url)
            if key in seen:
                continue
            seen.add(key)
            credit = c.get("credit") or f"Video by {c.get('author', 'Pexels contributor')} on Pexels"
            lines.append(f"• {credit} — {source_url}")
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
    sources = (research.get("grounding") or {}).get("sources") or []
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
    for detailed in (True, False):
        credit = _credit_lines(credits, detailed)
        text = "\n".join(lines + (["─"*40] + credit + [""] if credit else []) + tail)
        if len(text) <= 4900:      # YouTube's limit is 5000
            return text
    return text[:4900]
