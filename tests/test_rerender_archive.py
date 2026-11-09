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
