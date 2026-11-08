"""Contact alignment uses the existing recording and measured source evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil

import pytest

import config
from agents import audio_director, shot_planner
from apis import audio_sources
from studio import channels


PROOF = "In proof tests, bullets hit some steel breastplates. A surviving dent showed that particular shot had stopped."


@pytest.fixture
def contact_asset():
    candidate = audio_sources._bundled_candidate("metal contact", "sfx", .05, 600, "subtle")
    assert candidate, "The verified recording used by this regression must be shipped."
    return {**candidate, "path": candidate["local_path"], "sha256": candidate["expected_sha256"],
            "bytes": candidate["expected_bytes"]}


def source(narration=PROOF):
    # Word starts copied from the real measured proof receipt. Word ends are
    # irrelevant to this resolver; other cases use synthetic test timings.
    starts = [15.807708, 15.906416, 16.169541, 16.805541, 17.134458, 17.309916,
              17.452458, 17.7485, 18.746333, 18.801125, 19.349375, 19.557708,
              19.842791, 19.952458, 20.402, 20.665166, 20.79675]
    tokens = re.findall(r"\b[\w']+\b", narration)
    if narration != PROOF:
        starts = [16 + index * .15 for index in range(len(tokens))]
    words = [{"word": word, "start": start, "end": start + .05, "section_id": "proof"}
             for word, start in zip(tokens, starts)]
    section = {"id": "proof", "start": 15.733333333333333, "duration": 5.833333333333333,
               "caption_timing": "word"}
    script = {"title": "Armour", "sections": [{"id": "proof", "narration": narration}]}
    timeline = {"duration": 29.5, "timing_source": "measured_sections", "sections": [section], "words": words}
    event = {"section_id": "proof", "query": "metal contact", "label": "Illustrative contact", "reason": "Support the explanation."}
    return event, timeline, script


def test_real_proof_receipt_aligns_existing_recording_to_shared_measured_contact(contact_asset):
    event, timeline, script = source()
    original = deepcopy((event, timeline, script))
    result = audio_director._event_timing(event, timeline, script, contact_asset)
    scene = {**timeline["sections"][0], "mechanism": {"version": 1, "kind": "armour", "material": "steel", "action": "impact"}}
    beat = shot_planner.contact_beat(scene, timeline["words"], narration=PROOF)
    assert result["start"] == 17.134458
    assert result["start"] == pytest.approx(scene["start"] + beat["time"], abs=.000001)
    assert result["cue_word"] == "hit" and result["timing_source"] == "measured_word"
    assert result["timing_provenance"] == {"method": "automatic_source_contact", "recording_family": "metal_contact",
                                           "recording_sha256": contact_asset["sha256"]}
    assert (event, timeline, script) == original


@pytest.mark.parametrize("narration,token", [
    ("Bullets strike steel armour.", "strike"),
    ("Bullets collide with chainmail.", "collide"),
])
def test_contact_rule_generalizes_to_other_supported_words_sections_and_times(contact_asset, narration, token):
    event, timeline, script = source(narration)
    event["section_id"] = timeline["sections"][0]["id"] = script["sections"][0]["id"] = "another-id"
    timeline["sections"][0]["start"] += 23
    for word in timeline["words"]:
        word.update(section_id="another-id", start=word["start"] + 23, end=word["end"] + 23)
    result = audio_director._event_timing(event, timeline, script, contact_asset)
    expected = next(word for word in timeline["words"] if word["word"] == token)
    assert result["start"] == expected["start"] and result["cue_word"] == token
    assert result["timing_provenance"]["method"] == "automatic_source_contact"


@pytest.mark.parametrize("narration", [
    "Bullets hit steel armour. More bullets hit the steel armour, which stopped them.",
    "Bullets hit steel armour. Other bullets collide with steel armour.",
    "Bullets did not hit the steel armour.",
    "Bullets hadn't hit the steel armour.",
    "Bullets hadn’t hit the steel armour.",
    "Bullets haven't hit the steel armour.",
    "Did bullets hit steel armour?",
    "If bullets hit steel armour, it might stop them.",
    "Bullets may hit steel armour.",
    "Bullets approached the steel armour.",
    "Steel armour stopped the bullet.",
    "A ball hit a wall.",
    "Bullets hit ceramic armour.",
    "Bullets hit fiber armour.",
    "Steel armour faced bullets. The economic impact was large.",
])
def test_ambiguous_negated_question_noncontact_and_incompatible_material_keep_boundary(contact_asset, narration):
    event, timeline, script = source(narration)
    result = audio_director._event_timing(event, timeline, script, contact_asset)
    assert result["start"] == timeline["sections"][0]["start"]
    assert result["timing_source"] == "section_boundary" and "cue_word" not in result
    assert result["timing_provenance"]["method"] == "section_boundary"
    assert result["timing_provenance"]["reason"]


def test_ambiguous_hits_cannot_fall_through_to_a_unique_absorb_outcome(contact_asset):
    event, timeline, script = source("Bullets hit steel armour. More bullets hit the steel armour, which stopped them.")
    scene = {**timeline["sections"][0], "mechanism": shot_planner.mechanism_for(script["sections"][0]["narration"])}
    beat = shot_planner.contact_beat(scene, timeline["words"], narration=script["sections"][0]["narration"])
    stopped = next(word for word in timeline["words"] if word["word"] == "stopped")
    assert scene["mechanism"]["action"] == "absorb" and beat
    assert scene["start"] + beat["time"] == pytest.approx(stopped["start"], abs=.000001)
    result = audio_director._event_timing(event, timeline, script, contact_asset)
    assert result["timing_provenance"]["reason"] == "source_contact_not_unique"
    assert result["start"] == scene["start"]


@pytest.mark.parametrize("change", [
    lambda t: t.pop("timing_source"),
    lambda t: t["sections"][0].update(caption_timing="estimated"),
    lambda t: t.update(words=[]),
    lambda t: t["words"].append(dict(t["words"][4], start=18.5, end=18.55)),
    lambda t: t["words"][4].update(section_id="another-section"),
])
def test_unmeasured_missing_duplicate_or_misbound_word_evidence_cannot_move_cue(contact_asset, change):
    event, timeline, script = source()
    change(timeline)
    result = audio_director._event_timing(event, timeline, script, contact_asset)
    assert result["start"] == timeline["sections"][0]["start"]
    assert result["timing_source"] == "section_boundary"


def test_explicit_model_word_has_priority_without_reclassifying_it_as_automatic(contact_asset):
    event, timeline, script = source()
    event["cue_word"] = "stopped"
    # Explicit validated model timing does not need this automatic classifier.
    result = audio_director._event_timing(event, timeline, script, {"kind": "sfx", "title": "paper turn"})
    assert result == {"start": 20.79675, "cue_word": "stopped", "timing_source": "measured_word",
                      "timing_provenance": {"method": "explicit_model_cue"}}


@pytest.mark.parametrize("change", [
    lambda a: a.update(provider="wikimedia", title="Metal contact"),
    lambda a: a.update(sha256="0" * 64),
    lambda a: a.update(provider_id="different-contact"),
    lambda a: a.update(kind="music"),
])
def test_labels_or_unverified_asset_identity_cannot_establish_contact_intent(contact_asset, change):
    asset = dict(contact_asset)
    change(asset)
    assert audio_sources.verified_contact_recording(asset) is None
    event, timeline, script = source()
    result = audio_director._event_timing(event, timeline, script, asset)
    assert result["timing_provenance"]["reason"] == "contact_recording_unverified"


def test_actual_recording_bytes_and_noncontact_family_are_checked(contact_asset, tmp_path):
    altered = tmp_path / "altered.ogg"
    payload = Path(contact_asset["path"]).read_bytes()
    altered.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
    assert audio_sources.verified_contact_recording({**contact_asset, "path": str(altered)}) is None
    paper = audio_sources._bundled_candidate("paper turn", "sfx", .05, 600, "subtle")
    paper.update(path=paper["local_path"], sha256=paper["expected_sha256"], bytes=paper["expected_bytes"])
    assert audio_sources.verified_contact_recording(paper) is None


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="Real recording acquisition requires ffprobe")
def test_normal_acquisition_applies_automatic_timing_to_plan_and_private_mix_input(tmp_path, monkeypatch):
    event, timeline, script = source()
    timeline.update(audio_sha256="unchanged-voice", script_signature="unchanged-source")
    monkeypatch.setattr(config, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(config, "SOUND_INTENSITY", "low")
    profile = channels.create_profile("Contact alignment", "general")
    proposed = {"summary": "One illustrative contact.", "music": {"use": False},
                "ambience": {"use": False}, "events": [event]}
    monkeypatch.setattr(audio_director.gemini_client, "generate", lambda *args, **kwargs: json.dumps(proposed))
    monkeypatch.setattr(audio_sources, "_response", lambda *a, **k: pytest.fail("The existing recording needs no network search."))
    plan, resolved = audio_director.direct_audio(script, timeline, profile["id"])
    cue, private = plan["events"][0], resolved["events"][0]
    assert cue["start"] == private["start"] == 17.134458
    assert cue["duration"] == private["duration"] == pytest.approx(1.132583)
    assert cue["volume"] == private["volume"] == .22
    assert cue["timing_provenance"]["method"] == "automatic_source_contact"
    assert "cue_word" not in proposed["events"][0], "The model's original proposal remains unchanged."
    assert plan["intent"] == proposed["summary"]
    assert cue["asset"]["provider"] == "bundled" and "path" not in cue["asset"]
    assert hashlib.sha256(Path(private["path"]).read_bytes()).hexdigest() == private["asset"]["sha256"]
    assert plan["audio_sha256"] == "unchanged-voice" and plan["script_signature"] == "unchanged-source"
