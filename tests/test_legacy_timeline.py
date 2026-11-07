"""Old voiceovers stay reusable, with honest cue/estimated timing provenance."""
import copy
import json
import math
import wave

import pytest

import config
from video.timeline import audio_signature, import_legacy_timeline, read_srt, script_signature


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VIDEO_FPS", 24)
    audio = tmp_path / "narration.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 64400)
    script = {"title": "A saved voiceover", "sections": [
        {"id": 1, "title": "Generation", "narration": "First sunlight reaches panels. The panels generate electricity."},
        {"id": 2, "title": "Storage", "narration": "Batteries store energy for later use."},
    ]}
    srt = tmp_path / "narration.srt"
    srt.write_text("1\n00:00:00,400 --> 00:00:02,200\nFirst sunlight reaches panels.\n\n"
                   "2\n00:00:02,400 --> 00:00:04,000\nThe panels generate electricity.\n\n"
                   "3\n00:00:04,800 --> 00:00:07,900\nBatteries store energy for later use.\n", encoding="utf-8")
    return tmp_path, audio, script, srt


def test_reuses_audio_and_original_cues_with_inferred_section_boundaries(legacy):
    output, audio, script, srt = legacy
    original = copy.deepcopy(script)
    audio_before, captions_before = audio.read_bytes(), srt.read_bytes()
    timeline = import_legacy_timeline(script, str(audio), str(output))
    assert timeline["timing_source"] == "legacy_srt"
    assert timeline["timing_precision"] == "phrase"
    assert timeline["narration_reused"] is True
    assert timeline["captions_source"] == "existing_srt"
    assert timeline["sections"][1]["start_frame"] == round(4.8 * 24)
    assert timeline["sections"][1]["start_boundary"] == "subtitle_cue"
    assert timeline["audio_duration"] == 8.05
    assert timeline["total_frames"] == math.ceil(8.05 * 24)
    assert sum(section["duration_frames"] for section in timeline["sections"]) == timeline["total_frames"]
    assert timeline["captions"] == read_srt(str(srt), 8.05)
    assert timeline["words"] == []
    assert all("speech_start" not in section for section in timeline["sections"])
    assert any("exact word alignment is unavailable" in warning for warning in timeline["warnings"])
    assert (audio.read_bytes(), srt.read_bytes(), script) == (audio_before, captions_before, original)
    assert not (output / "narration.timings.json").exists()
    assert json.loads((output / "timeline.json").read_text()) == timeline
    assert import_legacy_timeline(script, str(audio), str(output)) == timeline


def test_cue_that_crosses_sections_is_explicitly_interpolated(legacy):
    output, audio, script, srt = legacy
    script["sections"][0]["narration"] = "Alpha beta gamma."
    script["sections"][1]["narration"] = "Delta epsilon zeta."
    srt.write_text("1\n00:00:01,000 --> 00:00:07,000\nAlpha beta gamma. Delta epsilon zeta.\n")
    result = import_legacy_timeline(script, str(audio), str(output))
    assert result["sections"][1]["start"] == 4
    assert result["sections"][1]["start_boundary"] == "subtitle_interpolated"
    assert result["words"] == [] and result["timing_precision"] == "phrase"


def test_no_subtitles_uses_measured_audio_length_and_labelled_estimates(legacy):
    output, audio, script, srt = legacy
    srt.unlink()
    script["sections"][0]["narration"] = "Two words"
    script["sections"][1]["narration"] = "These are six words in total"
    result = import_legacy_timeline(script, str(audio), str(output))
    assert result["timing_source"] == "estimated_duration"
    assert result["timing_precision"] == "estimated"
    assert result["sections"][1]["start_frame"] == round(8.05 * 2 / 8 * 24)
    assert result["captions"] == [] and result["words"] == []
    assert result["captions_source"] == "none"
    assert all(section["timing_source"] == "estimated_duration" for section in result["sections"])
    assert any("estimated from the measured audio duration" in warning for warning in result["warnings"])


def test_unrelated_subtitles_keep_their_cues_but_do_not_control_sections(legacy):
    output, audio, script, srt = legacy
    srt.write_text("1\n00:00:02,000 --> 00:00:04,000\nAn unrelated discussion of medieval architecture.\n")
    result = import_legacy_timeline(script, str(audio), str(output))
    assert result["timing_source"] == "estimated_duration"
    assert result["captions"] == read_srt(str(srt))
    assert any("did not match" in warning for warning in result["warnings"])


def test_partial_subtitle_match_uses_available_anchors_without_fake_precision(legacy):
    output, audio, script, srt = legacy
    script["sections"] = [{"id": index, "narration": text} for index, text in enumerate([
        "Red green blue purple", "Oranges apples pears bananas", "North south east west",
    ])]
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nRed green blue purple\n\n"
                   "2\n00:00:03,000 --> 00:00:05,000\nMercury Venus Earth Mars\n\n"
                   "3\n00:00:06,000 --> 00:00:08,000\nNorth south east west\n")
    result = import_legacy_timeline(script, str(audio), str(output))
    assert result["timing_source"] == "legacy_srt"
    assert result["sections"][1]["start"] == 3
    assert result["sections"][1]["start_boundary"] == "duration_estimate"
    assert result["sections"][2]["start"] == 6
    assert any("Some section boundaries" in warning for warning in result["warnings"])


def test_unreadable_subtitle_file_does_not_require_recording_again(legacy):
    output, audio, script, srt = legacy
    srt.write_bytes(b"\xff\xff\x00")
    result = import_legacy_timeline(script, str(audio), str(output))
    assert result["timing_source"] == "estimated_duration"
    assert any("could not be imported" in warning for warning in result["warnings"])
    assert result["audio_sha256"] == audio_signature(audio)


def test_windows_utf16_subtitles_are_imported_with_their_original_timestamps(legacy):
    output, audio, script, srt = legacy
    text = srt.read_text()
    srt.write_text(text, encoding="utf-16")
    result = import_legacy_timeline(script, str(audio), str(output))
    assert result["timing_source"] == "legacy_srt"
    assert result["captions"][2]["start"] == 4.8


def test_legacy_import_cannot_bypass_stale_measured_metadata(legacy):
    output, audio, script, _ = legacy
    (output / "narration.timings.json").write_text(json.dumps({"script_signature": "stale"}))
    with pytest.raises(ValueError, match="script changed"):
        import_legacy_timeline(script, str(audio), str(output))
    assert not (output / "timeline.json").exists()


def test_import_identity_changes_with_subtitles_or_same_length_audio(legacy):
    output, audio, script, srt = legacy
    first = import_legacy_timeline(script, str(audio), str(output))
    srt.write_text(srt.read_text().replace("04,800", "04,900"))
    second = import_legacy_timeline(script, str(audio), str(output))
    assert second["timeline_signature"] != first["timeline_signature"]
    data = bytearray(audio.read_bytes())
    data[-2:] = b"\x01\x00"
    audio.write_bytes(data)
    third = import_legacy_timeline(script, str(audio), str(output))
    assert third["timeline_signature"] != second["timeline_signature"]
    assert third["audio_duration"] == second["audio_duration"]


def test_invalid_timestamp_is_not_treated_as_a_valid_cue(tmp_path):
    source = tmp_path / "captions.srt"
    source.write_text("1\n00:99:00,000 --> 00:99:01,000\nInvalid minutes.\n\n"
                      "2\n00:00:00,100 --> 00:00:01,000\nValid cue.\n")
    assert read_srt(str(source)) == [{"start": .1, "end": 1.0, "text": "Valid cue."}]


@pytest.mark.parametrize("with_subtitles", [True, False])
def test_repeat_import_rejects_changed_narration_before_overwriting_timeline(legacy, with_subtitles):
    output, audio, script, srt = legacy
    if not with_subtitles:
        srt.unlink()
    import_legacy_timeline(script, str(audio), str(output))
    target = output / "timeline.json"
    original_timeline, original_audio = target.read_bytes(), audio.read_bytes()
    original_srt = srt.read_bytes() if with_subtitles else None
    script["sections"][0]["narration"] += " This newly added sentence is not in the saved recording."
    with pytest.raises(ValueError, match="Restore the script.*Narration step"):
        import_legacy_timeline(script, str(audio), str(output))
    assert target.read_bytes() == original_timeline
    assert audio.read_bytes() == original_audio
    assert not (output / "timeline.json.tmp").exists()
    if with_subtitles:
        assert srt.read_bytes() == original_srt


def test_title_only_edits_keep_the_existing_recording_usable(legacy):
    output, audio, script, _ = legacy
    first = import_legacy_timeline(script, str(audio), str(output))
    script["title"] = "A revised video title"
    script["sections"][0]["title"] = "A revised section title"
    second = import_legacy_timeline(script, str(audio), str(output))
    assert second["script_signature"] == first["script_signature"]
    assert second["audio_sha256"] == first["audio_sha256"]
    assert second["title"] == "A revised video title"
    assert second["sections"][0]["title"] == "A revised section title"


def test_replaced_recording_can_be_imported_with_a_changed_script(legacy):
    output, audio, script, _ = legacy
    first = import_legacy_timeline(script, str(audio), str(output))
    script["sections"][0]["narration"] = "Different spoken words belong to this replacement recording."
    data = bytearray(audio.read_bytes())
    data[-2:] = b"\x01\x00"
    audio.write_bytes(data)
    second = import_legacy_timeline(script, str(audio), str(output))
    assert second["script_signature"] != first["script_signature"]
    assert second["audio_sha256"] != first["audio_sha256"]
    assert second["audio_duration"] == first["audio_duration"]
    assert json.loads((output / "timeline.json").read_text()) == second


def test_renaming_unchanged_audio_cannot_bypass_the_script_check(legacy):
    output, audio, script, _ = legacy
    import_legacy_timeline(script, str(audio), str(output))
    renamed = output / "renamed.wav"
    renamed.write_bytes(audio.read_bytes())
    script["sections"][0]["narration"] = "This text was changed."
    with pytest.raises(ValueError, match="narration changed"):
        import_legacy_timeline(script, str(renamed), str(output))


@pytest.mark.parametrize("damage", ["invalid_json", "invalid_utf8", "array", "missing_hash", "invalid_hash", "missing_script", "invalid_script"])
def test_malformed_existing_identity_fails_clearly_without_replacing_it(legacy, damage):
    output, audio, script, _ = legacy
    previous = import_legacy_timeline(script, str(audio), str(output))
    target = output / "timeline.json"
    if damage == "invalid_json":
        payload = b"{truncated"
    elif damage == "invalid_utf8":
        payload = b"\xff\xff"
    elif damage == "array":
        payload = b"[]"
    else:
        if damage == "missing_hash":
            previous.pop("audio_sha256")
        elif damage == "invalid_hash":
            previous["audio_sha256"] = "invalid"
        elif damage == "missing_script":
            previous.pop("script_signature")
        else:
            previous["script_signature"] = 123
        payload = json.dumps(previous).encode()
    target.write_bytes(payload)
    with pytest.raises(ValueError, match="saved narration timeline.*Restore.*Narration step"):
        import_legacy_timeline(script, str(audio), str(output))
    assert target.read_bytes() == payload
    assert not (output / "timeline.json.tmp").exists()


def test_measured_sidecar_remains_authoritative_over_old_import_metadata(legacy):
    output, audio, script, _ = legacy
    (output / "timeline.json").write_text("invalid old import metadata")
    (output / "narration.timings.json").write_text(json.dumps({
        "version": 1, "duration": 8.05, "script_signature": script_signature(script),
        "audio_sha256": audio_signature(audio), "words": [],
        "sections": [{"id": 1, "start": 0, "duration": 4.5, "caption_timing": "phrase"},
                     {"id": 2, "start": 4.5, "duration": 3.55, "caption_timing": "phrase"}],
    }))
    result = import_legacy_timeline(script, str(audio), str(output))
    assert result["timing_source"] == "measured_sections"
    assert result["sections"][1]["speech_start"] == 4.5
