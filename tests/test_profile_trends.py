"""Trend workers use the requesting profile's inputs and cache by those inputs."""
from types import SimpleNamespace

import pytest

import config
from apis import trends


@pytest.fixture(autouse=True)
def clear_cache():
    trends.get_trends.cache_clear()
    yield
    trends.get_trends.cache_clear()


def test_trend_region_and_categories_follow_profile_into_workers_and_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "TRENDS_GEO", "US")
    monkeypatch.setattr(config, "ARXIV_CATEGORIES", ["cs.AI"], raising=False)

    def google(geo):
        calls.append(("google", geo, config.TRENDS_GEO))
        return [{"title": geo}]

    def arxiv(*, categories):
        calls.append(("arxiv", categories))
        return [{"title": ",".join(categories)}]

    monkeypatch.setattr(trends, "SOURCES", {"google": ("Google", google), "arxiv": ("arXiv", arxiv)})
    with config.profile_context({"TRENDS_GEO": "GB", "ARXIV_CATEGORIES": ["q-bio.NC"]}):
        british = trends.get_trends()
        assert trends.get_trends() is british
    assert british["sources"]["google"]["items"] == [{"title": "GB"}]
    assert british["sources"]["arxiv"]["items"] == [{"title": "q-bio.NC"}]
    # The worker has the base ContextVar state; explicit arguments are what
    # preserve GB instead of silently reading US from global configuration.
    assert ("google", "GB", "US") in calls
    with config.profile_context({"TRENDS_GEO": "US", "ARXIV_CATEGORIES": ["q-bio.NC"]}):
        american = trends.get_trends()
    with config.profile_context({"TRENDS_GEO": "US", "ARXIV_CATEGORIES": ["cs.AI"]}):
        different_subject = trends.get_trends()
    assert american is not british and different_subject is not american
    assert len(calls) == 6
    assert different_subject["sources"]["arxiv"]["items"] == [{"title": "cs.AI"}]
    trends.get_trends.cache_clear()
    trends.get_trends()
    assert len(calls) == 8


def test_arxiv_worker_uses_explicit_categories_in_network_query(monkeypatch):
    requests = []
    monkeypatch.setattr(config, "ARXIV_CATEGORIES", ["wrong.subject"], raising=False)
    def get(url, **kwargs):
        requests.append(kwargs["params"])
        return SimpleNamespace(content=b'<feed xmlns="http://www.w3.org/2005/Atom"/>')
    monkeypatch.setattr(trends, "get", get)
    assert trends.arxiv_latest(categories=("cs.AI", "q-bio.NC")) == []
    assert requests[0]["search_query"] == "cat:cs.AI OR cat:q-bio.NC"


def test_one_trend_source_failure_does_not_discard_other_sources(monkeypatch):
    def broken(geo):
        raise RuntimeError("source temporarily unavailable")
    monkeypatch.setattr(trends, "SOURCES", {
        "google": ("Google", broken),
        "wikipedia": ("Wikipedia", lambda: [{"title": "Calm rivers"}]),
    })
    result = trends.get_trends()["sources"]
    assert result["google"]["ok"] is False and result["google"]["items"] == []
    assert result["wikipedia"]["ok"] is True
    assert result["wikipedia"]["items"] == [{"title": "Calm rivers"}]
