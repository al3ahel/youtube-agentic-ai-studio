"""Pixabay scene selection uses real media probes with offline API transports."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time

import pytest

import config
from apis import stock_video as stock
from video import stock_media


KEY = "pixabay-test-key-must-never-reach-media"
BINDING = {"script_signature": "a" * 64, "profile_id": "default", "scene_id": "scene-1"}
BRIEF = {"subject_terms": ["water filter", "purifier"], "exclude_terms": ["cartoon"],
         "theme": "outdoor survival", "palette": []}


class Response:
    def __init__(self, payload=None, *, data=None, status=200, headers=None, after_chunk=None):
        self.status_code = status
        self.headers = headers or {}
        self.data = json.dumps(payload).encode() if data is None and payload is not None else data or b""
        self.closed = False
        self.after_chunk = after_chunk

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.data), chunk_size):
            yield self.data[offset:offset + chunk_size]
            if self.after_chunk is not None:
                self.after_chunk()

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PIXABAY_API_KEY", KEY)
    monkeypatch.setattr(config, "PEXELS_API_KEY", "")
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    stock._set_status(False, "not_requested")
    monkeypatch.setattr(stock.requests, "get", lambda *args, **kw: pytest.fail("Unexpected network request"))


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg/ffprobe required for real media validation")
    root = tmp_path_factory.mktemp("stock-selection-clips")
    result = {}
    for name, color, size, duration, box in [
        ("red", "red", "640x360", 4, True), ("blue", "blue", "640x360", 4, True),
        ("portrait", "blue", "360x640", 4, True), ("short", "blue", "640x360", 1, True),
        ("blank", "black", "640x360", 4, False),
    ]:
        target = root / (name + ".mp4")
        source = f"color=c={color}:size={size}:rate=12:duration={duration}"
        if box:
            source += ",drawbox=x=80:y=60:w=120:h=180:color=white:t=fill"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source, "-an", "-c:v", "libx264",
                        "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", str(target)],
                       check=True, capture_output=True, timeout=20)
        result[name] = target.read_bytes()
    return result


def hit(identifier=1, *, tags="water filter, purifier, survival", width=640, height=360, duration=4,
        url=None, size=10000, video_type="film"):
    return {"id": identifier, "type": video_type, "duration": duration, "tags": tags,
            "pageURL": f"https://pixabay.com/videos/id-{identifier}/?tracking=removed", "user": "Camera Artist",
            "videos": {"medium": {"url": url or f"https://cdn.pixabay.com/video/{identifier}.mp4",
                                    "width": width, "height": height, "size": size}}}


def transport(monkeypatch, hits, assets):
    calls, responses = [], []

    def get(url, **options):
        calls.append((url, options))
        if url == stock.PIXABAY_SEARCH_URL:
            response = Response({"hits": hits})
        else:
            assert url in assets
            response = Response(data=assets[url], headers={"Content-Type": "video/mp4", "Content-Length": str(len(assets[url]))})
        responses.append(response)
        return response

    monkeypatch.setattr(stock.requests, "get", get)
    return calls, responses


def acquire(output, **options):
    values = {"brief": deepcopy(BRIEF), **BINDING, **options}
    return stock.fetch_video("water filter survival", output, min_duration=3, **values)


def test_pixabay_same_key_real_probe_and_bound_receipt(monkeypatch, tmp_path, clips):
    calls, responses = transport(monkeypatch, [hit(width=1920, height=1080, duration=99)],
                                 {"https://cdn.pixabay.com/video/1.mp4": clips["blue"]})
    output = tmp_path / "output"
    record = acquire(output)
    assert record is not None and record["provider"] == "pixabay"
    assert record["duration"] == 4 and (record["width"], record["height"]) == (640, 360)
    receipt = record["selection"]
    assert receipt["sha256"] == hashlib.sha256(clips["blue"]).hexdigest()
    assert receipt["bytes"] == len(clips["blue"])
    assert receipt["matched_subjects"] == ["water filter", "purifier"]
    assert receipt["license"] == "Pixabay Content License"
    assert receipt["validation"] == "metadata_and_decoded_frames"
    assert stock.verify_footage(record, output, **BINDING) is True
    assert record["source_url"] == "https://pixabay.com/videos/id-1/"
    assert not (output / "video_credits.json").exists()  # Root commits only selected plan credits.
    url, request = calls[0]
    assert url == "https://pixabay.com/api/videos/" and request["params"]["key"] == KEY
    assert request["params"]["video_type"] == "film" and request["params"]["safesearch"] == "true"
    assert "orientation" not in request["params"] and "colors" not in request["params"]
    assert request["allow_redirects"] is False
    assert KEY not in json.dumps(calls[1]) and "Authorization" not in calls[1][1]["headers"]
    assert KEY not in json.dumps(record)
    cache_text = next((tmp_path / "data" / "video-search-cache").glob("*.json")).read_text()
    assert KEY not in cache_text and all(response.closed for response in responses)


def test_subject_relevance_is_required_before_blue_palette_preference(monkeypatch, tmp_path, clips):
    calls, _ = transport(monkeypatch, [hit(1, tags="blue sky, clouds, rain"), hit(2)], {
        "https://cdn.pixabay.com/video/1.mp4": clips["blue"],
        "https://cdn.pixabay.com/video/2.mp4": clips["red"],
    })
    brief = {**BRIEF, "palette": ["#0000ff"]}
    record = acquire(tmp_path / "output", brief=brief)
    assert record["provider_id"] == "2"
    assert all(url != "https://cdn.pixabay.com/video/1.mp4" for url, _ in calls)
    assert stock.get_status()["rejection_reasons"]["subject_mismatch"] == 1


def test_palette_ranks_actual_decoded_frames_only_among_matching_subjects(monkeypatch, tmp_path, clips):
    calls, _ = transport(monkeypatch, [hit(1), hit(2)], {
        "https://cdn.pixabay.com/video/1.mp4": clips["red"],
        "https://cdn.pixabay.com/video/2.mp4": clips["blue"],
    })
    record = acquire(tmp_path / "output", brief={**BRIEF, "palette": ["#0000ff"]})
    assert record["provider_id"] == "2" and record["selection"]["palette_score"] > .9
    assert len(calls) == 3 and stock.get_status()["candidates_downloaded"] == 2


def test_exclusions_and_duplicate_ids_are_respected_before_download(monkeypatch, tmp_path, clips):
    calls, _ = transport(monkeypatch, [hit(1, tags="water filter cartoon"), hit(2), hit(3)], {
        "https://cdn.pixabay.com/video/3.mp4": clips["blue"],
    })
    record = acquire(tmp_path / "output", exclude_ids={"pixabay:2"})
    assert record["provider_id"] == "3" and len(calls) == 2
    assert stock.get_status()["rejection_reasons"] == {"excluded_subject": 1, "already_used": 1}


@pytest.mark.parametrize("change", [
    {"script_signature": "b" * 64}, {"profile_id": "c" * 32}, {"scene_id": "scene-2"},
    {"brief": {**BRIEF, "theme": "rescue"}},
])
def test_clip_cache_is_scoped_to_script_profile_scene_and_brief(monkeypatch, tmp_path, clips, change):
    calls, _ = transport(monkeypatch, [hit()], {"https://cdn.pixabay.com/video/1.mp4": clips["blue"]})
    output = tmp_path / "output"
    first, second = acquire(output), acquire(output, **change)
    assert first["path"] != second["path"] and first["selection"]["request_sha256"] != second["selection"]["request_sha256"]
    assert [url for url, _ in calls].count(stock.PIXABAY_SEARCH_URL) == 1  # Metadata safely shared for 24h.
    assert len(calls) == 3  # Each distinct scene receives a freshly validated project asset.
    if "brief" not in change:
        assert stock.verify_footage(first, output, **{**BINDING, **change}) is False


def test_verified_modern_clip_reuse_performs_no_api_or_media_request(monkeypatch, tmp_path, clips):
    transport(monkeypatch, [hit()], {"https://cdn.pixabay.com/video/1.mp4": clips["blue"]})
    output = tmp_path / "output"
    first = acquire(output)
    monkeypatch.setattr(stock.requests, "get", lambda *args, **kwargs: pytest.fail("Verified local reuse used the network"))
    assert acquire(output) == first
    assert stock.get_status()["reason"] == "reused_verified_clip"


def test_new_subject_cannot_reuse_old_clip_from_same_output(monkeypatch, tmp_path, clips):
    calls, _ = transport(monkeypatch, [hit()], {"https://cdn.pixabay.com/video/1.mp4": clips["blue"]})
    output = tmp_path / "output"
    original = acquire(output)
    assert stock.fetch_video("solar panels", output, min_duration=3,
        brief={"subject_terms": ["solar panels"]}, **{**BINDING, "script_signature": "d" * 64}) is None
    assert stock.get_status()["reason"] == "subject_mismatch"
    assert Path(original["path"]).read_bytes() == clips["blue"]
    assert len(calls) == 3 and sum("cdn.pixabay.com" in url for url, _ in calls) == 1


def test_changed_file_and_malformed_receipt_are_cache_misses_not_reuse_or_repair_traps(monkeypatch, tmp_path, clips):
    calls, _ = transport(monkeypatch, [hit()], {"https://cdn.pixabay.com/video/1.mp4": clips["blue"]})
    output = tmp_path / "output"
    record = acquire(output)
    Path(record["path"]).write_bytes(clips["red"])
    assert stock.verify_footage(record, output, **BINDING) is False
    refreshed = acquire(output)
    assert stock.verify_footage(refreshed, output, **BINDING) is True
    (Path(refreshed["path"]).parent / "selection.json").write_text("broken receipt")
    again = acquire(output)
    assert stock.verify_footage(again, output, **BINDING) is True
    assert len(calls) == 4  # One search, three real validations/downloads; no stale directory discovery.


def test_receipt_survives_archive_relative_path_remapping(monkeypatch, tmp_path, clips):
    transport(monkeypatch, [hit()], {"https://cdn.pixabay.com/video/1.mp4": clips["blue"]})
    record = acquire(tmp_path / "output")
    archive = tmp_path / "archive"
    destination = archive / "assets" / "media" / "renamed.mp4"
    destination.parent.mkdir(parents=True)
    shutil.copy2(record["path"], destination)
    archived = {**deepcopy(record), "path": "assets/media/renamed.mp4"}
    assert stock.verify_footage(archived, archive, **BINDING) is True
    assert stock.verify_footage(archived, archive, **{**BINDING, "profile_id": "e" * 32}) is False
    assert stock.verify_footage({k: v for k, v in archived.items() if k != "selection"}, archive) is None


def test_pixabay_search_metadata_expires_after_24_hours(monkeypatch, tmp_path, clips):
    calls, _ = transport(monkeypatch, [hit()], {"https://cdn.pixabay.com/video/1.mp4": clips["blue"]})
    output = tmp_path / "output"
    acquire(output)
    cache = next((tmp_path / "data" / "video-search-cache").glob("*.json"))
    value = json.loads(cache.read_text())
    value["created"] = time.time() - stock.CACHE_SECONDS - 1
    cache.write_text(json.dumps(value))
    acquire(output, scene_id="scene-2")
    assert [url for url, _ in calls].count(stock.PIXABAY_SEARCH_URL) == 2


def test_search_cache_does_not_share_an_account_identity(monkeypatch, tmp_path, clips):
    calls, _ = transport(monkeypatch, [hit()], {"https://cdn.pixabay.com/video/1.mp4": clips["blue"]})
    output = tmp_path / "output"
    acquire(output)
    monkeypatch.setattr(config, "PIXABAY_API_KEY", "another-private-pixabay-key")
    acquire(output, scene_id="scene-2")
    assert [url for url, _ in calls].count(stock.PIXABAY_SEARCH_URL) == 2
    paths = list((tmp_path / "data" / "video-search-cache").glob("*.json"))
    assert len(paths) == 2 and all("private" not in path.name for path in paths)


def test_bad_first_asset_continues_to_another_relevant_candidate(monkeypatch, tmp_path, clips):
    transport(monkeypatch, [hit(1), hit(2)], {
        "https://cdn.pixabay.com/video/1.mp4": b"\x00\x00\x00\x18ftypmp42" + bytes(100),
        "https://cdn.pixabay.com/video/2.mp4": clips["blue"],
    })
    record = acquire(tmp_path / "output")
    assert record["provider_id"] == "2" and stock.get_status()["candidates_downloaded"] == 2
    assert stock.get_status()["rejection_reasons"]["media_probe_failed"] == 1


@pytest.mark.parametrize("clip,reason", [("short", "clip_too_short"), ("blank", "blank_video_frames"),
                                         ("portrait", "excessive_crop")])
def test_real_media_overrules_misleading_api_dimensions_and_duration(monkeypatch, tmp_path, clips, clip, reason):
    transport(monkeypatch, [hit()], {"https://cdn.pixabay.com/video/1.mp4": clips[clip]})
    output = tmp_path / "output"
    assert acquire(output) is None and stock.get_status()["reason"] == reason
    assert not list(output.rglob("selection.json"))
    assert not list(output.rglob("*.part")) and not list(output.rglob("*.mp4"))


def test_portrait_request_rejects_destructive_landscape_crop_before_download(monkeypatch, tmp_path, clips):
    calls, _ = transport(monkeypatch, [hit(1), hit(2, width=360, height=640)], {
        "https://cdn.pixabay.com/video/2.mp4": clips["portrait"],
    })
    record = stock.fetch_video("water filter", tmp_path / "output", "portrait", 3, brief=BRIEF, **BINDING)
    assert record["provider_id"] == "2" and record["selection"]["crop_fraction"] == 1
    assert len(calls) == 2


def test_pixabay_cdn_redirect_cannot_use_pexels_or_other_hosts(monkeypatch, tmp_path):
    calls = []
    response = Response(status=302, headers={"Location": "https://videos.pexels.com/other.mp4"})
    monkeypatch.setattr(stock.requests, "get", lambda *a, **kw: calls.append((a, kw)) or response)
    assert not stock._download("https://cdn.pixabay.com/video/clip.mp4", tmp_path / "clip.mp4", provider="pixabay")
    assert len(calls) == 1 and response.closed


def test_credential_bearing_cdn_url_is_rejected_before_a_request(tmp_path):
    assert not stock._download("https://cdn.pixabay.com/video/clip.mp4?key=" + KEY,
                               tmp_path / "clip.mp4", provider="pixabay")


def test_cancellation_during_download_removes_partial_clip_and_never_commits_receipt(monkeypatch, tmp_path, clips):
    stop = threading.Event()
    responses = []
    def get(url, **options):
        response = (Response({"hits": [hit()]}) if url == stock.PIXABAY_SEARCH_URL else
                    Response(data=clips["blue"], headers={"Content-Type": "video/mp4"}, after_chunk=stop.set))
        responses.append(response)
        return response
    monkeypatch.setattr(stock.requests, "get", get)
    output = tmp_path / "output"
    with pytest.raises(InterruptedError):
        acquire(output, stop_event=stop)
    assert not list(output.rglob("*.mp4")) and not list(output.rglob("*.part"))
    assert not list(output.rglob("selection.json")) and all(response.closed for response in responses)


def test_cancellation_terminates_actual_probe_process(monkeypatch, tmp_path, clips):
    target = tmp_path / "clip.mp4"
    target.write_bytes(clips["blue"])
    stop, children = threading.Event(), []
    real_popen = subprocess.Popen
    def popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        stop.set()
        return child
    monkeypatch.setattr(stock_media.subprocess, "Popen", popen)
    with pytest.raises(InterruptedError):
        stock_media.inspect_clip(target, "landscape", 3, stop_event=stop)
    assert len(children) == 1 and children[0].poll() is not None


@pytest.mark.parametrize("status,reason", [(429, "rate_limited"), (403, "access_denied"), (503, "provider_unavailable")])
def test_pixabay_failure_status_has_no_download_or_unbounded_retry(monkeypatch, tmp_path, status, reason):
    calls = []
    monkeypatch.setattr(stock.requests, "get", lambda *a, **kw: calls.append(a) or Response(status=status))
    assert acquire(tmp_path / "output") is None
    assert stock.get_status()["reason"] == reason and len(calls) == 1


def test_provider_fallback_stays_explicit_and_preserves_subject_requirement(monkeypatch, tmp_path, clips):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "pexels-other-key")
    calls = []
    def get(url, **options):
        calls.append((url, options))
        if url == stock.PIXABAY_SEARCH_URL:
            return Response({"hits": []})
        if url == stock.SEARCH_URL:
            return Response({"videos": [{"id": 42, "duration": 4,
                "url": "https://www.pexels.com/video/outdoor-water-filter-42/", "user": {"name": "Other Artist"},
                "video_files": [{"id": 99, "file_type": "video/mp4", "width": 640, "height": 360,
                                 "link": "https://videos.pexels.com/video.mp4"}]}]})
        return Response(data=clips["blue"], headers={"Content-Type": "video/mp4"})
    monkeypatch.setattr(stock.requests, "get", get)
    record = acquire(tmp_path / "output")
    assert record["provider"] == "pexels" and stock.get_status()["provider"] == "pexels"
    assert calls[0][1]["params"]["key"] == KEY
    assert "key" not in calls[1][1]["params"] and calls[1][1]["headers"]["Authorization"] == "pexels-other-key"
    assert "Authorization" not in calls[2][1]["headers"]


def test_ffprobe_unavailable_is_not_a_successful_download(monkeypatch, tmp_path, clips):
    transport(monkeypatch, [hit()], {"https://cdn.pixabay.com/video/1.mp4": clips["blue"]})
    real_which = shutil.which
    monkeypatch.setattr(stock_media.shutil, "which", lambda name: None if name == "ffprobe" else real_which(name))
    output = tmp_path / "output"
    assert acquire(output) is None and stock.get_status()["reason"] == "ffprobe_unavailable"
    assert not list(output.rglob("*.mp4")) and not list(output.rglob("selection.json"))


@pytest.mark.parametrize("options", [
    {"brief": {}}, {"brief": None}, {"profile_id": "someone-else"}, {"script_signature": None},
    {"scene_id": "../scene"}, {"brief": {**BRIEF, "palette": ["javascript:bad"]}},
    {"brief": {"subject_terms": ["the and footage"]}},
])
def test_invalid_bound_request_never_searches_or_blesses_legacy_files(tmp_path, options):
    assert acquire(tmp_path / "output", **options) is None
    assert stock.get_status()["reason"] == "invalid_request_binding"
