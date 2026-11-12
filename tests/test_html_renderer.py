"""Security/timing boundaries and real subprocess cancellation for motion output."""
import copy
import json
import os
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
        (project / "vendor" / "gsap.min.js").write_text("/* fixture */", encoding="utf-8")
    monkeypatch.setattr(renderer, "_copy_vendor", copy_vendor)


def test_plan_is_data_and_original_is_unchanged(plan, audio, tmp_path, vendor):
    attack = '</script><script>window.attacked=true</script>&@@WIDTH@@'
    plan["title"] = attack
    plan["scenes"][0]["title"] = attack
    plan["scenes"][0]["items"][0]["label"] = '<img src=x onerror=alert(1)>'
    original = copy.deepcopy(plan)
    entry = renderer.prepare_motion_project(plan, str(audio), str(tmp_path))
    output = entry.read_text(encoding="utf-8")
    assert "<script>window.attacked" not in output
    assert "\\u003c/script\\u003e" in output
    assert "@@WIDTH@@" in output  # text is not recursively interpolated
    saved = json.loads((entry.parent / "project.json").read_text(encoding="utf-8"))
    assert saved["title"] == attack
    assert saved["audio"]["path"].endswith(".wav")
    assert (entry.parent / saved["audio"]["path"]).read_bytes() == audio.read_bytes()
    assert plan == original


def test_bounded_art_direction_and_scene_override_survive_preparation(plan, audio, tmp_path, vendor):
    plan["visual_direction"] = {"version": 1, "look": "editorial", "rhythm": "measured",
                                "rationale": "Clear source-led explanation <not executable>"}
    plan["scenes"][0].update(look="blueprint", rhythm="calm", composition="split")
    original = copy.deepcopy(plan)
    entry = renderer.prepare_motion_project(plan, str(audio), str(tmp_path))
    saved = json.loads((entry.parent / "project.json").read_text(encoding="utf-8"))
    assert saved["visual_direction"] == plan["visual_direction"]
    assert {key: saved["scenes"][0][key] for key in ("look", "rhythm", "composition")} == {
        "look": "blueprint", "rhythm": "calm", "composition": "split"}
    assert "<not executable>" not in entry.read_text(encoding="utf-8")
    assert plan == original


def test_composed_world_contract_and_palette_survive_preparation(plan, audio, tmp_path, vendor):
    plan["creative_brief"] = {"version": 1, "theme": "Energy", "premise": "Follow energy into storage",
                              "continuity": "Keep the same system visible", "palette": "forest"}
    plan["palette_colors"] = {"ink": "url(https://untrusted.invalid)"}
    scene = plan["scenes"][0]
    scene.update(graphics_version=2, staging={"version": 1, "world": "generic", "action": "connect", "camera": "follow"},
                 palette="sand", items=[{"label": "Solar panels", "symbol": "solar", "role": "subject"},
                                        {"label": "Battery storage", "symbol": "battery", "role": "support"}])
    original = copy.deepcopy(plan)
    entry = renderer.prepare_motion_project(plan, str(audio), str(tmp_path))
    saved = json.loads((entry.parent / "project.json").read_text(encoding="utf-8"))
    assert saved["scenes"][0]["staging"] == scene["staging"]
    assert saved["scenes"][0]["items"] == scene["items"]
    assert saved["palette_colors"] == renderer.PALETTES["forest"]
    assert saved["scenes"][0]["palette_colors"] == renderer.PALETTES["sand"]
    assert (entry.parent / "worlds.js").is_file()
    assert (entry.parent / "worlds.css").is_file()
    assert "untrusted.invalid" not in entry.read_text(encoding="utf-8")
    assert plan == original


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(staging={"version": 1, "world": "flood", "action": "invent_dam", "camera": "wide"}),
    lambda row: row["staging"].update(camera="javascript:alert(1)"),
    lambda row: row["staging"].update(code="alert(1)"),
    lambda row: row["items"][0].update(role="context"),
    lambda row: row["items"].append({"label": "Second subject", "symbol": "solar", "role": "subject"}),
    lambda row: row["items"][0].update(symbol="<svg onload=alert(1)>"),
    lambda row: row.update(palette="url(https://untrusted.invalid)"),
    lambda row: row.update(image={"path": "saved.png"}),
])
def test_composed_world_rejects_unsupported_code_roles_and_media(plan, mutation):
    scene = plan["scenes"][0]
    scene.update(graphics_version=2, staging={"version": 1, "world": "generic", "action": "connect", "camera": "wide"},
                 items=[{"label": "A circuit", "symbol": "circuit", "role": "subject"}])
    mutation(scene)
    with pytest.raises(ValueError):
        renderer._validate_plan(plan)


def test_legacy_object_scene_is_not_silently_upgraded(plan):
    plan["scenes"][0].update(graphics_version=1, items=[{"label": "Solar panel", "symbol": "solar"}])
    saved = renderer._validate_plan(plan)
    assert saved["scenes"][0]["graphics_version"] == 1
    assert "staging" not in saved["scenes"][0]
    assert "role" not in saved["scenes"][0]["items"][0]


@pytest.mark.parametrize("field,value", [("look", "url(https://example.com)"), ("look", []),
                                        ("rhythm", "random"), ("rhythm", 1),
                                        ("composition", "<script>"), ("composition", None)])
def test_scene_art_direction_rejects_unbounded_values(plan, field, value):
    plan["scenes"][0][field] = value
    with pytest.raises(ValueError, match=f"Unsupported scene {field}"):
        renderer._validate_plan(plan)


@pytest.mark.parametrize("direction", [None, [], {"version": True},
    {"version": 1, "look": "legacy", "rhythm": "calm"},
    {"version": 1, "look": "poster", "rhythm": "random"},
    {"version": 1, "look": "poster", "rhythm": "calm", "css": "body{display:none}"},
])
def test_art_direction_rejects_unbounded_values(plan, direction):
    plan["visual_direction"] = direction
    with pytest.raises(ValueError, match="Visual direction|Unsupported visual direction"):
        renderer._validate_plan(plan)


def test_legacy_art_direction_is_not_invented_for_saved_plans(plan):
    saved = renderer._validate_plan(plan)
    assert "visual_direction" not in saved
    assert all(key not in saved["scenes"][0] for key in ("look", "rhythm", "composition"))


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


def test_measured_word_captions_survive_preparation_as_safe_data(plan, audio, tmp_path, vendor):
    plan.update(caption_style="highlight", timing_source="measured_sections",
                sections=[{"id": "section", "start": 0, "duration": 2,
                           "narration": "Hello <world>", "caption_timing": "word"}],
                words=[{"word": "Hello", "start": .1, "end": .5, "section_id": "section"},
                       {"word": "<world>", "start": .8, "end": 1.2, "section_id": "section"}])
    original = copy.deepcopy(plan)
    entry = renderer.prepare_motion_project(plan, str(audio), str(tmp_path))
    saved = json.loads((entry.parent / "project.json").read_text(encoding="utf-8"))
    assert saved["caption_style"] == "highlight"
    assert saved["words"] == plan["words"]
    assert saved["sections"][0]["caption_timing"] == "word"
    assert "<world>" not in entry.read_text(encoding="utf-8")
    assert plan == original


@pytest.mark.parametrize("source,precision", [
    ("estimated_duration", "word"), ("legacy_srt", "phrase"),
    ("measured_sections", "estimated"), ("measured_sections", "phrase"), (None, "word"),
])
def test_unmeasured_word_events_cannot_become_highlights(plan, source, precision):
    plan.update(caption_style="highlight", sections=[{"id": "section", "start": 0,
                "duration": 2, "caption_timing": precision}],
                words=[{"word": "Hello", "start": .1, "end": .5, "section_id": "section"}])
    if source is not None:
        plan["timing_source"] = source
    result = renderer._validate_plan(plan)
    assert result["words"] == []
    assert result["captions"] == plan["captions"]


@pytest.mark.parametrize("change", [{"start": -1}, {"end": 10}, {"start": True},
                                      {"end": float("nan")}, {"word": "x" * 101}])
def test_malformed_word_timing_is_rejected(plan, change):
    word = {"word": "Hello", "start": .1, "end": .5, "section_id": "section"}
    plan["words"] = [{**word, **change}]
    with pytest.raises(ValueError):
        renderer._validate_plan(plan)


def test_caption_style_is_explicit_and_legacy_plans_stay_plain(plan):
    assert renderer._validate_plan(plan)["caption_style"] == "plain"
    for style in ("highlight", "plain", "off"):
        plan["caption_style"] = style
        assert renderer._validate_plan(plan)["caption_style"] == style
    plan["caption_style"] = "guess-word-timing"
    with pytest.raises(ValueError, match="captions"):
        renderer._validate_plan(plan)


def test_live_preview_files_stay_complete_while_replacement_is_copied(tmp_path, monkeypatch):
    source, destination = tmp_path / "new.js", tmp_path / "served.js"
    source.write_bytes(b"complete new script")
    destination.write_bytes(b"complete previous script")
    original = renderer.shutil.copy2
    observed = []

    def partial_copy(source_path, target_path):
        Path(target_path).write_bytes(b"partial")
        observed.append(destination.read_bytes())
        return original(source_path, target_path)

    monkeypatch.setattr(renderer.shutil, "copy2", partial_copy)
    renderer._atomic_copy(source, destination)
    assert observed == [b"complete previous script"]
    assert destination.read_bytes() == b"complete new script"
    assert not list(tmp_path.glob(".*.tmp"))


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
    saved = json.loads((entry.parent / "project.json").read_text(encoding="utf-8"))
    asset = saved["scenes"][0]["image"]
    assert asset["path"].startswith("assets/picture-")
    assert asset["credit"] == "NASA/JPL" and asset["license"] == "Public domain"
    with Image.open(entry.parent / asset["path"]) as result:
        assert result.size == (40, 80)
        assert not result.getexif()
    assert 'id="image-0"' in entry.read_text(encoding="utf-8")
    assert saved["timing_source"] == "legacy_srt" and saved["narration_reused"] is True
    assert plan == before


def test_graphics_motion_replaces_old_photo_composition_in_same_output(plan, audio, tmp_path, vendor):
    from PIL import Image
    picture = tmp_path / "old-project-photo.png"
    Image.new("RGB", (64, 36), "red").save(picture)
    old = copy.deepcopy(plan)
    old["use_project_photos"] = True
    old["scenes"][0]["image"] = {"path": str(picture)}
    entry = renderer.prepare_motion_project(old, str(audio), str(tmp_path))
    previous = json.loads((entry.parent / "project.json").read_text(encoding="utf-8"))
    old_asset = previous["scenes"][0]["image"]["path"]
    assert previous["use_project_photos"] is True
    assert 'id="image-0"' in entry.read_text(encoding="utf-8")

    plan["use_project_photos"] = False
    entry = renderer.prepare_motion_project(plan, str(audio), str(tmp_path))
    current = json.loads((entry.parent / "project.json").read_text(encoding="utf-8"))
    assert current["use_project_photos"] is False
    assert all("image" not in scene and "footage" not in scene for scene in current["scenes"])
    assert 'id="image-' not in entry.read_text(encoding="utf-8")
    assert old_asset not in entry.read_text(encoding="utf-8")
    assert current["scenes"][0]["template"] == "flow"
    assert current["scenes"][0]["beats"] == plan["scenes"][0]["beats"]


@pytest.mark.parametrize("asset", ["image", "footage"])
def test_graphics_only_motion_cannot_render_inherited_media(plan, asset):
    plan["use_project_photos"] = False
    plan["scenes"][0][asset] = {"path": "old.jpg" if asset == "image" else "old.mp4"}
    with pytest.raises(ValueError, match="does not use photos|animated graphics"):
        renderer._validate_plan(plan)


@pytest.mark.parametrize("value", ["false", 0, None])
def test_photo_policy_is_not_coerced_from_an_invalid_value(plan, value):
    plan["use_project_photos"] = value
    with pytest.raises(ValueError, match="true or false"):
        renderer._validate_plan(plan)


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
        Path(path).write_text("<svg onload='alert(1)'/>", encoding="utf-8")
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
    saved = json.loads((entry.parent / "project.json").read_text(encoding="utf-8"))
    with Image.open(entry.parent / saved["scenes"][0]["image"]["path"]) as result:
        assert getattr(result, "n_frames", 1) == 1
        red, green, blue = result.convert("RGB").getpixel((16, 12))
        assert red > 240 and green < 10 and blue < 10


def test_render_subprocess_forces_offline_flags_and_preserves_parent_config(tmp_path, monkeypatch):
    forced = ("DO_NOT_TRACK", "HYPERFRAMES_NO_TELEMETRY", "NO_COLOR",
              "HYPERFRAMES_NO_CAPTURE_DOTENV", "HYPERFRAMES_SKIP_SKILLS",
              "HYPERFRAMES_NO_UPDATE_CHECK", "HYPERFRAMES_NO_AUTO_INSTALL")
    inherited = {"HYPERFRAMES_BROWSER_PATH": str(tmp_path / "configured browser"),
                 "YAAS_RENDER_TEST_SETTING": "preserve-parent-value"}
    for name in forced:
        monkeypatch.setenv(name, "0")
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)
    names = [*forced, *inherited]
    child = f"import json, os; print(json.dumps({{key: os.environ.get(key) for key in {names!r}}}))"
    renderer._run_render([sys.executable, "-c", child], tmp_path, timeout=5,
                         progress=lambda message: None)
    received = json.loads((tmp_path / "render.log").read_text(encoding="utf-8"))
    assert {name: received[name] for name in forced} == dict.fromkeys(forced, "1")
    assert {name: received[name] for name in inherited} == inherited
    assert {name: os.environ[name] for name in forced} == dict.fromkeys(forced, "0")
    assert {name: os.environ[name] for name in inherited} == inherited


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
    assert "started" in (tmp_path / "render.log").read_text(encoding="utf-8")


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
