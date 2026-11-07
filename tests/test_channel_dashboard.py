"""Fetched channel totals remain useful without a separate public API key."""
from unittest.mock import Mock

import pytest

import config
from apis import youtube_stats
from studio import channels, create_app
from studio.state import state


@pytest.fixture
def dashboard(monkeypatch):
    for key in ("running", "uploading", "visual_editing"):
        monkeypatch.setitem(state, key, False)
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "")
    monkeypatch.setattr(config, "YOUTUBE_CHANNEL_ID", "")
    news = channels.create_profile("News", "news")
    calm = channels.create_profile("Calm", "asmr")
    registry = channels._read_registry()
    selected = next(profile for profile in registry["profiles"] if profile["id"] == news["id"])
    selected["channel"] = {"id": "UC" + "n" * 22, "title": "Verified News Channel",
                           "subscribers": None, "views": 12345, "videos": 17,
                           "fetched_at": "2026-11-08T00:00:00+00:00"}
    channels._write_registry(registry)
    app = create_app()
    app.testing = True
    return app.test_client(), news["id"], calm["id"]


def test_saved_oauth_totals_need_no_extra_api_key_and_stay_in_their_profile(dashboard, monkeypatch):
    client, news_id, calm_id = dashboard
    network = Mock(side_effect=AssertionError("Snapshot must not call YouTube"))
    monkeypatch.setattr(youtube_stats, "channel_stats", network)

    result = client.get(f"/api/channel-stats?profile_id={news_id}")
    assert result.status_code == 200
    assert result.json == {"ok": True, "configured": False, "source": "saved_channel",
                           "title": "Verified News Channel", "subscribers": None,
                           "views": 12345, "videos": 17, "recent": [],
                           "fetched_at": "2026-11-08T00:00:00+00:00"}
    assert client.get(f"/api/channel-stats?profile_id={calm_id}").json == {"ok": False, "configured": False}
    assert client.get("/api/channel-stats").json == {"ok": False, "configured": False}
    network.assert_not_called()


def test_live_statistics_take_priority_when_available(dashboard, monkeypatch):
    client, news_id, _ = dashboard
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "fixture-key")
    calls = []
    def current():
        calls.append(config.YOUTUBE_CHANNEL_ID)
        return {"title": "Current news title", "subscribers": 100, "views": 15000, "videos": 18,
                "recent": [{"id": "example", "views": 200, "title": "Latest bulletin"}]}
    monkeypatch.setattr(youtube_stats, "channel_stats", current)
    result = client.get(f"/api/channel-stats?profile_id={news_id}").json
    assert result["source"] == "live" and result["configured"] is True
    assert result["views"] == 15000 and result["recent"][0]["views"] == 200
    assert calls == ["UC" + "n" * 22]


def test_live_failure_shows_a_labelled_snapshot_without_provider_details(dashboard, monkeypatch):
    client, news_id, _ = dashboard
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "fixture-key")
    monkeypatch.setattr(youtube_stats, "channel_stats", Mock(side_effect=RuntimeError("sensitive-provider-detail")))
    result = client.get(f"/api/channel-stats?profile_id={news_id}").json
    assert result["ok"] is True and result["source"] == "saved_channel"
    assert result["views"] == 12345 and result["subscribers"] is None
    assert "saved channel totals" in result["warning"]
    assert "sensitive-provider-detail" not in str(result)
