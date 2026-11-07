"""Compatible section image filenames and safe cache rediscovery, offline."""
from pathlib import Path

import pytest

from video import stock


@pytest.mark.parametrize(("identifier", "expected"), [
    (0, "00"), (1, "01"), (12, "12"), ("1", "01"), ("01", "01"),
    ("12", "12"), ("intro", "intro"), ("chapter_2", "chapter_2"), ("Part-A", "Part-A"),
])
def test_section_key_keeps_numeric_names_and_supports_text_ids(identifier, expected):
    assert stock.section_key(identifier) == expected


@pytest.mark.parametrize("identifier", [
    "", ".", "..", "../secret", "a/b", "a\\b", "a*b", "a[1]", "a:1", "a?b",
    " leading", "trailing ", "-1", -1, True, None, 1.5, "x" * 81,
])
def test_invalid_section_ids_cannot_become_paths_or_globs(identifier, tmp_path):
    with pytest.raises(ValueError, match="Section IDs"):
        stock.section_key(identifier)
    with pytest.raises(ValueError, match="Section IDs"):
        stock.section_images(str(tmp_path), identifier)


def test_numeric_string_ids_rediscover_all_supported_image_extensions(tmp_path):
    expected = ["section_01_img01.jpg", "section_01_img02.jpeg", "section_01_img03.png", "section_01_img04.WEBP"]
    for name in expected + ["section_02_img01.png", "section_01_img05.svg", "unrelated.png"]:
        (tmp_path / name).write_bytes(b"cached picture")
    (tmp_path / "section_01_img06.jpg").mkdir()
    assert [Path(path).name for path in stock.section_images(str(tmp_path), "1")] == expected
    assert stock.section_images(str(tmp_path), "01") == stock.section_images(str(tmp_path), 1)


def test_text_ids_find_only_their_own_cached_files(tmp_path):
    for name in ["section_intro_img01.PNG", "section_intro_img02.jpeg", "section_introduction_img01.jpg"]:
        (tmp_path / name).write_bytes(b"cached picture")
    assert [Path(path).name for path in stock.section_images(str(tmp_path), "intro")] == [
        "section_intro_img01.PNG", "section_intro_img02.jpeg"]
    assert stock.section_images(str(tmp_path / "missing"), "intro") == []


def test_downloader_uses_the_same_safe_keys_without_network(tmp_path, monkeypatch):
    candidate = {"url": "https://example.test/image.jpg", "credit": {"source": "Fixture"}}
    monkeypatch.setattr(stock, "_candidates", lambda *args: [candidate])
    monkeypatch.setattr(stock, "_credits", [])
    monkeypatch.setattr(stock, "_used_urls", set())
    def fetch(value, path):
        Path(path).write_bytes(b"download fixture")
        return True
    monkeypatch.setattr(stock.image_apis, "fetch", fetch)
    first = stock._fetch_images("fixture", "intro", str(tmp_path))
    numeric = stock._fetch_images("fixture", "1", str(tmp_path))
    assert [Path(path).name for path in first + numeric] == ["section_intro_img01.jpg", "section_01_img01.jpg"]
    assert [row["file"] for row in stock._credits] == ["section_intro_img01.jpg", "section_01_img01.jpg"]


@pytest.mark.parametrize("ids", [["../bad"], [1, "01"], ["Intro", "intro"]])
def test_invalid_or_colliding_ids_do_not_clear_the_previous_cache(tmp_path, ids):
    directory = tmp_path / "images"
    directory.mkdir()
    previous = directory / "section_01_img01.png"
    previous.write_bytes(b"last good picture")
    with pytest.raises(ValueError, match="Section IDs"):
        stock.download_images({"sections": [{"id": value} for value in ids]}, str(tmp_path))
    assert previous.read_bytes() == b"last good picture"


def test_new_image_run_clears_prior_section_formats_without_deleting_other_files(tmp_path, monkeypatch):
    directory = tmp_path / "images"
    directory.mkdir()
    stale = ["section_01_img01.jpg", "section_02_img01.png", "section_intro_img01.JPEG", "section_04_img01.webp"]
    for name in stale + ["unrelated.png", "section_notes.txt"]:
        (directory / name).write_bytes(b"old")
    monkeypatch.setattr(stock, "_pexels_ready", lambda: True)
    monkeypatch.setattr(stock, "_fetch_images", lambda *args, **kwargs: [])
    monkeypatch.setattr(stock, "_credits", [])
    monkeypatch.setattr(stock, "_used_urls", set())
    stock.download_images({"sections": [{"id": "new-scene", "image_query": "fixture"}]}, str(tmp_path))
    assert not any((directory / name).exists() for name in stale)
    assert (directory / "unrelated.png").is_file()
    assert (directory / "section_notes.txt").is_file()
