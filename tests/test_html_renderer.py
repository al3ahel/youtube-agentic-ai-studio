"""Security/timing boundaries and real subprocess cancellation for motion output."""
import copy
import json
from pathlib import Path
import sys
import threading
import time
import wave

import pytest

from video.renderers import html as renderer


@pytest.fixture
def plan():
    return {
        "version": 1, "fps": 24, "width": 640, "height": 360, "duration": 2,
        "title": "Test film", "mode": "motion", "sections": [], "captions": [
            {"start": .1, "end": 1.8, "text": "A real timed caption"},
        ],
        "scenes": [{"id": "one", "section_id": "section", "template": "flow", "start": 0,
                    "duration": 2, "title": "How it works", "items": [{"label": "A"}, {"label": "B"}],
                    "beats": [{"time": .4, "target": "item-1", "action": "reveal"}]}],
    }


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "narration with spaces.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 16000)
    return path


@pytest.fixture
def vendor(monkeypatch):
    # Unit tests exercise preparation without requiring npm/network installation.
    def copy_vendor(project):
        (project / "vendor").mkdir(exist_ok=True)
        (project / "vendor" / "gsap.min.js").write_text("/* fixture */")
    monkeypatch.setattr(renderer, "_copy_vendor", copy_vendor)


def test_plan_is_data_and_original_is_unchanged(plan, audio, tmp_path, vendor):
    attack = '</script><script>window.attacked=true</script>&@@WIDTH@@'
    plan["title"] = attack
    plan["scenes"][0]["title"] = attack
    plan["scenes"][0]["items"][0]["label"] = '<img src=x onerror=alert(1)>'
    original = copy.deepcopy(plan)
    entry = renderer.prepare_motion_project(plan, str(audio), str(tmp_path))
    output = entry.read_text()
    assert "<script>window.attacked" not in output
    assert "\\u003c/script\\u003e" in output
    assert "@@WIDTH@@" in output  # text is not recursively interpolated
    saved = json.loads((entry.parent / "project.json").read_text())
    assert saved["title"] == attack
    assert saved["audio"]["path"].endswith(".wav")
    assert (entry.parent / saved["audio"]["path"]).read_bytes() == audio.read_bytes()
    assert plan == original


@pytest.mark.parametrize("value", [None, "a lot", float("nan"), float("inf"), True])
def test_chart_rejects_unsourced_or_non_numeric_values(plan, value):
    plan["scenes"][0]["template"] = "chart"
    plan["scenes"][0]["items"] = [{"label": "Claim", "value": value}]
    with pytest.raises(ValueError):
        renderer._validate_plan(plan)


def test_chart_preserves_supplied_zero_and_negative_values(plan):
    plan["scenes"][0]["template"] = "chart"
    plan["scenes"][0]["items"] = [{"label": "A", "value": 0}, {"label": "B", "value": -4.25}]
    result = renderer._validate_plan(plan)
    assert [item["value"] for item in result["scenes"][0]["items"]] == [0, -4.25]


@pytest.mark.parametrize("change", [
    {"start": .5}, {"duration": 1}, {"duration": 3},
    {"beats": [{"time": 3, "target": "item-0", "action": "reveal"}]},
])
def test_inconsistent_narration_timing_is_rejected(plan, change):
    plan["scenes"][0].update(change)
    with pytest.raises(ValueError):
        renderer._validate_plan(plan)


def test_media_paths_cannot_escape_download_directory(plan, audio, tmp_path, vendor, monkeypatch):
    output = tmp_path / "job"
    output.mkdir()
    private = tmp_path / "private.mp4"
    private.write_bytes(b"private recording")
    monkeypatch.setattr(renderer.config, "OUTPUT_DIR", str(output))
    plan["scenes"][0]["template"] = "footage"
    plan["scenes"][0]["footage"] = {"path": str(private)}
    with pytest.raises(ValueError, match="local media"):
        renderer.prepare_motion_project(plan, str(audio), str(output))
    assert not (output / "motion" / "index.html").exists()


def test_network_media_is_never_fetched(plan, audio, tmp_path, vendor):
    plan["scenes"][0]["template"] = "footage"
    plan["scenes"][0]["footage"] = {"path": "https://example.com/private.mp4"}
    with pytest.raises(ValueError, match="downloaded local"):
        renderer.prepare_motion_project(plan, str(audio), str(tmp_path))


def test_local_image_is_normalized_copied_and_attributed_without_mutating_plan(plan, audio, tmp_path, vendor):
    from PIL import Image
    source = tmp_path / "stock picture.jpg"
    picture = Image.new("RGB", (80, 40), "#408090")
    exif = Image.Exif()
    exif[274] = 6
    picture.save(source, exif=exif)
    plan["scenes"][0]["image"] = {"path": str(source), "source": "NASA", "credit": "NASA/JPL",
                                    "source_url": "https://images.nasa.gov/example", "license": "Public domain"}
    plan.update(timing_source="legacy_srt", timing_precision="phrase", narration_reused=True,
                warnings=["Section boundaries are inferred from subtitle text."])
    before = copy.deepcopy(plan)
    entry = renderer.prepare_motion_project(plan, str(audio), str(tmp_path))
    saved = json.loads((entry.parent / "project.json").read_text())
    asset = saved["scenes"][0]["image"]
    assert asset["path"].startswith("assets/picture-")
    assert asset["credit"] == "NASA/JPL" and asset["license"] == "Public domain"
    with Image.open(entry.parent / asset["path"]) as result:
        assert result.size == (40, 80)
        assert not result.getexif()
    assert 'id="image-0"' in entry.read_text()
    assert saved["timing_source"] == "legacy_srt" and saved["narration_reused"] is True
    assert plan == before


@pytest.mark.parametrize("kind", ["remote", "svg", "fake_raster", "outside"])
def test_scene_pictures_cannot_fetch_or_embed_unsupported_files(plan, audio, tmp_path, vendor, monkeypatch, kind):
    output = tmp_path / "job"
    output.mkdir()
    monkeypatch.setattr(renderer.config, "OUTPUT_DIR", str(output))
    if kind == "remote":
        path = "https://example.com/picture.jpg"
    elif kind == "outside":
        path = str(tmp_path / "private.jpg")
        Path(path).write_bytes(b"private")
    else:
        path = str(output / ("script.svg" if kind == "svg" else "script.png"))
        Path(path).write_text("<svg onload='alert(1)'/>")
    plan["scenes"][0]["image"] = {"path": path}
    with pytest.raises(ValueError):
        renderer.prepare_motion_project(plan, str(audio), str(output))
    assert not (output / "motion" / "index.html").exists()


def test_animated_image_is_imported_as_a_single_seekable_still(plan, audio, tmp_path, vendor):
    from PIL import Image
    source = tmp_path / "animated.webp"
    Image.new("RGB", (32, 24), "red").save(source, save_all=True,
        append_images=[Image.new("RGB", (32, 24), "blue")], duration=200, loop=0, lossless=True)
    plan["scenes"][0]["image"] = {"path": str(source)}
    entry = renderer.prepare_motion_project(plan, str(audio), str(tmp_path))
    saved = json.loads((entry.parent / "project.json").read_text())
    with Image.open(entry.parent / saved["scenes"][0]["image"]["path"]) as result:
        assert getattr(result, "n_frames", 1) == 1
        red, green, blue = result.convert("RGB").getpixel((16, 12))
        assert red > 240 and green < 10 and blue < 10


def test_render_subprocess_cancels_promptly(tmp_path):
    event = threading.Event()
    timer = threading.Timer(.25, event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(renderer.MotionRenderCancelled):
            renderer._run_render([sys.executable, "-u", "-c", "import time; print('started'); time.sleep(30)"],
                                 tmp_path, timeout=5, stop_event=event, progress=lambda message: None)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 4
    assert "started" in (tmp_path / "render.log").read_text()


def test_render_subprocess_has_a_real_timeout(tmp_path):
    with pytest.raises(renderer.MotionRenderError, match="time limit"):
        renderer._run_render([sys.executable, "-c", "import time; time.sleep(30)"],
                             tmp_path, timeout=.2, progress=lambda message: None)


def test_failed_render_does_not_replace_existing_video(plan, audio, tmp_path, vendor, monkeypatch):
    final = tmp_path / "final_video.mp4"
    final.write_bytes(b"last good render")
    monkeypatch.setattr(renderer, "_dependencies", lambda: ("node", Path("cli.mjs"), "ffprobe"))
    monkeypatch.setattr(renderer, "_probe", lambda *args: {
        "streams": [{"codec_type": "audio"}], "format": {"duration": "2"},
    })
    def fail(command, *args):
        assert isinstance(command, list)
        assert "--no-browser-gpu" in command
        Path(command[command.index("--output") + 1]).write_bytes(b"partial render")
        raise renderer.MotionRenderError("expected failure")
    monkeypatch.setattr(renderer, "_run_render", fail)
    with pytest.raises(renderer.MotionRenderError, match="expected failure"):
        renderer.render_motion_video({}, str(audio), str(tmp_path), plan, progress=lambda message: None)
    assert final.read_bytes() == b"last good render"
    assert not list(tmp_path.glob(".motion-render-*.mp4"))


def test_render_rejects_footage_that_would_end_before_scene(plan, audio, tmp_path, monkeypatch):
    footage = tmp_path / "short.mp4"
    footage.write_bytes(b"fixture")
    plan["scenes"][0]["template"] = "footage"
    plan["scenes"][0]["footage"] = {"path": str(footage), "in_point": .5}
    monkeypatch.setattr(renderer, "_dependencies", lambda: ("node", Path("cli.mjs"), "ffprobe"))
    def probe(path, *args):
        if path == footage:
            return {"streams": [{"codec_type": "video", "duration": "2.0"}], "format": {"duration": "2.0"}}
        return {"streams": [{"codec_type": "audio"}], "format": {"duration": "2.0"}}
    monkeypatch.setattr(renderer, "_probe", probe)
    with pytest.raises(renderer.MotionRenderError, match="too short"):
        renderer.render_motion_video({}, str(audio), str(tmp_path), plan)
    assert not (tmp_path / "motion").exists()
