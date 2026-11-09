"""Archive continuation must preserve real audio and remain incomplete overall."""
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from renderers.hyperframes import rerender_archive as archive


def test_recovered_clock_copies_measured_events_without_inventing_raw_narration(tmp_path):
    source = archive.ROOT / "docs/samples/comparable-armour"
    data, _ = archive._load_archive(source)
    plan = data["scene-plan.json"]
    original = deepcopy(plan)
    mix = tmp_path / "decoded_mix.wav"
    mix.write_bytes(b"decoded archived soundtrack identity")
    timeline = archive._recover_clock(data["script.json"], plan, mix)
    for key in ("sections", "captions", "words", "fps", "width", "height", "duration"):
        assert timeline[key] == original[key]
    assert plan == original
    assert timeline["audio_origin"] == "decoded_archived_complete_mix"
    assert timeline["source_timeline_signature"] == plan["timeline_signature"]
    assert timeline["timeline_signature"] != plan["timeline_signature"]
    assert not (tmp_path / "narration.timings.json").exists()


def test_archive_local_routing_disables_environment_hosted_and_stock_credentials(tmp_path, monkeypatch):
    import config
    from apis import llm, openrouter, stock_video
    for provider in ("OPENROUTER", "GEMINI", "GROQ", "PEXELS", "PIXABAY"):
        monkeypatch.setenv(provider + "_API_KEY", "environment-credential-must-not-be-used")
    args = SimpleNamespace(local_model="qwen3.5:4b", local_url="http://127.0.0.1:11434")
    with config.profile_context(archive._settings(args, tmp_path)):
        assert config.LLM_ROUTE == "local"
        assert config.OLLAMA_MODEL == "qwen3.5:4b"
        assert stock_video.availability_status()["available"] is False
        assert not openrouter._api_key()
        assert llm.providers() == []
    args.local_url = "https://speech.platform.bing.com"
    with pytest.raises(RuntimeError, match="loopback|local Ollama|this computer"):
        archive._settings(args, tmp_path)


def test_archived_credits_feed_normal_media_adapter_and_existing_plan_without_inference(tmp_path, monkeypatch):
    import requests
    from agents import gemini_client, visual_director
    from video.renderers import html
    source = archive.ROOT / "docs/samples/comparable-armour"
    data, _ = archive._load_archive(source)
    original = deepcopy(data)

    def prohibited(*args, **kwargs):
        pytest.fail("Restoring verified museum metadata must not call a network or model")

    monkeypatch.setattr(requests.Session, "request", prohibited)
    monkeypatch.setattr(gemini_client, "generate", prohibited)
    image_map = archive._restore_museum_assets(source, data, tmp_path)
    credits = archive.demo._read(tmp_path / "credits.json")
    assert credits == data["credits.json"]["museum_images"]
    assert data == original and data["credits.json"]["audio"]
    mix = tmp_path / "archived_complete_mix.wav"
    mix.write_bytes(b"identity only: this test does not decode or render audio")
    timeline = archive._recover_clock(data["script.json"], data["scene-plan.json"], mix)
    plan = visual_director.plan_scenes(data["script.json"], timeline, mode="hybrid", output_dir=str(tmp_path),
                                      use_ai=False, image_map=image_map, use_project_photos=True)
    media_scenes = [scene for scene in plan["scenes"] if scene.get("media_layers")]
    assert len(media_scenes) == 2
    expected = {row["file"]: row for row in credits}
    for scene in media_scenes:
        for asset in [scene["image"], *scene["media_layers"]]:
            credit = expected[Path(asset["path"]).name]
            assert asset == {"path": asset["path"], "source": credit["source"], "credit": credit["credit"],
                             "source_url": credit["link"], "license": credit["license"]}
    prepared = html._validate_plan(plan)
    assert {asset["credit"].split(" · ")[0] for scene in prepared["scenes"] for asset in scene.get("media_layers", [])} == {
        row["title"] for row in data["museum-assets.json"]}

    # Reattaching the same verified image map repairs only missing attribution;
    # the normal adapter keeps selected photos, shot choices and measured clocks.
    missing = deepcopy(plan)
    for scene in missing["scenes"]:
        for asset in ([scene["image"]] if scene.get("image") else []) + scene.get("media_layers", []):
            asset.update(source="", credit="", source_url="", license="")
    visual_director._attach_images(missing, image_map, tmp_path)
    visual_director.validate_plan(missing)
    assert missing == plan


@pytest.mark.parametrize("corruption", ["missing_credit", "wrong_object", "wrong_license", "wrong_image"])
def test_archive_metadata_restoration_rejects_unbound_identity_before_writing(tmp_path, corruption):
    source = archive.ROOT / "docs/samples/comparable-armour"
    data, _ = archive._load_archive(source)
    if corruption == "missing_credit":
        data["credits.json"]["museum_images"].pop(0)
    elif corruption == "wrong_object":
        data["credits.json"]["museum_images"][0]["link"] = "https://www.metmuseum.org/art/collection/search/26849"
    elif corruption == "wrong_license":
        data["credits.json"]["museum_images"][0]["license"] = "Invented permission"
    else:
        data["museum-assets.json"][0]["sha256"] = "0" * 64
    with pytest.raises(archive.demo.ComparisonBlocked, match="identity|binding"):
        archive._restore_museum_assets(source, data, tmp_path)
    assert not (tmp_path / "credits.json").exists()
    assert not (tmp_path / "images").exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required for actual stream preservation.")
def test_remux_preserves_complete_original_audio_but_uses_new_video(tmp_path):
    for name, size, frequency in (("original", "32x48", "220"), ("candidate", "64x96", "880")):
        archive._ffmpeg(["-y", "-f", "lavfi", "-i", f"color=blue:size={size}:rate=30:duration=1",
                         "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=1",
                         "-c:v", "libx264", "-c:a", "aac", "-shortest", tmp_path / f"{name}.mp4"])
    original, candidate = tmp_path / "original.mp4", tmp_path / "candidate.mp4"
    expected = archive._pcm_signature(original)
    assert archive._pcm_signature(candidate) != expected
    assert archive._preserve_audio(candidate, original, expected) == expected
    assert archive._pcm_signature(candidate) == expected
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(candidate)], capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "64,96"


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg verification is required.")
def test_even_successful_visual_review_keeps_fresh_audio_requirements_unmet(tmp_path, monkeypatch):
    import requests
    from agents import render_review, visual_director
    from studio import runner
    from video import narrator
    from video.renderers import html
    source = archive.ROOT / "docs/samples/comparable-armour"
    baseline = archive.demo._read(source / "scene-plan.json")

    def prohibited(*args, **kwargs):
        pytest.fail("Archive continuation must not invoke TTS, mixing, or external HTTP")

    monkeypatch.setattr(narrator, "generate_narration", prohibited)
    monkeypatch.setattr(runner, "_mix_project_audio", prohibited)
    monkeypatch.setattr(requests.Session, "request", prohibited)
    monkeypatch.setattr(archive.demo, "_local_runtime_evidence", lambda: {"status": "installed"})
    calls = []

    def plan(script, timeline, **kwargs):
        calls.append("fresh_planning")
        assert timeline["words"] == baseline["words"]
        assert kwargs["use_ai"] is True and kwargs["image_map"]
        return deepcopy(baseline)

    def render(script, audio, output, candidate, **kwargs):
        calls.append("render")
        assert Path(audio).name == "archived_complete_mix.wav"
        target = Path(output) / "final_short.mp4"
        shutil.copyfile(source / "video.mp4", target)
        return str(target)

    monkeypatch.setattr(visual_director, "plan_scenes", plan)
    monkeypatch.setattr(visual_director, "has_model_visual_contribution", lambda plan: True)
    monkeypatch.setattr(html, "render_motion_video", render)
    monkeypatch.setattr(html, "prepare_motion_project", lambda *args: None)
    monkeypatch.setattr(render_review, "review_and_repair", lambda video, plan, *args, **kwargs:
                        (video, plan, {"passed": True, "vision": {"complete": True}}))
    args = SimpleNamespace(source=source, output=tmp_path / "new-run", local_model="qwen3.5:4b", local_url=None, max_repairs=0)
    assert archive.run(args) == 0
    result = archive.demo._read(args.output / "archive_rerender.json")
    assert calls == ["fresh_planning", "render"]
    assert result["visual_review_passed"] is True and result["comparison_complete"] is False
    assert result["new_narration"] is False and result["new_sound_mix"] is False
    assert result["requirements_unmet"] == ["fresh_complete_narration", "fresh_automatic_sound_mix"]
    assert result["stages"]["final_verification"]["audio_pcm_identical"] is True
    assert result["source"]["gates"] == archive.demo._read(source / "comparison.json")["gates"]
