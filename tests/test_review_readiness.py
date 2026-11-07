"""Review stays readable across saved drafts, legacy metadata and app updates."""
from copy import deepcopy
from html.parser import HTMLParser
import os
from pathlib import Path
import shutil
from urllib.parse import parse_qs, urlsplit

import pytest

import config
from studio import create_app, review
from studio.state import fresh_project_state, state, stop_event


@pytest.fixture
def app(tmp_path, monkeypatch):
    previous = deepcopy(state)
    state.clear()
    state.update(fresh_project_state())
    stop_event.clear()
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(config, "IMAGES_DIR", str(output / "images"))
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", False)
    application = create_app()
    application.config["TESTING"] = True
    yield application
    state.clear()
    state.update(previous)
    stop_event.clear()


@pytest.fixture
def client(app):
    return app.test_client()


def _saved_project():
    state.update(script={
        "title": "A saved video title", "description": "The original generated introduction.",
        "tags": ["saved tag"],
        "sections": [{"id": 1, "title": "The evidence", "narration": "Observe and compare the evidence."}],
    }, research={
        "topic": "A saved research topic",
        "grounding": {"sources": [{"title": "An older source", "url": "https://example.test/report"}]},
    })


@pytest.mark.parametrize("description", ["My saved publishing description.", ""])
def test_output_uses_saved_description_without_building_a_fallback(client, monkeypatch, description):
    _saved_project()
    state["description"] = description
    before = deepcopy(state)

    def forbidden(*args, **kwargs):
        pytest.fail("Reading a saved description must not rebuild it from legacy research metadata")

    monkeypatch.setattr(review, "build_description", forbidden)
    response = client.get("/api/output")
    assert response.status_code == 200
    assert response.json["description"] == description
    assert response.json["title"] == before["script"]["title"]
    assert response.json["tags"] == before["script"]["tags"]
    assert response.json["sections"] == before["script"]["sections"]
    assert state == before


@pytest.mark.parametrize("source", [
    {"title": "An older source", "url": "https://example.test/report"},
    {"url": "https://example.test/report"},
    {"title": "An older source"},
    {"title": "An older source", "source": None, "url": "https://example.test/report"},
])
@pytest.mark.parametrize("description_is_null", [False, True])
def test_output_builds_missing_description_with_optional_legacy_source_fields(
        client, source, description_is_null):
    _saved_project()
    state["research"]["grounding"]["sources"] = [source]
    if description_is_null:
        state["description"] = None
    else:
        state.pop("description", None)
    before = deepcopy(state)
    response = client.get("/api/output")
    assert response.status_code == 200
    description = response.json["description"]
    assert isinstance(description, str)
    assert before["script"]["description"] in description
    if source.get("url"):
        assert source["url"] in description
        if source.get("title"):
            assert source["title"] in description
        assert response.json["sources"]
        assert all(isinstance(row[key], str) and row[key]
                   for row in response.json["sources"] for key in ("title", "source", "url"))
    else:
        assert response.json["sources"] == []
    assert "(None)" not in description
    assert state == before


class _AssetURLs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        url = attributes.get("src") if tag == "script" else attributes.get("href") if tag == "link" else None
        if url:
            path = urlsplit(url).path
            if path.startswith("/static/") and path.endswith((".js", ".css")):
                self.urls.append(url)


def _bundle_urls(response):
    assert response.status_code == 200
    parser = _AssetURLs()
    parser.feed(response.get_data(as_text=True))
    assert parser.urls, "The home page must include its local JavaScript and CSS"
    paths = {urlsplit(url).path for url in parser.urls}
    assert {"/static/app.css", "/static/js/review.js", "/static/js/visuals.js"} <= paths
    tokens = []
    for url in parser.urls:
        values = parse_qs(urlsplit(url).query).get("v", [])
        assert len(values) == 1 and values[0], f"Missing bundle version: {url}"
        tokens.append(values[0])
    assert len(set(tokens)) == 1, "All scripts and styles must come from one app bundle"
    return parser.urls, tokens[0]


def test_home_versions_all_local_scripts_and_styles_and_disables_html_cache(client):
    response = client.get("/")
    urls, version = _bundle_urls(response)
    assert response.cache_control.no_store
    assert _bundle_urls(client.get("/"))[1] == version
    for url in urls:
        asset = client.get(url)
        assert asset.status_code == 200, url
        assert asset.data, url
        asset.close()


@pytest.fixture
def copied_ui(app, tmp_path):
    source = Path(app.root_path)
    root = tmp_path / "isolated-studio"
    shutil.copytree(source / "static", root / "static")
    (root / "templates").mkdir()
    shutil.copy2(source / "templates" / "index.html", root / "templates" / "index.html")
    app.root_path = str(root)
    app.static_folder = str(root / "static")
    return root


@pytest.mark.parametrize("filename", ["static/js/visuals.js", "static/app.css", "templates/index.html"])
def test_same_size_file_content_change_versions_the_entire_bundle(app, copied_ui, filename):
    client = app.test_client()
    before_urls, before_version = _bundle_urls(client.get("/"))
    changed = copied_ui / filename
    before = changed.read_bytes()
    timestamps = changed.stat()
    # A copied checkout may preserve timestamps. Content is the bundle identity.
    replacement = b" " if before[-1:] != b" " else b"\n"
    changed.write_bytes(before[:-1] + replacement)
    os.utime(changed, ns=(timestamps.st_atime_ns, timestamps.st_mtime_ns))
    assert changed.stat().st_size == len(before)
    after_response = client.get("/")
    after_urls, after_version = _bundle_urls(after_response)
    assert after_version != before_version
    assert after_response.cache_control.no_store
    assert {urlsplit(url).path for url in before_urls} == {urlsplit(url).path for url in after_urls}
    assert set(before_urls).isdisjoint(after_urls)
    assert _bundle_urls(client.get("/"))[1] == after_version


def _valid_storyboard():
    from agents.visual_director import plan_scenes
    script = {"title": "A measured storyboard", "sections": [{
        "id": 1, "title": "Observe and compare", "narration": "Observe a pattern. Compare the evidence.",
    }]}
    timeline = {"fps": 24, "width": 640, "height": 360, "duration": 6,
                "sections": [{"id": 1, "start": 0, "duration": 6}], "captions": []}
    state.update(script=script, timeline=timeline,
                 visual_plan=plan_scenes(script, timeline, use_ai=False))


@pytest.mark.parametrize("plan", [None, {}, {"scenes": []}, {"scenes": [{"id": "scene-001"}]}])
def test_empty_or_invalid_storyboard_does_not_make_review_ready(client, plan):
    index = Path(config.OUTPUT_DIR) / "motion" / "index.html"
    index.parent.mkdir()
    index.write_text("<html>A stale preview from an earlier project</html>", encoding="utf-8")
    state["visual_plan"] = deepcopy(plan)
    status = client.get("/api/status").json
    assert not status["has_storyboard"]
    assert not status["has_html_preview"]
    assert not status["has_preview"] and not status["has_video"]
    assert not status["review_ready"]
    assert "workspace_warning" in status


def test_valid_storyboard_requires_nonempty_preview_file_to_make_review_ready(client):
    _valid_storyboard()
    status = client.get("/api/status").json
    assert status["has_storyboard"] and not status["has_html_preview"]
    assert not status["review_ready"]
    index = Path(config.OUTPUT_DIR) / "motion" / "index.html"
    index.parent.mkdir()
    index.touch()
    status = client.get("/api/status").json
    assert status["has_storyboard"] and not status["has_html_preview"]
    assert not status["review_ready"]
    index.write_text("<html><body>A prepared motion preview</body></html>", encoding="utf-8")
    status = client.get("/api/status").json
    assert status["has_storyboard"] and status["has_html_preview"]
    assert status["review_ready"] and not status["has_preview"] and not status["has_video"]
    draft = Path(config.OUTPUT_DIR) / "motion_preview.mp4"
    draft.write_bytes(b"encoded draft fixture")
    state["preview_path"] = str(draft)
    index.unlink()
    status = client.get("/api/status").json
    assert status["has_storyboard"] and status["has_preview"]
    assert not status["has_html_preview"] and not status["has_video"]
    assert status["review_ready"]


@pytest.mark.parametrize("path_key,ready_key", [
    ("preview_path", "has_preview"), ("video_path", "has_video"),
])
def test_legacy_playback_requires_a_nonempty_regular_file_without_requiring_a_plan(
        client, path_key, ready_key):
    video = Path(config.OUTPUT_DIR) / "legacy.mp4"
    state[path_key] = str(video)
    for kind in ("missing", "directory", "empty", "video"):
        if kind == "directory":
            video.mkdir()
        elif kind == "empty":
            video.rmdir()
            video.touch()
        elif kind == "video":
            video.write_bytes(b"encoded legacy video fixture")
        status = client.get("/api/status").json
        assert status[ready_key] is (kind == "video")
        assert status["review_ready"] is (kind == "video")
        assert not status["has_storyboard"] and not status["has_html_preview"]


def test_status_exposes_recovery_warning_without_claiming_a_ready_preview(client):
    state["workspace_warning"] = "The previous draft could not be restored. Open it from Library."
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json["workspace_warning"] == state["workspace_warning"]
    assert response.json["review_ready"] is False
