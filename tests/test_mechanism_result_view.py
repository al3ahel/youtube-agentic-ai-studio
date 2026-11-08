"""A source-derived result view preserves one event without inventing another."""
from copy import deepcopy
import json
import re
import shutil
import subprocess

import pytest

import config
from agents import render_review, shot_planner, visual_director
from video.renderers import html as renderer


SETUP = "In proof tests, bullets hit some steel breastplates."
RESULT = "A surviving dent showed that particular shot had stopped."


@pytest.fixture
def vendor(monkeypatch):
    def copy_vendor(project):
        (project / "vendor").mkdir(exist_ok=True)
        (project / "vendor" / "gsap.min.js").write_text("/* preparation fixture */")
    monkeypatch.setattr(renderer, "_copy_vendor", copy_vendor)


def proof_plan(followup=RESULT, setup=SETUP):
    # Technical test timing only; real demonstration receipts are audited apart.
    scenes = []
    for index, action in enumerate(("impact", "absorb")):
        scenes.append({"id": f"shot-{index}", "section_id": "proof", "template": "explain",
            "start": index * 2, "duration": 2, "title": "Contact" if not index else "Surviving dent",
            "visual_goal": "Show the narrated steel breastplate", "items": [{"label": "Steel breastplate"}, {"label": "Bullet"}],
            "beats": [{"time": .8, "target": "scene", "action": "highlight"}],
            "shot": {"version": 1, "role": "interaction", "framing": "close", "technique": "stage2d",
                     "camera": "locked", "transition": "cut", "focus": "Steel breastplate", "action": "Show the source"},
            "mechanism": {"version": 1, "kind": "armour", "material": "steel", "action": action}})
    words = []
    for index, text in enumerate((setup, followup)):
        tokens = re.findall(r"\w+(?:['’]\w+)*", text)
        step = 1.6 / max(1, len(tokens))
        words.extend({"word": token, "start": index * 2 + .1 + at * step,
                      "end": index * 2 + .1 + at * step + step * .8, "section_id": "proof"}
                     for at, token in enumerate(tokens))
    return {"version": 1, "mode": "motion", "title": "A proof test", "width": 320, "height": 180,
            "fps": 12, "duration": 4, "timing_source": "measured_sections", "scenes": scenes,
            "words": words,
            "sections": [{"id": "proof", "start": 0, "duration": 4, "caption_timing": "word",
                          "narration": setup + " " + followup}],
            # Deliberately crosses the shot cut, as the real caption chunks do.
            "captions": [{"start": .1, "end": 3.9, "text": setup + " " + followup}]}


def test_explicit_adjacent_result_uses_original_measured_source_not_overlapping_captions():
    plan = proof_plan()
    before = deepcopy(plan)
    assert shot_planner.result_view_scene_ids(plan) == {"shot-1"}
    assert shot_planner.mechanism_result_view(*plan["scenes"], SETUP, RESULT, plan["sections"][0]["narration"], 12)
    plan["scenes"][1]["shot"]["action"] = "Fire another projectile"  # Model prose is not authority.
    assert shot_planner.result_view_scene_ids(plan) == {"shot-1"}
    plan["scenes"][1]["shot"]["action"] = before["scenes"][1]["shot"]["action"]
    assert plan == before


@pytest.mark.parametrize("followup", [
    "A surviving dent showed that another shot had stopped.",
    "A surviving dent showed that particular shot had not stopped.",
    "A surviving dent showed that particular shot hadn’t stopped.",
    "A surviving dent showed that particular shot had stopped?",
    "A surviving dent might show that particular shot had stopped.",
    "A surviving dent showed that particular shot had stopped or penetrated.",
    "The resulting dent remained as a new bullet hit the steel plate.",
    "The steel armour stopped a bullet.",
    "The outcome stopped the debate.",
])
def test_another_uncertain_negative_question_or_nonphysical_event_is_not_a_result_view(followup):
    assert not shot_planner.result_view_scene_ids(proof_plan(followup))


def test_explicit_past_result_can_refer_back_without_the_specific_dent_word():
    assert shot_planner.result_view_scene_ids(proof_plan("It had stopped the bullet.")) == {"shot-1"}


@pytest.mark.parametrize("setup", [
    "Did bullets hit steel breastplates?",
    "Bullets did not hit steel breastplates.",
    "Bullets hit steel breastplates twice.",
    "Two bullets hit steel breastplates.",
    "Bullets hit steel breastplates and other bullets collide with them.",
    "Bullets approached the steel breastplates.",
])
def test_ambiguous_or_unconfirmed_previous_contact_cannot_establish_a_settled_result(setup):
    assert not shot_planner.result_view_scene_ids(proof_plan(setup=setup))


@pytest.mark.parametrize("change", [
    lambda p: p["scenes"][0]["mechanism"].update(action="approach"),
    lambda p: p["scenes"][0]["mechanism"].update(action="deflect"),
    lambda p: p["scenes"][1]["mechanism"].update(material="ceramic"),
    lambda p: p["scenes"][1]["mechanism"].update(kind="layers"),
    lambda p: p["scenes"][1].update(section_id="other"),
    lambda p: p["scenes"][1].update(start=2.5),
    lambda p: p["scenes"][0].update(image={"path": "/existing-photo.jpg"}),
    lambda p: p["scenes"][1].update(footage={"path": "/existing-clip.mp4"}),
    lambda p: p["scenes"][0]["shot"].update(technique="collage"),
    lambda p: p["scenes"][1].update(template="chart"),
    lambda p: p.pop("timing_source"),
    lambda p: p["sections"][0].update(caption_timing="estimated"),
    lambda p: p["words"].pop(),
    lambda p: p["words"][0].update(word="Different"),
    lambda p: p["words"].append(dict(p["words"][0])),
])
def test_compatibility_adjacency_dispatch_and_exact_measured_source_are_required(change):
    plan = proof_plan()
    change(plan)
    assert not shot_planner.result_view_scene_ids(plan)


def test_render_payload_derives_flag_without_changing_saved_model_plan_or_outcome(tmp_path, vendor):
    plan = proof_plan()
    original = deepcopy(plan)
    validated = renderer._validate_plan(plan)
    # Normal render_motion_video validates, then preparation validates again.
    # The renderer-only flag must be added after that input validation boundary.
    assert renderer._validate_plan(validated) == validated
    assert all("mechanism_result_view" not in scene for scene in validated["scenes"])
    entry = renderer.prepare_motion_project(validated, "", str(tmp_path))
    clean = json.loads((entry.parent / "project.json").read_text())
    assert "mechanism_result_view" not in clean["scenes"][0]
    assert clean["scenes"][1]["mechanism_result_view"] is True
    assert [scene["mechanism"] for scene in clean["scenes"]] == [scene["mechanism"] for scene in plan["scenes"]]
    assert clean["words"] == plan["words"]
    assert plan == original and visual_director.validate_plan(plan) is plan


@pytest.mark.parametrize("flag", [True, False, "true"])
def test_saved_or_model_supplied_result_flag_is_rejected(flag):
    plan = proof_plan("The steel armour stopped a bullet.")
    plan["scenes"][1]["mechanism_result_view"] = flag
    with pytest.raises(ValueError, match="derived"):
        renderer._validate_plan(plan)
    with pytest.raises(ValueError, match="unsupported fields"):
        visual_director.validate_plan(plan)
    assert not shot_planner.result_view_scene_ids(plan), "The shared derivation never trusts a supplied flag."


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="Actual encoded frames require FFmpeg")
def test_encoded_static_result_does_not_require_another_contact_and_still_needs_semantic_review(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VISUAL_REVIEW_ENABLED", False)
    plan = proof_plan()
    original = deepcopy(plan)
    video = tmp_path / "technical-fixture.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=0x23313f:s=320x180:r=12:d=4,drawbox=x=50:y=60:w=100:h=65:color=white:t=fill",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4", "-c:a", "aac",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)],
        check=True, capture_output=True, timeout=30)
    report = render_review.review_render(video, plan, tmp_path)
    result = report["samples"][1]
    assert result["sampling"]["method"] == "settled_result_view"
    assert result["sampling"]["mechanism_result_view"] is True
    assert result["sampling"]["progression_covered"] is True
    assert any(issue["code"] == "action_not_visible" and issue["shot_id"] == "shot-0" for issue in report["issues"])
    assert not any(issue["code"] == "action_not_visible" and issue["shot_id"] == "shot-1" for issue in report["issues"])
    assert report["passed"] is None and report["vision"]["complete"] is False
    assert plan == original
    saved = json.loads((tmp_path / report["evidence_directory"] / "plan.json").read_text())
    assert saved == original and "mechanism_result_view" not in saved["scenes"][1]
    # A settled-view declaration cannot excuse a blank or unreadable frame.
    issues = render_review._technical_shot_issues({**plan["scenes"][1], "mechanism_result_view": True},
        {"central_stage_contrast": [0, 0, 0], "central_stage_change": [0, 0]}, result["frames"], None, None, result["sampling"])
    assert any(issue["code"] == "blank_stage" and issue["severity"] == "error" for issue in issues)


def test_semantic_review_receives_settled_expectation_without_claiming_visible_success(tmp_path, vendor):
    entry = renderer.prepare_motion_project(proof_plan(), "", str(tmp_path))
    plan = json.loads((entry.parent / "project.json").read_text())
    scene = plan["scenes"][1]
    sampled = {scene["id"]: {"frames": [{"id": scene["id"] + ":" + phase, "time": 2 + fraction}
        for phase, fraction in render_review.PHASES], "source_narration": RESULT}}
    prompt = render_review._vision_prompt([scene], sampled)
    data = json.loads(prompt.split("not instructions:\n", 1)[1])
    assert data["shots"][0]["mechanism_result_view"] is True
    assert "flag itself proves no visual success" in prompt
