"""The benchmark may append a short hold, but must never trim narration."""
import json
import hashlib
from pathlib import Path
import struct
import wave

import pytest

from renderers.hyperframes.comparable_demo import ComparisonBlocked, _pad_complete_narration
from video.timeline import audio_signature, build_timeline, script_signature


def _recorded_fixture(folder):
    folder.mkdir()
    script = {"title": "Timing fixture", "sections": [{"id": "one", "narration": "Complete recorded phrase."}]}
    frames = b"".join(struct.pack("<h", (i % 1000) - 500) for i in range(48000))
    audio = folder / "narration.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(frames)
    timing = {"version": 1, "duration": 1.0, "sample_rate": 48000,
              "audio_sha256": audio_signature(audio), "script_signature": script_signature(script),
              "sections": [{"id": "one", "start": 0, "duration": 1.0, "caption_timing": "word"}],
              "words": [{"word": "phrase", "start": .65, "end": .95, "section_id": "one"}]}
    (folder / "narration.timings.json").write_text(json.dumps(timing))
    (folder / "narration.srt").write_text("1\n00:00:00,100 --> 00:00:00,950\nComplete recorded phrase.\n")
    return script, frames, timing


def test_tail_preserves_all_pcm_and_word_positions(tmp_path):
    script, frames, original = _recorded_fixture(tmp_path / "recorded")
    output = tmp_path / "prepared"
    output.mkdir()
    audio, timing = _pad_complete_narration(tmp_path / "recorded", output, 1.5)
    with wave.open(audio, "rb") as stream:
        decoded = stream.readframes(stream.getnframes())
    assert decoded == frames + bytes(24000 * 2)
    assert timing["words"] == original["words"]
    assert timing["benchmark_tail"]["speech_samples_changed"] is False
    assert timing["benchmark_tail"]["source_audio_sha256"] == original["audio_sha256"]
    assert (output / "narration.srt").read_bytes() == (tmp_path / "recorded" / "narration.srt").read_bytes()
    timeline = build_timeline(script, audio, str(output), "shorts")
    assert timeline["audio_duration"] == 1.5
    assert timeline["sections"][-1]["speech_end"] == 1.5


@pytest.mark.parametrize("target", [.8, 1.8])
def test_too_short_or_excessive_padding_is_rejected(tmp_path, target):
    _recorded_fixture(tmp_path / "recorded")
    output = tmp_path / "prepared"
    output.mkdir()
    with pytest.raises(ComparisonBlocked, match="No speech was cut or retimed"):
        _pad_complete_narration(tmp_path / "recorded", output, target)
    assert not (output / "narration.wav").exists()


def test_local_diagnostics_preserve_response_and_do_not_save_inputs(tmp_path, monkeypatch):
    import config
    from apis import local_llm
    from renderers.hyperframes import comparable_demo as demo
    key = "sk-or-v1-" + "a" * 64
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", key)
    reply = {"done": True, "message": {"content": "A diagnostic echo of " + key}}
    body = {"model": "local:4b", "format": {"type": "object"},
            "messages": [{"content": "PRIVATE_REQUEST_TEXT", "images": ["PRIVATE_IMAGE_DATA"]}]}

    def request(base, endpoint, **kwargs):
        assert kwargs["body"] is body
        return reply

    monkeypatch.setattr(local_llm, "_request_json", request)
    with demo._capture_local_responses(tmp_path, lambda: "visual_planning") as audit:
        assert local_llm._request_json(None, "/api/chat", body=body) is reply
    assert local_llm._request_json is request
    assert reply["message"]["content"].endswith(key)
    text = (tmp_path / audit["files"][0]).read_text()
    assert key not in text and "PRIVATE_REQUEST_TEXT" not in text and "PRIVATE_IMAGE_DATA" not in text
    saved = json.loads(text)
    assert saved["native_json_schema"] is True and saved["image_count"] == 1
    assert saved["status"] == "received" and saved["truncated"] is False


def test_local_diagnostic_file_size_is_bounded_without_changing_response(tmp_path, monkeypatch):
    from apis import local_llm
    from renderers.hyperframes import comparable_demo as demo
    reply = {"done": True, "message": {"content": '"α"' * 10000}}
    monkeypatch.setattr(local_llm, "_request_json", lambda *args, **kwargs: reply)
    monkeypatch.setattr(demo, "MAX_RESPONSE_CAPTURE_BYTES", 4096)
    with demo._capture_local_responses(tmp_path, lambda: "review") as audit:
        assert local_llm._request_json(None, "/api/chat", body={"model": "local:4b"}) is reply
    file = tmp_path / audit["files"][0]
    assert file.stat().st_size <= 4096
    assert json.loads(file.read_text())["truncated"] is True
    assert len(reply["message"]["content"]) == 30000


def test_local_diagnostics_keep_transport_error_and_restore_adapter(tmp_path, monkeypatch):
    from apis import local_llm
    from renderers.hyperframes import comparable_demo as demo
    failure = local_llm.LocalLLMError("timeout", "Local generation timed out")

    def request(*args, **kwargs):
        raise failure

    monkeypatch.setattr(local_llm, "_request_json", request)
    with pytest.raises(local_llm.LocalLLMError) as caught:
        with demo._capture_local_responses(tmp_path, lambda: "visual_planning") as audit:
            local_llm._request_json(None, "/api/chat", body={"model": "local:4b"})
    assert caught.value is failure and local_llm._request_json is request
    saved = json.loads((tmp_path / audit["files"][0]).read_text())
    assert saved["status"] == "transport_failed"


def _replay_fixture(tmp_path, monkeypatch, count=1):
    import config
    from renderers.hyperframes import comparable_demo as demo
    monkeypatch.setattr(config, "LLM_ROUTE", "local")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "fixture:4b")
    monkeypatch.setattr(config, "CHANNEL_PRESET", "general")
    for name in demo.VISUAL_BINDING_FILES:
        (tmp_path / name).write_text(json.dumps({"timeline_signature": "fixture-timeline"}))
    report = {"started_at": "2026-01-01T00:00:00+00:00", "script_sha256": "fixture-script",
              "configuration": demo._configuration(), "stages": {"local_runtime": {
                  "status": "installed", "model": {"id": "fixture:4b", "digest": "a" * 64, "size": 123}}}}
    binding = demo._visual_response_binding(tmp_path, report, "motion")
    binding.update(shots_enabled=True, expected_batches=count,
                   expected_scene_ids=[f"scene-{n:03}" for n in range(1, count + 1)])
    folder = tmp_path / "model_responses"
    folder.mkdir()
    captures = []
    for n in range(1, count + 1):
        # Deliberately incomplete scene data: the replay layer checks identity,
        # while the production caller remains responsible for the full schema.
        reply = {"done": True, "done_reason": "stop", "model": "fixture:4b",
                 "message": {"content": json.dumps({"scenes": [{"id": f"scene-{n:03}"}]})}}
        text = json.dumps(reply, separators=(",", ":"))
        path = folder / f"response-{n:04}.json"
        demo._write(path, {"version": 1, "stage": "visual_planning", "status": "received",
                           "model": "fixture:4b", "native_json_schema": True, "truncated": False,
                           "image_count": 0, "response_text": text,
                           "redacted_response_json_sha256": hashlib.sha256(text.encode()).hexdigest(),
                           "started_at": report["started_at"], "finished_at": "2026-01-01T00:01:00+00:00"})
        captures.append({"file": path.relative_to(tmp_path).as_posix(), "sha256": demo._sha(path)})
    binding["response_captures"] = captures
    path = tmp_path / "binding.json"
    demo._write(path, binding)
    return demo, path, report


def test_visual_replay_consumes_exact_batches_and_restores_sound_vision_generation(tmp_path, monkeypatch):
    from agents import gemini_client, visual_director
    demo, binding, report = _replay_fixture(tmp_path, monkeypatch, count=2)
    actual_calls = []

    def ordinary(prompt, **kwargs):
        actual_calls.append(prompt)
        return "ordinary generation"

    monkeypatch.setattr(gemini_client, "generate", ordinary)
    with demo._replay_visual_responses(binding, tmp_path, report, "motion") as audit:
        for n in (1, 2):
            info = {}
            schema = visual_director._ai_schema([f"scene-{n:03}"], "motion", shots_enabled=True)
            content = gemini_client.generate("production prompt", json_mode=True, json_schema=schema, provider_info=info)
            assert json.loads(content)["scenes"][0]["id"] == f"scene-{n:03}"
            assert info["response_origin"] == "recorded_local_response" and info["new_inference"] is False
    assert actual_calls == [] and len(audit["consumed_batches"]) == 2
    assert gemini_client.generate is ordinary
    assert gemini_client.generate("automatic sound") == "ordinary generation"
    assert gemini_client.generate("local visual review") == "ordinary generation"
    assert actual_calls == ["automatic sound", "local visual review"]


@pytest.mark.parametrize("changed", ["input", "model", "schema_source", "capture"])
def test_visual_replay_refuses_changed_evidence(tmp_path, monkeypatch, changed):
    from agents import gemini_client
    demo, binding_path, report = _replay_fixture(tmp_path, monkeypatch)
    binding = demo._read(binding_path)
    if changed == "input":
        (tmp_path / "narration.wav").write_bytes(b"changed recording")
    elif changed == "model":
        report["stages"]["local_runtime"]["model"]["digest"] = "b" * 64
    elif changed == "schema_source":
        binding["visual_director_functions_sha256"]["_ai_schema"] = "changed"
        demo._write(binding_path, binding)
    else:
        (tmp_path / binding["response_captures"][0]["file"]).write_text("{}")
    ordinary = gemini_client.generate
    with pytest.raises(ComparisonBlocked, match="replay refused"):
        with demo._replay_visual_responses(binding_path, tmp_path, report, "motion"):
            pytest.fail("Changed evidence reached generation")
    assert gemini_client.generate is ordinary


@pytest.mark.parametrize("fault", ["unconsumed", "changed_schema", "extra_batch"])
def test_visual_replay_rejects_incomplete_or_different_batch_use(tmp_path, monkeypatch, fault):
    from agents import gemini_client, visual_director
    demo, binding, report = _replay_fixture(tmp_path, monkeypatch)
    ordinary = gemini_client.generate
    with pytest.raises(ComparisonBlocked, match="replay refused"):
        with demo._replay_visual_responses(binding, tmp_path, report, "motion"):
            if fault != "unconsumed":
                schema = visual_director._ai_schema(["scene-001"], "motion", shots_enabled=True)
                if fault == "changed_schema":
                    schema["additionalProperties"] = True
                gemini_client.generate("normal prompt", json_mode=True, json_schema=schema)
                if fault == "extra_batch":
                    gemini_client.generate("unexpected batch", json_mode=True, json_schema=schema)
    assert gemini_client.generate is ordinary


def test_recorded_content_still_faces_production_schema_validation(tmp_path, monkeypatch):
    import config
    from agents import gemini_client, visual_director
    from apis.openrouter import OpenRouterError
    demo, binding, report = _replay_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "SHOT_PLANNING_ENABLED", True)
    script = {"title": "Solar panels", "sections": [{"id": "one", "narration": "Sunlight reaches the solar panel."}]}
    timeline = {"duration": 2, "fps": 30, "width": 1080, "height": 1920,
                "sections": [{"id": "one", "start": 0, "duration": 2}]}
    plan = visual_director.plan_scenes(script, timeline, use_ai=False)
    scene = plan["scenes"][0]
    sources = {scene["id"]: {"narration": script["sections"][0]["narration"], "section": script["sections"][0]}}
    ordinary = gemini_client.generate
    with pytest.raises((OpenRouterError, ValueError)):
        with demo._replay_visual_responses(binding, tmp_path, report, "motion"):
            visual_director._request_design_impl([scene], sources, direction=plan["visual_direction"],
                                                 brief=plan["creative_brief"], mode="motion")
    assert gemini_client.generate is ordinary


def _checkpoint_fixture(tmp_path):
    import config
    from PIL import Image
    from renderers.hyperframes import comparable_demo as demo
    source = tmp_path / "original-run"
    source.mkdir()
    attempts = source / "voice_attempts"
    attempts.mkdir()
    script, _, timing = _recorded_fixture(attempts / "attempt-1")
    demo._write(source / "script.json", script)
    demo._write(source / "reference.json", {"target": {"width": 1080, "height": 1920, "fps": 30,
                                                       "duration_seconds": 1.5}})
    demo._write(source / "narration_attempts.json", [{
        "directory": "voice_attempts/attempt-1", "status": "recorded", "voice": config.VOICE_ID,
        "pitch": config.VOICE_PITCH, "rate": config.VOICE_RATE, "engine": "edge-tts",
        "engine_version": demo._package("edge-tts"), "script_signature": script_signature(script),
        "audio_sha256": timing["audio_sha256"], "duration_seconds": 1.0}])
    audio, _ = demo._pad_complete_narration(attempts / "attempt-1", source, 1.5)
    with config.profile_context({"SHORTS_WIDTH": 1080, "SHORTS_HEIGHT": 1920, "SHORTS_FPS": 30}):
        build_timeline(script, audio, str(source), "shorts")
    (source / "images").mkdir()
    image = source / "images/met-123.jpg"
    Image.new("RGB", (20, 30), "navy").save(image)
    demo._write(source / "museum_assets.json", [{"object_id": 123, "status": "acquired", "is_public_domain": True,
                                                "file": "images/met-123.jpg", "sha256": demo._sha(image)}])
    demo._write(source / "image_map.json", {"one": ["images/met-123.jpg"]})
    demo._write(source / "comparison_report.json", {"status": "rendered_needs_review", "comparison_complete": False})
    # Credentials and unrelated run files must never be swept into a package.
    (source / "private-settings.json").write_text('{"token":"not-for-export"}')
    return demo, source, script


def test_checkpoint_round_trip_reuses_exact_recordings_without_provider_calls(tmp_path, monkeypatch):
    from video import narrator
    demo, source, script = _checkpoint_fixture(tmp_path)
    original = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    checkpoint, restored = tmp_path / "saved", tmp_path / "resumed"
    manifest = demo._export_checkpoint(source, checkpoint)
    assert manifest["new_inference"] is False and manifest["approval_granted"] is False
    assert "private-settings.json" not in {row["path"] for row in manifest["files"]}
    assert original == {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    receipt = demo._restore_checkpoint(checkpoint, restored)
    assert receipt["new_inference_during_restore"] is False
    for row in manifest["files"]:
        assert (restored / row["path"]).read_bytes() == original[row["path"]]
    assert demo._read(restored / "checkpoint_source/comparison_report.json")["comparison_complete"] is False

    def no_new_voice(*args, **kwargs):
        pytest.fail("A verified restored voice must not call the narration service")

    monkeypatch.setattr(narrator, "generate_narration", no_new_voice)
    _, evidence = demo._record_narration(script, restored, {}, 1.5, resume=True, attempts=1)
    assert evidence["recording_reused"] is True and evidence["new_recording_attempts"] == 0
    assert (restored / "narration.wav").read_bytes() == original["narration.wav"]


@pytest.mark.parametrize("fault", ["tampered_bytes", "missing_voice", "traversal", "duplicate", "size", "symlink"])
def test_checkpoint_restore_refuses_unsafe_or_incomplete_data_without_publishing(tmp_path, fault):
    demo, source, _ = _checkpoint_fixture(tmp_path)
    checkpoint, restored = tmp_path / "saved", tmp_path / "resumed"
    manifest = demo._export_checkpoint(source, checkpoint)
    if fault == "tampered_bytes":
        path = checkpoint / "files/narration.wav"
        path.write_bytes(path.read_bytes() + b"changed")
    elif fault == "missing_voice":
        manifest["files"] = [row for row in manifest["files"] if row["path"] != "voice_attempts/attempt-1/narration.wav"]
    elif fault == "traversal":
        manifest["files"][0]["path"] = "../outside.json"
    elif fault == "duplicate":
        manifest["files"].append(dict(manifest["files"][0]))
    elif fault == "size":
        manifest["files"][0]["bytes"] = demo.MAX_CHECKPOINT_FILE_BYTES + 1
    else:
        path = checkpoint / "files/images/met-123.jpg"
        path.unlink()
        path.symlink_to(source / "images/met-123.jpg")
    demo._write(checkpoint / "checkpoint.json", manifest)
    with pytest.raises(ComparisonBlocked):
        demo._restore_checkpoint(checkpoint, restored)
    assert not restored.exists()


@pytest.mark.parametrize("file,change", [
    ("timeline.json", lambda value: value.update(words=[])),
    ("museum_assets.json", lambda value: value[0].update(sha256="0" * 64)),
    ("image_map.json", lambda value: value.update(one=["images/met-999.jpg"])),
    ("narration.timings.json", lambda value: value.update(words=[])),
])
def test_checkpoint_verifies_recording_and_asset_bindings_beyond_manifest_hashes(tmp_path, file, change):
    demo, source, _ = _checkpoint_fixture(tmp_path)
    checkpoint, restored = tmp_path / "saved", tmp_path / "resumed"
    manifest = demo._export_checkpoint(source, checkpoint)
    path = checkpoint / "files" / file
    content = demo._read(path)
    change(content)
    demo._write(path, content)
    row = next(row for row in manifest["files"] if row["path"] == file)
    row.update(bytes=path.stat().st_size, sha256=demo._sha(path))
    demo._write(checkpoint / "checkpoint.json", manifest)
    with pytest.raises(ComparisonBlocked):
        demo._restore_checkpoint(checkpoint, restored)
    assert not restored.exists()


def test_checkpoint_refuses_partial_exports_and_existing_destinations(tmp_path):
    demo, source, _ = _checkpoint_fixture(tmp_path)
    destination = tmp_path / "saved"
    (source / "voice_attempts/attempt-1/narration.wav").unlink()
    with pytest.raises(ComparisonBlocked, match="raw voice"):
        demo._export_checkpoint(source, destination)
    assert not destination.exists()
    destination.mkdir()
    marker = destination / "owned.txt"
    marker.write_text("keep this")
    with pytest.raises(ComparisonBlocked, match="new checkpoint directory"):
        demo._export_checkpoint(source, destination)
    with pytest.raises(ComparisonBlocked, match="new output directory"):
        demo._restore_checkpoint(source, destination)
    assert marker.read_text() == "keep this"


def test_checkpoint_cli_export_never_enters_pipeline(tmp_path, monkeypatch):
    import sys
    demo, source, _ = _checkpoint_fixture(tmp_path)
    monkeypatch.setattr(sys, "argv", ["comparable_demo.py", "--output", str(source),
                                      "--export-checkpoint", str(tmp_path / "saved")])
    monkeypatch.setattr(demo, "run", lambda args: pytest.fail("Export must not run the generation pipeline"))
    assert demo.main() == 0
