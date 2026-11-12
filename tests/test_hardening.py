"""Regressions for secret handling, partial downloads and settings-file safety."""
import io
import json
import os
from types import SimpleNamespace

import pytest
import requests

import config
import gui
from agents import researcher
from apis import http, images
from video import stock


# ── .env handling ────────────────────────────────────────────────────────────

def test_env_file_with_byte_order_mark_and_export_prefix_is_read(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes("﻿YAAS_TEST_BOM_KEY=one\nexport YAAS_TEST_EXPORT_KEY='two'\n".encode("utf-8"))
    try:
        config._load_env(str(env))
        assert os.environ["YAAS_TEST_BOM_KEY"] == "one"
        assert os.environ["YAAS_TEST_EXPORT_KEY"] == "two"
    finally:
        os.environ.pop("YAAS_TEST_BOM_KEY", None)
        os.environ.pop("YAAS_TEST_EXPORT_KEY", None)


def test_env_file_in_another_encoding_does_not_stop_startup(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_bytes("YAAS_TEST_UTF16_KEY=one\n".encode("utf-16"))   # Windows PowerShell 5.1 `>` output
    config._load_env(str(env))
    assert "YAAS_TEST_UTF16_KEY" not in os.environ
    assert "UTF-8" in capsys.readouterr().out


def test_save_env_refuses_values_that_would_add_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=old\n", encoding="utf-8")
    with pytest.raises(ValueError):
        config.save_env({"GEMINI_API_KEY": "new\nYOUTUBE_API_KEY=injected"}, path=str(env))
    assert env.read_text(encoding="utf-8") == "GEMINI_API_KEY=old\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_save_env_keeps_a_marked_file_readable_and_leaves_no_temporary_file(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes("﻿GEMINI_API_KEY=old\nexport GROQ_API_KEY=g\n".encode("utf-8"))
    config.save_env({"GEMINI_API_KEY": "new", "GROQ_API_KEY": "h"}, path=str(env))
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines == ["GEMINI_API_KEY=new", "GROQ_API_KEY=h"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


# ── secrets in network errors ────────────────────────────────────────────────

def test_connection_errors_do_not_expose_query_string_keys(monkeypatch):
    def refuse(url, **kwargs):
        raise requests.ConnectionError("Max retries exceeded with url: /api/?key=SECRET123&q=moon")
    monkeypatch.setattr(http._session, "get", refuse)
    with pytest.raises(requests.ConnectionError) as error:
        http.get("https://pixabay.example/api/", params={"key": "SECRET123", "q": "moon"})
    assert "SECRET123" not in str(error.value) and "q=moon" in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__


def test_http_status_errors_do_not_expose_query_string_keys(monkeypatch):
    response = requests.Response()
    response.status_code = 403
    response.reason = "Forbidden"
    response.url = "https://www.googleapis.com/youtube/v3/channels?part=snippet&key=SECRET123"
    monkeypatch.setattr(http._session, "get", lambda url, **kwargs: response)
    with pytest.raises(requests.HTTPError) as error:
        http.get_json("https://www.googleapis.com/youtube/v3/channels")
    assert "SECRET123" not in str(error.value) and "403" in str(error.value)
    assert error.value.response is response


def test_ttl_cache_forgets_expired_entries(monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(http.time, "time", lambda: clock.now)
    calls = []

    @http.ttl_cache(10)
    def lookup(value):
        calls.append(value)
        return value * 2

    assert lookup(1) == 2 and lookup(1) == 2 and calls == [1]
    clock.now += 11
    assert lookup(2) == 4
    assert lookup.cache_size() == 1          # the expired entry for 1 was dropped
    lookup.cache_clear()
    assert lookup.cache_size() == 0


# ── image downloads ──────────────────────────────────────────────────────────

class _Reply:
    def __init__(self, body, content_type="image/jpeg", fail_after=None):
        self.headers = {"Content-Type": content_type}
        self._body, self._fail_after, self.closed = body, fail_after, False

    def iter_content(self, size):
        for index in range(0, len(self._body), 7):
            if self._fail_after is not None and index >= self._fail_after:
                raise requests.ConnectionError("connection dropped")
            yield self._body[index:index + 7]

    def close(self):
        self.closed = True


def _png():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (32, 18), (20, 40, 60)).save(buffer, "PNG")
    return buffer.getvalue()


def test_interrupted_or_fake_image_downloads_leave_nothing_behind(tmp_path, monkeypatch):
    target = tmp_path / "section_01_img01.jpg"
    replies = [_Reply(_png(), fail_after=14), _Reply(b"<html>not an image</html>")]
    monkeypatch.setattr(images, "get", lambda url, **kwargs: replies.pop(0))
    candidate = {"url": "https://example.test/large.jpg", "fallback_url": "https://example.test/small.jpg"}
    assert images.fetch(candidate, str(target)) is False
    assert list(tmp_path.iterdir()) == [] and not replies


def test_complete_image_download_is_saved_and_closed(tmp_path, monkeypatch):
    target = tmp_path / "section_01_img01.jpg"
    reply = _Reply(_png(), content_type="image/png")
    monkeypatch.setattr(images, "get", lambda url, **kwargs: reply)
    assert images.fetch({"url": "https://example.test/a.png"}, str(target)) is True
    assert [p.name for p in tmp_path.iterdir()] == ["section_01_img01.jpg"] and reply.closed
    assert target.read_bytes() == _png()


def test_oversized_image_download_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 20)
    monkeypatch.setattr(images, "get", lambda url, **kwargs: _Reply(_png()))
    assert images.fetch({"url": "https://example.test/a.png"}, str(tmp_path / "a.jpg")) is False
    assert list(tmp_path.iterdir()) == []


# ── Pexels key ───────────────────────────────────────────────────────────────

def test_rejected_pexels_key_is_skipped_without_erasing_the_saved_key(monkeypatch):
    monkeypatch.setattr(config, "PEXELS_API_KEY", "rejected-key")
    monkeypatch.setattr(stock, "_rejected_pexels_keys", set())
    monkeypatch.setattr(stock.requests, "get", lambda *args, **kwargs: SimpleNamespace(status_code=401))
    assert stock._pexels_ready()
    assert stock._pexels("moon", "landscape", 1) == []
    assert config.PEXELS_API_KEY == "rejected-key"      # Settings must not later save an empty key
    assert not stock._pexels_ready()
    monkeypatch.setattr(config, "PEXELS_API_KEY", "replacement-key")
    assert stock._pexels_ready()


# ── banned topics ────────────────────────────────────────────────────────────

def test_banned_topics_are_saved_one_per_line(tmp_path, monkeypatch):
    path = tmp_path / "banned_topics.txt"
    monkeypatch.setattr(researcher, "_BANNED_TOPICS_FILE", str(path))
    researcher.save_banned_topics(["Black holes\nMars", "  spaced   out ", "# not a topic", "Simulation Theory", "Mars", "Black holes Mars"])
    assert researcher.load_banned_topics() == researcher._BUILTIN_BANNED + ["Black holes Mars", "spaced out", "Mars"]
    assert [p.name for p in tmp_path.iterdir()] == ["banned_topics.txt"]


@pytest.mark.parametrize("body", [{"topics": "Mars"}, {"topics": [1, 2]}, {"topics": ["x" * 301]}, ["Mars"], {}])
def test_banned_topics_route_rejects_malformed_lists(body, tmp_path, monkeypatch):
    monkeypatch.setattr(researcher, "_BANNED_TOPICS_FILE", str(tmp_path / "banned_topics.txt"))
    response = gui.app.test_client().post("/api/save-banned-topics", json=body)
    assert response.status_code == 400 and response.get_json()["ok"] is False
    assert not (tmp_path / "banned_topics.txt").exists()


# ── research agent ───────────────────────────────────────────────────────────

RESEARCH = {"topic": "Tides", "video_title": "Why tides happen", "key_points": ["The Moon pulls water"], "tags": ["tides"]}


def test_research_requests_json_and_replaces_one_invalid_reply(monkeypatch):
    replies = ["Sure! Here is the plan you asked for.", json.dumps(RESEARCH)]
    seen = []

    def generate(prompt, **options):
        seen.append(options)
        return replies.pop(0)
    monkeypatch.setattr(researcher, "generate", generate)
    monkeypatch.setattr(researcher, "_grounding", lambda topic: {"facts": "", "sources": []})
    result = researcher.research_topic("Ocean science", topic_override="Tides")
    assert result["video_title"] == "Why tides happen"
    assert seen == [{"json_mode": True}, {"json_mode": True}]


def test_research_stops_after_one_replacement(monkeypatch):
    calls = []
    monkeypatch.setattr(researcher, "generate", lambda prompt, **options: calls.append(prompt) or "no json here")
    monkeypatch.setattr(researcher, "_grounding", lambda topic: {"facts": "", "sources": []})
    with pytest.raises(ValueError):
        researcher.research_topic("Ocean science", topic_override="Tides")
    assert len(calls) == 2


def test_stopping_during_research_is_not_retried(monkeypatch):
    calls = []

    def generate(prompt, **options):
        calls.append(prompt)
        raise InterruptedError("stopped")
    monkeypatch.setattr(researcher, "generate", generate)
    monkeypatch.setattr(researcher, "_grounding", lambda topic: {"facts": "", "sources": []})
    with pytest.raises(InterruptedError):
        researcher.research_topic("Ocean science", topic_override="Tides")
    assert len(calls) == 1


# ── job release ──────────────────────────────────────────────────────────────

@pytest.fixture
def clean_state():
    from studio.state import fresh_project_state, state
    yield state
    state.clear()
    state.update(fresh_project_state())


def test_job_is_released_even_when_the_final_checkpoint_fails(tmp_path, monkeypatch, clean_state):
    import sys
    from studio import preflight, runner

    def broken_checkpoint(*args, **kwargs):
        raise RuntimeError("dictionary changed size during iteration")

    def refuse(*args, **kwargs):
        raise ValueError("Choose a topic first.")
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(runner, "checkpoint_workspace", broken_checkpoint)
    monkeypatch.setattr(preflight, "validate_topic_steps", refuse)
    stream = sys.stdout
    # The worker normally owns a thread; keep its stage marker out of this one.
    import contextvars
    contextvars.copy_context().run(
        runner._run_pipeline, ["research"], "", "normal", "", False, "motion", False, None, None, True)
    assert clean_state["running"] is False            # otherwise every later run answers 409
    assert clean_state["error"] == "Choose a topic first."
    assert "dictionary changed size" in clean_state["workspace_warning"]
    assert sys.stdout is stream


def test_job_is_released_when_setup_fails_before_the_pipeline_starts(monkeypatch, clean_state):
    from studio import profile_context, runner

    def missing(profile_id):
        raise ValueError("That channel profile no longer exists.")
    monkeypatch.setattr(profile_context, "capture_profile_settings", missing)
    clean_state.update(running=True, job_profile_id="deleted", job_profile_name="Deleted")
    runner.run_pipeline_thread(["research"], profile_id="deleted")
    assert clean_state["running"] is False and clean_state["job_profile_id"] is None
    assert clean_state["error"] == "That channel profile no longer exists."


# ── framing ──────────────────────────────────────────────────────────────────

def test_pages_cannot_be_framed_by_other_sites():
    response = gui.app.test_client().get("/api/banned-topics", headers={"Host": "localhost:7070"})
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
