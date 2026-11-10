"""Contact alignment uses the existing recording and measured source evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil

import pytest

import config
from agents import audio_director, shot_planner, visual_director
from apis import audio_sources
from studio import channels
from video.timeline import script_signature


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
def test_ambiguous_negated_question_noncontact_and_incompatible_material_omit_contact(contact_asset, narration):
    event, timeline, script = source(narration)
    result = audio_director._event_timing(event, timeline, script, contact_asset)
    assert result["omit"] is True and "start" not in result
    assert result["timing_provenance"]["method"] == "unsupported_contact_omitted"
    assert result["reason"]


def test_ambiguous_hits_cannot_fall_through_to_a_unique_absorb_outcome(contact_asset):
    event, timeline, script = source("Bullets hit steel armour. More bullets hit the steel armour, which stopped them.")
    scene = {**timeline["sections"][0], "mechanism": shot_planner.mechanism_for(script["sections"][0]["narration"])}
    beat = shot_planner.contact_beat(scene, timeline["words"], narration=script["sections"][0]["narration"])
    stopped = next(word for word in timeline["words"] if word["word"] == "stopped")
    assert scene["mechanism"]["action"] == "absorb" and beat
    assert scene["start"] + beat["time"] == pytest.approx(stopped["start"], abs=.000001)
    result = audio_director._event_timing(event, timeline, script, contact_asset)
    assert result["reason"] == "source_contact_not_unique"
    assert result["omit"] is True and "start" not in result


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
    assert result["omit"] is True and "start" not in result


def test_explicit_model_word_has_priority_without_reclassifying_it_as_automatic(contact_asset):
    event, timeline, script = source()
    event["cue_word"] = "stopped"
    # Explicit validated model timing does not need this automatic classifier.
    result = audio_director._event_timing(event, timeline, script, {"kind": "sfx", "title": "paper turn"})
    assert result == {"start": 20.79675, "cue_word": "stopped", "timing_source": "measured_word",
                      "timing_provenance": {"method": "explicit_model_cue"}}


def test_explicit_model_contact_cannot_bypass_the_actual_action_word(contact_asset):
    event, timeline, script = source()
    event["cue_word"] = "stopped"
    result = audio_director._event_timing(event, timeline, script, contact_asset)
    assert result["omit"] is True and result["reason"] == "explicit_contact_word_mismatch"
    event["cue_word"] = "hit"
    result = audio_director._event_timing(event, timeline, script, contact_asset)
    assert result["start"] == 17.134458 and result["cue_word"] == "hit"
    assert result["timing_provenance"]["method"] == "explicit_model_contact"


@pytest.mark.parametrize("narration,cue", [
    ("Mail armour uses linked rings. Steel plate armour can deflect some blows away from the body.", "rings"),
    ("When firearms spread, steel armour was hardened, thickened, or given a reinforcing layer over the breastplate.", "layer"),
])
def test_material_construction_is_not_an_impact_even_with_a_model_word(contact_asset, narration, cue):
    event, timeline, script = source(narration)
    event["cue_word"] = cue
    result = audio_director._event_timing(event, timeline, script, contact_asset)
    assert result["omit"] is True and "start" not in result


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


def visual_source():
    """A valid synthetic storyboard with the real proof-word receipt."""
    event, timeline, script = source()
    proof = {**timeline["sections"][0], "narration": PROOF}
    sections = [{"id": "opening", "start": 0, "duration": proof["start"],
                 "narration": "We examine historical armour.", "caption_timing": "estimated"},
                proof, {"id": "closing", "start": proof["start"] + proof["duration"],
                        "duration": timeline["duration"] - proof["start"] - proof["duration"],
                        "narration": "Protection had limits.", "caption_timing": "estimated"}]
    script["sections"] = [{"id": row["id"], "narration": row["narration"]} for row in sections]
    timeline.update(width=320, height=180, fps=30, sections=sections, captions=[],
                    script_signature=script_signature(script), timeline_signature="a" * 64, audio_sha256="voice")
    scenes = [{"id": f"shot-{index}", "section_id": row["id"], "start": row["start"], "duration": row["duration"],
               "template": "explain", "title": "Steel armour", "visual_goal": "Show the narrated steel armour",
               "items": [{"label": "Steel armour", "symbol": "armour"}, {"label": "Subject detail", "symbol": "concept"}],
               "beats": [{"time": 0, "target": "scene", "action": "reveal"}], "composition": "split",
               "shot": {"version": 1, "role": "detail", "framing": "close", "technique": "stage2d",
                        "camera": "locked", "transition": "cut", "focus": "Steel armour", "action": "Reveal the subject"}}
              for index, row in enumerate(sections)]
    scenes[1]["mechanism"] = {"version": 1, "kind": "armour", "material": "steel", "action": "impact"}
    scenes[1]["beats"].append({"time": 1.4, "target": "scene", "action": "highlight"})
    visual = {"version": 1, "mode": "motion", "title": "Armour", "width": 320, "height": 180,
              "fps": 30, "duration": timeline["duration"], "profile_id": "default",
              "timing_source": "measured_sections", "script_signature": timeline["script_signature"],
              "timeline_signature": timeline["timeline_signature"], "sections": deepcopy(sections),
              "words": deepcopy(timeline["words"]), "captions": [], "scenes": scenes}
    visual_director.validate_plan(visual)
    return event, timeline, script, visual


def saved_visual_source(change_clock=None):
    """Keep the producer's six-decimal clock across an actual JSON round trip."""
    event, timeline, script, visual = visual_source()
    if change_clock:
        change_clock(timeline)
    normalized, _ = visual_director._normalise_timeline(script, timeline)
    visual.update(normalized)
    for scene, section in zip(visual["scenes"], visual["sections"]):
        scene.update(start=section["start"], duration=section["duration"])
    visual = json.loads(json.dumps(visual))
    visual_director.validate_plan(visual)
    return event, timeline, script, visual


def test_saved_six_decimal_storyboard_retains_the_actual_visual_contact(contact_asset):
    event, timeline, script, visual = saved_visual_source()
    before = deepcopy((timeline, script, visual))
    assert visual["sections"][1]["start"] == 15.733333
    assert timeline["sections"][1]["start"] == 472 / 30
    assert visual["sections"][1]["start"] != timeline["sections"][1]["start"]
    assert audio_director._bound_visual_plan(visual, timeline, script, "default") is visual
    timing = audio_director._event_timing(event, timeline, script, contact_asset, visual)
    assert timing["timing_source"] == "visual_contact"
    assert timing["timing_provenance"]["scene_id"] == "shot-1"
    assert timing["start"] == visual["scenes"][1]["start"] + 1.4
    assert (timeline, script, visual) == before


def test_saved_storyboard_accepts_the_normalizers_final_section_rounding():
    def final_partial_frame(timeline):
        timeline["duration"] = 884 / 30
        timeline["sections"][1]["duration"] = 174 / 30
        timeline["sections"][2].update(start=646 / 30, duration=238 / 30)

    _, timeline, script, visual = saved_visual_source(final_partial_frame)
    # Rounding the start and then covering the final partial second can change
    # the last duration by more than half, but less than one, microsecond.
    difference = abs(visual["sections"][-1]["duration"] - timeline["sections"][-1]["duration"])
    assert .0000005 < difference < .000001
    assert audio_director._bound_visual_plan(visual, timeline, script, "default") is visual


def test_saved_storyboard_timing_tolerance_does_not_grow_with_absolute_time():
    def later_proof(timeline):
        timeline["duration"] += 6000
        timeline["sections"][0]["duration"] += 6000
        for section in timeline["sections"][1:]:
            section["start"] += 6000
        for word in timeline["words"]:
            word["start"] += 6000
            word["end"] += 6000

    _, timeline, script, visual = saved_visual_source(later_proof)
    assert audio_director._bound_visual_plan(visual, timeline, script, "default") is visual
    visual["sections"][1]["start"] += .000002
    assert audio_director._bound_visual_plan(visual, timeline, script, "default") is None


@pytest.mark.parametrize("field,delta", [
    ("start", .000002), ("duration", -.000002),
    ("start", 1 / 30), ("duration", -1 / 30),
])
def test_saved_storyboard_still_rejects_timing_drift(field, delta):
    _, timeline, script, visual = saved_visual_source()
    visual["sections"][1][field] += delta
    # These remain structurally valid within the renderer's one-frame bounds;
    # source binding must be substantially stricter than that layout tolerance.
    visual_director.validate_plan(visual)
    assert audio_director._bound_visual_plan(visual, timeline, script, "default") is None


@pytest.mark.parametrize("field", ["start", "duration"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_storyboard_binding_rejects_nonfinite_section_clocks(field, value):
    _, timeline, script, visual = saved_visual_source()
    # Even matching invalid values cannot establish a clock binding.
    visual["sections"][1][field] = timeline["sections"][1][field] = value
    assert audio_director._bound_visual_plan(visual, timeline, script, "default") is None


@pytest.mark.parametrize("change", [
    lambda v, t, s: v.update(profile_id="0" * 32),
    lambda v, t, s: t.update(audio_sha256="new-voice", timeline_signature="b" * 64),
    lambda v, t, s: v.update(script_signature="b" * 64),
    lambda v, t, s: s["sections"][1].update(narration="A different source sentence."),
    lambda v, t, s: v["words"][0].update(start=v["words"][0]["start"] + .000002),
    lambda v, t, s: t["sections"][1].update(id="another-section"),
    lambda v, t, s: t["sections"].reverse(),
    lambda v, t, s: t["sections"].pop(),
])
def test_saved_storyboard_keeps_source_owner_and_section_identity_guards(change):
    _, timeline, script, visual = saved_visual_source()
    change(visual, timeline, script)
    assert audio_director._bound_visual_plan(visual, timeline, script, "default") is None


def test_current_visual_contact_and_measured_word_share_the_actual_animation_beat(contact_asset):
    event, timeline, script, visual = visual_source()
    before = deepcopy(visual)
    timing = audio_director._event_timing(event, timeline, script, contact_asset, visual)
    assert timing["start"] == pytest.approx(17.133333333333333)
    assert timing["timing_source"] == "visual_contact"
    assert timing["cue_word"] == "hit"
    assert timing["timing_provenance"]["method"] == "automatic_visual_contact"
    assert timing["timing_provenance"]["measured_word_start"] == 17.134458
    assert timing["timing_provenance"]["scene_id"] == "shot-1"
    assert len(timing["timing_provenance"]["visual_plan_sha256"]) == 64
    assert visual == before


@pytest.mark.parametrize("change", [
    lambda p: p["scenes"][1]["mechanism"].update(action="reveal"),
    lambda p: p["scenes"][1]["beats"][-1].update(time=0),
    lambda p: p["scenes"][1].update(image={"path": "/fixture/plate.png"}),
])
def test_current_noncontact_or_misaligned_visual_does_not_receive_a_contact_sound(contact_asset, change):
    event, timeline, script, visual = visual_source()
    change(visual)
    visual_director.validate_plan(visual)
    timing = audio_director._event_timing(event, timeline, script, contact_asset, visual)
    assert timing["omit"] is True and timing["reason"] == "unique_aligned_visual_contact_unavailable"


@pytest.mark.parametrize("change", [
    lambda p: p.update(timeline_signature="old-timeline"),
    lambda p: p["sections"][1].update(narration="A stale source mentioning no impact."),
    lambda p: p["words"][4].update(start=17.5),
])
def test_stale_storyboard_cannot_suppress_contact_for_the_current_narration(contact_asset, change):
    event, timeline, script, visual = visual_source()
    visual["scenes"][1]["mechanism"]["action"] = "reveal"
    change(visual)
    timing = audio_director._event_timing(event, timeline, script, contact_asset, visual)
    assert timing["start"] == 17.134458
    assert timing["timing_provenance"]["method"] == "automatic_source_contact"


def test_foreign_channel_storyboard_is_not_supplied_to_the_sound_director():
    event, timeline, script, visual = visual_source()
    assert audio_director._bound_visual_plan(visual, timeline, script, "another-owner") is None
    assert audio_director._bound_visual_plan(visual, timeline, script, "default") is visual


def test_sound_prompt_exposes_only_source_eligible_contact_choices_and_actual_visual_beat():
    event, timeline, script, visual = visual_source()
    profile = {"name": "Armour", "preset": "general", "defaults": {}}
    prompt = audio_director._prompt(script, timeline, profile, [], visual_plan=visual)
    data = json.loads(prompt.split("PROJECT DATA:\n", 1)[1])
    choices = data["eligible_contact_cues"]
    assert len(choices) == 1
    assert choices[0]["section_id"] == "proof" and choices[0]["cue_word"] == "hit"
    assert choices[0]["measured_word_time"] == 17.134458
    assert choices[0]["visual_contact_time"] == pytest.approx(17.133333333333333)
    assert choices[0]["visual_scene_id"] == "shot-1"
    assert choices[0]["recording_query"] == "metal contact"
    visual["scenes"][1]["mechanism"]["action"] = "reveal"
    assert audio_director._eligible_contact_cues(script, timeline, visual) == []
    # Eligibility is context for the model, not a pre-filled accepted event.
    intent = audio_director.validate_intent({"events": []}, timeline, "general")
    assert intent["events"] == []


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


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="Real recording acquisition requires ffprobe")
def test_unsupported_construction_accent_cannot_consume_budget_before_supported_proof_hit(tmp_path, monkeypatch):
    event, timeline, script = source()
    construction = {"id": "construction", "start": 4.6, "duration": 4.966667,
                    "caption_timing": "word"}
    timeline["sections"].insert(0, construction)
    script["sections"].insert(0, {"id": "construction", "narration": "Mail armour uses linked rings."})
    timeline["words"] = [{"word": word, "start": 4.7 + index * .25, "end": 4.9 + index * .25,
                          "section_id": "construction"}
                         for index, word in enumerate(["Mail", "armour", "uses", "linked", "rings"])] + timeline["words"]
    timeline.update(audio_sha256="unchanged-voice", script_signature="unchanged-source")
    monkeypatch.setattr(config, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(config, "SOUND_INTENSITY", "low")
    profile = channels.create_profile("Supported contact only", "general")
    proposed = {"music": {"use": False}, "ambience": {"use": False}, "events": [
        {**event, "section_id": "construction"}, event]}
    monkeypatch.setattr(audio_director.gemini_client, "generate", lambda *args, **kwargs: json.dumps(proposed))
    monkeypatch.setattr(audio_sources, "_response", lambda *a, **k: pytest.fail("Existing recordings need no network search."))
    plan, resolved = audio_director.direct_audio(script, timeline, profile["id"])
    assert len(plan["events"]) == len(resolved["events"]) == 1
    assert plan["events"][0]["section_id"] == "proof"
    assert plan["events"][0]["start"] == resolved["events"][0]["start"] == 17.134458
    assert plan["omitted_events"][0]["section_id"] == "construction"
    assert plan["omitted_events"][0]["reason"] == "source_contact_not_unique"
    assert "4.6" not in json.dumps(plan["events"])


@pytest.fixture
def competing_effects(tmp_path, monkeypatch):
    """Two actual bundled proposals and a current, independently timed scene."""
    event, timeline, script, visual = saved_visual_source()
    timeline["sections"][0]["duration"] = 4.6
    overview = {"id": "overview", "start": 4.6, "duration": timeline["sections"][1]["start"] - 4.6,
                "narration": "Examine the structure of steel armour.", "caption_timing": "estimated"}
    timeline["sections"].insert(1, overview)
    script["sections"] = [{"id": row["id"], "narration": row["narration"]} for row in timeline["sections"]]
    timeline["script_signature"] = script_signature(script)
    visual["sections"] = deepcopy(timeline["sections"])
    visual["script_signature"] = timeline["script_signature"]
    visual["scenes"][0]["duration"] = 4.6
    transition_scene = deepcopy(visual["scenes"][0])
    transition_scene.update(id="overview-shot", section_id="overview", start=overview["start"], duration=overview["duration"])
    visual["scenes"].insert(1, transition_scene)
    monkeypatch.setattr(config, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    profile = channels.create_profile("Sparse contact selection", "general", defaults={"sound_intensity": "low"})
    visual["profile_id"] = profile["id"]
    visual_director.validate_plan(visual)
    assert audio_director._bound_visual_plan(visual, timeline, script, profile["id"]) is visual
    proposed = {"music": {"use": False}, "ambience": {"use": False}, "events": [
        {"section_id": "overview", "query": "paper turn", "label": "Overview transition", "reason": "Mark the overview."},
        event]}
    monkeypatch.setattr(audio_director.gemini_client, "generate", lambda *a, **k: json.dumps(proposed))
    monkeypatch.setattr(audio_sources, "_response", lambda *a, **k: pytest.fail("Existing recordings need no network search."))
    return profile, script, timeline, visual, proposed


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="Real recording acquisition requires ffprobe")
def test_later_valid_visual_contact_displaces_an_earlier_page_turn_in_the_normal_director(competing_effects):
    profile, script, timeline, visual, proposed = competing_effects
    before = deepcopy((script, timeline, visual, proposed))
    plan, resolved = audio_director.direct_audio(script, timeline, profile["id"], visual_plan=visual)
    assert len(plan["events"]) == len(resolved["events"]) == 1
    cue = plan["events"][0]
    assert cue["section_id"] == "proof" and cue["timing_source"] == "visual_contact"
    assert cue["start"] == resolved["events"][0]["start"] == pytest.approx(17.133333, abs=.000001)
    assert cue["volume"] == resolved["events"][0]["volume"] == .22
    assert cue["duration"] == resolved["events"][0]["duration"] == pytest.approx(1.132583)
    assert cue["asset"]["license"] == "CC BY 4.0" and cue["asset"]["provider"] == "bundled"
    assert hashlib.sha256(Path(resolved["events"][0]["path"]).read_bytes()).hexdigest() == cue["timing_provenance"]["recording_sha256"]
    assert [(row["section_id"], row["start"], row["reason"]) for row in plan["omitted_events"]] == [
        ("overview", 4.6, "effect_budget_exhausted")]
    assert plan["omitted_events"][0]["query"] == "paper turn"
    assert (script, timeline, visual, proposed) == before


@pytest.mark.parametrize("failure", ["noncontact_visual", "stale_visual", "noncontact_recording"])
@pytest.mark.skipif(not shutil.which("ffprobe"), reason="Real recording acquisition requires ffprobe")
def test_ineligible_or_unbound_contact_never_displaces_a_valid_transition(competing_effects, failure):
    profile, script, timeline, visual, proposed = competing_effects
    if failure == "noncontact_visual":
        next(row for row in visual["scenes"] if row.get("mechanism"))["mechanism"]["action"] = "reveal"
    elif failure == "stale_visual":
        visual["timeline_signature"] = "stale"
    else:
        proposed["events"][1].update(query="paper turn", label="Alleged metal impact")
    plan, resolved = audio_director.direct_audio(script, timeline, profile["id"], visual_plan=visual)
    assert [row["section_id"] for row in plan["events"]] == ["overview"]
    assert resolved["events"][0]["start"] == 4.6
    omitted = next(row for row in plan["omitted_events"] if row["section_id"] == "proof")
    assert omitted["reason"] == ("unique_aligned_visual_contact_unavailable" if failure == "noncontact_visual"
                                  else "effect_budget_exhausted")


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="Real recording acquisition requires ffprobe")
def test_too_short_contact_tail_does_not_consume_the_effect_budget(competing_effects, monkeypatch):
    profile, script, timeline, visual, proposed = competing_effects
    original = audio_director._event_timing

    def resolve(event, *args, **kwargs):
        timing = original(event, *args, **kwargs)
        # Exercise allocation with a resolver result too close to the audio end;
        # all source/recording eligibility checks above still run normally.
        if event["section_id"] == "proof" and timing.get("timing_source") == "visual_contact":
            timing["start"] = timeline["duration"] - .01
        return timing

    monkeypatch.setattr(audio_director, "_event_timing", resolve)
    plan, _ = audio_director.direct_audio(script, timeline, profile["id"], visual_plan=visual)
    assert [row["section_id"] for row in plan["events"]] == ["overview"]
    assert [(row["section_id"], row["reason"]) for row in plan["omitted_events"]] == [
        ("proof", "effect_duration_too_short")]


@pytest.mark.parametrize("unavailable", [False, True])
def test_contact_priority_never_inserts_an_unproposed_or_fallback_effect(competing_effects, monkeypatch, unavailable):
    profile, script, timeline, visual, proposed = competing_effects
    proposed["events"] = []
    if unavailable:
        def no_model(*args, **kwargs):
            raise RuntimeError("No local model response")
        monkeypatch.setattr(audio_director.gemini_client, "generate", no_model)
        monkeypatch.setattr(audio_sources, "acquire_audio", lambda *a, **k: None)
    plan, resolved = audio_director.direct_audio(script, timeline, profile["id"], visual_plan=visual)
    assert plan["events"] == resolved["events"] == plan["omitted_events"] == []
    assert plan["planner"]["status"] == ("local" if unavailable else "ai")
