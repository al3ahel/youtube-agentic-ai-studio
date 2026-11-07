import os

import pytest
from PIL import Image

from video import thumbnail as T


@pytest.mark.parametrize("title, expected", [
    ("Black Holes: Why Time Itself Breaks Down #Shorts", "Why Time Itself *Breaks* Down"),
    ("What If The Moon Disappeared Tomorrow?", "What If The Moon *Disappeared*"),
    ("Quantum Immortality", "Quantum *Immortality*"),
    ("", ""),
])
def test_auto_text(title, expected):
    assert T.auto_text(title) == expected


def test_auto_text_is_short():
    text = T.auto_text("The Incredibly Long And Winding Story Of How Stars Are Born In Nebulae")
    assert len(text.split()) <= 5


def test_tokens_mark_highlights():
    assert T._tokens("Time *stops* here") == [("Time", False), ("stops", True), ("here", False)]
    assert T._tokens("*two words*") == [("two", True), ("words", True)]


@pytest.mark.parametrize("style", list(T.STYLES))
@pytest.mark.parametrize("portrait, size", [(False, (1280, 720)), (True, (1080, 1920))])
def test_render_every_style_in_both_shapes(tmp_path, style, portrait, size):
    out = T.render(style, str(tmp_path / f"{style}.jpg"), "Time *stops* here",
                   bg_path=None, brand="Test Channel", portrait=portrait)
    with Image.open(out) as im:
        assert im.size == size
    assert os.path.getsize(out) < 2 * 1024 * 1024      # YouTube's thumbnail limit


def test_preview_size(tmp_path):
    out = T.render("impact", str(tmp_path / "p.jpg"), "Hi", size=(270, 480), portrait=True)
    with Image.open(out) as im:
        assert im.size == (270, 480)


def test_unknown_style_falls_back(tmp_path):
    out = T.render("nope", str(tmp_path / "x.jpg"), "Hi")
    with Image.open(out) as im:
        assert im.size == (1280, 720)


def test_thumbnail_backgrounds_preserve_numeric_and_named_scene_order(tmp_path):
    from studio.thumbs import thumb_backgrounds
    first, second, last = [str(tmp_path / name) for name in ("first.png", "second.png", "last.png")]
    for filename in (first, second, last):
        Image.new("RGB", (8, 8)).save(filename)
    assert thumb_backgrounds({"10": [last], 2: [second], "1": [first]}) == [first, second, last]
    assert thumb_backgrounds({"opening": [first], "2": [second], "ending": [last, first],
                              "missing": None, "directory": str(tmp_path)}) == [first, second, last]
