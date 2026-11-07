"""
Trend Radar — what the internet is curious about right now.
All sources are keyless.

  google      Google Trends daily RSS (search spikes)
  wikipedia   Wikipedia most-read articles (yesterday)
  hackernews  Hacker News front page (tech)
  spaceflight Spaceflight News API (space & science)
  arxiv       Newest arXiv papers in the channel's fields
"""
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import config
from apis.http import get, get_json, ttl_cache

_HT = {"ht": "https://trends.google.com/trending/rss"}
_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_DEFAULT_ARXIV_CATEGORIES = ("astro-ph.CO", "gr-qc", "quant-ph", "cs.AI", "q-bio.NC")

# Wikipedia's most-read list is dominated by these — never video material
_WIKI_SKIP = ("Main_Page", "Special:", "Wikipedia:", "Portal:", "File:", "Deaths_in_")


def google_trends(geo: str = "US", limit: int = 12) -> list:
    r = get("https://trends.google.com/trending/rss", params={"geo": geo})
    root = ET.fromstring(r.content)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        news = item.find("ht:news_item", _HT)
        url = (news.findtext("ht:news_item_url", namespaces=_HT)
               if news is not None else None)
        headline = (news.findtext("ht:news_item_title", namespaces=_HT)
                    if news is not None else "")
        out.append({
            "title":  title,
            "detail": headline or "",
            "meta":   (item.findtext("ht:approx_traffic", namespaces=_HT) or "").strip(),
            "url":    url or f"https://www.google.com/search?q={quote_plus(title)}",
        })
    return out[:limit]


def wikipedia_most_read(limit: int = 12) -> list:
    day = datetime.now(timezone.utc) - timedelta(days=1)
    d = get_json(f"https://en.wikipedia.org/api/rest_v1/feed/featured/{day:%Y/%m/%d}")
    out = []
    for a in d.get("mostread", {}).get("articles", []):
        if a.get("title", "").startswith(_WIKI_SKIP):
            continue
        out.append({
            "title":  a.get("normalizedtitle") or a.get("title", "").replace("_", " "),
            "detail": a.get("description", ""),
            "meta":   f"{a.get('views', 0):,} views",
            "url":    a.get("content_urls", {}).get("desktop", {}).get("page", ""),
        })
    return out[:limit]


def hackernews(limit: int = 10) -> list:
    d = get_json("https://hn.algolia.com/api/v1/search",
                 params={"tags": "front_page", "hitsPerPage": limit})
    return [{
        "title":  h.get("title", ""),
        "detail": h.get("author", ""),
        "meta":   f"{h.get('points', 0)} pts",
        "url":    h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
    } for h in d.get("hits", []) if h.get("title")]


def spaceflight_news(limit: int = 10) -> list:
    d = get_json("https://api.spaceflightnewsapi.net/v4/articles/",
                 params={"limit": limit})
    return [{
        "title":  a.get("title", ""),
        "detail": (a.get("summary") or "")[:160],
        "meta":   a.get("news_site", ""),
        "url":    a.get("url", ""),
    } for a in d.get("results", [])]


def arxiv_latest(limit: int = 8, *, categories=None) -> list:
    cats = categories if categories is not None else getattr(config, "ARXIV_CATEGORIES", _DEFAULT_ARXIV_CATEGORIES)
    query = " OR ".join(f"cat:{c}" for c in cats)
    r = get("https://export.arxiv.org/api/query", params={
        "search_query": query, "sortBy": "submittedDate",
        "sortOrder": "descending", "max_results": limit,
    }, timeout=20)
    root = ET.fromstring(r.content)
    out = []
    for e in root.findall("a:entry", _ATOM):
        title = " ".join((e.findtext("a:title", namespaces=_ATOM) or "").split())
        cat = e.find("{http://arxiv.org/schemas/atom}primary_category")
        out.append({
            "title":  title,
            "detail": " ".join((e.findtext("a:summary", namespaces=_ATOM) or "").split())[:160],
            "meta":   cat.get("term") if cat is not None else "arXiv",
            "url":    e.findtext("a:id", namespaces=_ATOM) or "",
        })
    return out


SOURCES = {
    "google":      ("Google Trends",   google_trends),
    "wikipedia":   ("Wikipedia",       wikipedia_most_read),
    "hackernews":  ("Hacker News",     hackernews),
    "spaceflight": ("Spaceflight News", spaceflight_news),
    "arxiv":       ("arXiv",           arxiv_latest),
}


def _safe(key: str, geo: str, arxiv_categories: tuple):
    label, fn = SOURCES[key]
    t0 = time.time()
    try:
        if key == "google":
            items = fn(geo)
        elif key == "arxiv":
            items = fn(categories=arxiv_categories)
        else:
            items = fn()
        return key, {"label": label, "ok": True, "items": items,
                     "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return key, {"label": label, "ok": False, "items": [],
                     "error": str(e)[:200], "ms": int((time.time() - t0) * 1000)}


def get_trends() -> dict:
    """Capture this channel's inputs before starting workers or checking cache."""
    geo = getattr(config, "TRENDS_GEO", "US")
    categories = tuple(getattr(config, "ARXIV_CATEGORIES", _DEFAULT_ARXIV_CATEGORIES))
    return _get_trends(geo, categories)


@ttl_cache(15 * 60)
def _get_trends(geo: str, categories: tuple) -> dict:
    # ContextVars do not implicitly follow ThreadPoolExecutor tasks. Pass the
    # selected region/categories explicitly, and keep them in the cache key.
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as ex:
        futures = [ex.submit(_safe, key, geo, categories) for key in SOURCES]
        results = dict(future.result() for future in futures)
    return {"sources": results, "fetched_at": int(time.time())}


get_trends.cache_clear = _get_trends.cache_clear


def headlines(limit: int = 25) -> list:
    """Flat list of trending titles — fed to the researcher for trend-aware picks."""
    data = get_trends()["sources"]
    titles = []
    for key in ("spaceflight", "arxiv", "hackernews", "wikipedia", "google"):
        titles += [i["title"] for i in data.get(key, {}).get("items", [])[:6]]
    return titles[:limit]
