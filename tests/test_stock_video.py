"""Pexels search and download boundaries, using only fake HTTP responses."""
import json
import shutil
import subprocess

import pytest

from apis import stock_video as stock


KEY = "pexels-secret-for-tests-123456789"
MP4 = b"\x00\x00\x00\x18ftypmp42" + bytes(100)


class Response:
    def __init__(self, payload=None, status=200, headers=None, chunks=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks if chunks is not None else ([json.dumps(payload).encode()] if payload is not None else [])
        self.closed = False

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        yield from self.chunks

    def close(self):
        self.closed = True


def video(identifier=1, width=1920, height=1080, duration=20, link="https://videos.pexels.com/video.mp4"):
    return {"id": identifier, "duration": duration, "url": f"https://www.pexels.com/video/example-{identifier}/?tracking=discard",
            "user": {"name": "Camera Author"},
            "video_files": [{"id": 11, "file_type": "video/mp4", "width": width, "height": height, "link": link}]}


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    monkeypatch.setattr(stock.config, "PEXELS_API_KEY", "", raising=False)
    monkeypatch.setattr(stock.config, "PIXABAY_API_KEY", "", raising=False)
    monkeypatch.setattr(stock.config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    stock._set_status(False, "not_requested")

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected live network request")

    monkeypatch.setattr(stock.requests, "get", forbidden)


@pytest.fixture(scope="module")
def playable_mp4(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are needed for actual clip validation")
    target = tmp_path_factory.mktemp("legacy-stock-video") / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=12:duration=12", "-an", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "32", "-pix_fmt", "yuv420p", str(target)],
                   check=True, capture_output=True, timeout=30)
    return target.read_bytes()


@pytest.mark.parametrize("key", ["", "YOUR_PEXELS_API_KEY"])
def test_missing_key_is_explicit_and_does_not_make_output_directory(tmp_path, monkeypatch, key):
    monkeypatch.setattr(stock.config, "PEXELS_API_KEY", key)
    output = tmp_path / "untouched"
    assert stock.fetch_video("science", output) is None
    assert stock.get_status()["reason"] == "missing_key"
    assert not output.exists()


@pytest.mark.parametrize("url", ["http://videos.pexels.com/a.mp4", "https://videos.pexels.com.evil.test/a.mp4",
                              "https://127.0.0.1/a.mp4", "https://user:pass@videos.pexels.com/a.mp4",
                              "https://videos.pexels.com:444/a.mp4", "file:///tmp/a.mp4"])
def test_untrusted_media_urls_rejected_without_request(tmp_path, url):
    assert not stock._download(url, tmp_path / "clip.mp4")


def test_candidates_filter_orientation_duration_and_malformed_rows():
    malformed = video(7)
    malformed["video_files"][0]["file_type"] = None
    payload = {"videos": [video(1), video(2, width=1080, height=1920), video(3, duration=2),
                          video(4, width=320, height=180), video(5, link="https://evil.test/file.mp4"), malformed,
                          {"id": 6, "duration": "NaN", "video_files": []}]}
    candidates = stock._candidates(payload, "landscape", 10)
    assert [candidate["video"]["id"] for candidate in candidates] == [1]


def test_download_redirect_cannot_leave_allowlist(monkeypatch, tmp_path):
    response = Response(status=302, headers={"Location": "https://evil.test/leak"})
    calls = []
    monkeypatch.setattr(stock.requests, "get", lambda *a, **k: calls.append((a, k)) or response)
    assert not stock._download("https://videos.pexels.com/clip.mp4", tmp_path / "clip.mp4")
    assert len(calls) == 1 and response.closed
    assert "Authorization" not in calls[0][1]["headers"]


def test_streamed_file_size_limit_cleans_partial_download(monkeypatch, tmp_path):
    monkeypatch.setattr(stock, "MAX_BYTES", 128)
    response = Response(headers={"Content-Type": "video/mp4"}, chunks=[MP4, MP4])
    monkeypatch.setattr(stock.requests, "get", lambda *a, **k: response)
    assert not stock._download("https://videos.pexels.com/clip.mp4", tmp_path / "clip.mp4")
    assert not list(tmp_path.iterdir())
    assert response.closed


def test_full_fetch_keeps_key_on_api_only_and_saves_attribution(monkeypatch, tmp_path, playable_mp4):
    monkeypatch.setattr(stock.config, "PEXELS_API_KEY", KEY)
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if url == stock.SEARCH_URL:
            return Response({"videos": [video()]})
        return Response(headers={"Content-Type": "video/mp4", "Content-Length": str(len(playable_mp4))}, chunks=[playable_mp4])

    monkeypatch.setattr(stock.requests, "get", get)
    result = stock.fetch_video("sunlight " + KEY, tmp_path, min_duration=10)
    assert result["credit"] == "Video by Camera Author on Pexels"
    assert result["source_url"] == "https://www.pexels.com/video/example-1/"
    assert result["duration"] >= 10 and result["in_point"] == 0
    assert result["width"] == 640 and result["height"] == 360  # Actual file, not the API's claimed 1920x1080.
    assert calls[0][0] == "https://api.pexels.com/v1/videos/search"
    assert calls[0][1]["headers"]["Authorization"] == KEY
    assert KEY not in calls[0][1]["params"]["query"]
    assert "Authorization" not in calls[1][1]["headers"]
    assert stock.get_status()["reason"] == "downloaded"
    credits = (tmp_path / "video_credits.json").read_text()
    assert KEY not in credits and "videos.pexels.com" not in credits
    assert json.loads(credits)[0]["sha256"] == result["sha256"]
    stock.fetch_video("sunlight", tmp_path, min_duration=10)
    assert len(json.loads((tmp_path / "video_credits.json").read_text())) == 1
    assert len(calls) == 2  # Verified scene/query receipt avoids another API call or download.
    assert stock.get_status()["reason"] == "reused_verified_clip"


def test_output_symlink_cannot_redirect_download_outside_job(monkeypatch, tmp_path):
    monkeypatch.setattr(stock.config, "PEXELS_API_KEY", KEY)
    output, outside = tmp_path / "job", tmp_path / "outside"
    output.mkdir()
    outside.mkdir()
    (output / "assets").symlink_to(outside, target_is_directory=True)
    calls = []
    monkeypatch.setattr(stock.requests, "get", lambda *a, **k: calls.append(a) or Response({"videos": [video()]}))
    assert stock.fetch_video("science", output) is None
    assert stock.get_status()["reason"] == "invalid_output_directory"
    assert not calls and not list(outside.iterdir())  # Invalid scope is rejected before API work.


def test_stock_quota_has_no_download_or_unbounded_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(stock.config, "PEXELS_API_KEY", KEY)
    calls = []
    monkeypatch.setattr(stock.requests, "get", lambda *a, **k: calls.append(a) or Response(status=429))
    assert stock.fetch_video("science", tmp_path) is None
    assert stock.get_status()["reason"] == "rate_limited"
    assert len(calls) == 1
