"""Rendered evidence, honest missing-model states, and real repair verification."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

import config
from agents import render_review as review
from apis import local_llm, local_vision, openrouter


def plan():
    scenes = []
    for index in range(2):
        scenes.append({"id": f"shot-{index}", "section_id": "one", "start": index * 2, "duration": 2,
            "template": "explain", "title": "Sunlight reaches solar panels", "visual_goal": "Show the panel and circuit",
            "items": [{"label": "Solar panel", "symbol": "solar"}, {"label": "Circuit", "symbol": "circuit"}],
            "beats": [{"time": 0, "target": "scene", "action": "reveal"}], "composition": "split",
            "shot": {"version": 1, "role": "detail", "framing": "wide", "technique": "stage2d", "camera": "locked",
                     "transition": "cut", "focus": "Solar panel", "action": "Show the circuit"}})
    return {"version": 1, "mode": "motion", "width": 320, "height": 180, "fps": 12, "duration": 4,
            "title": "Solar power", "scenes": scenes, "captions": [], "words": [],
            "sections": [{"id": "one", "start": 0, "duration": 4, "narration": "Sunlight reaches the panel. Current flows through the circuit."}]}


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(config, "VISUAL_REVIEW_ENABLED", False)
    monkeypatch.setattr(config, "CHANNEL_PRESET", "general")
    monkeypatch.setattr(config, "LLM_ROUTE", "local")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "")


def encode(path, *, changed=False, audio=True, change_time=2):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Real encoded-review checks need FFmpeg/FFprobe")
    graphic = "color=c=0x23313f:s=320x180:r=12:d=4,drawbox=x=50:y=60:w=100:h=65:color=white:t=fill"
    if changed:
        graphic += f":enable='lt(t,{change_time})',drawbox=x=180:y=45:w=65:h=85:color=yellow:t=fill:enable='gte(t,{change_time})'"
    command = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", graphic]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4", "-c:a", "aac"]
    command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, check=True, capture_output=True, timeout=30)
    return path


def test_decodes_every_phase_and_measures_actual_audio_without_fake_approval(tmp_path):
    video = encode(tmp_path / "movie.mp4")
    value = review.review_render(video, plan(), tmp_path)
    assert value["coverage"]["technical_complete"] is True
    assert len(value["samples"]) == 2
    assert [row["phase"] for row in value["samples"][0]["frames"]] == ["setup", "action", "result"]
    assert all((tmp_path / value["evidence_directory"] / frame["file"]).is_file()
               for row in value["samples"] for frame in row["frames"])
    assert value["render"]["sha256"] == review._hash(video)
    assert value["passed"] is None and value["status"] == "needs_review"
    assert value["vision"]["complete"] is False
    assert value["audio"]["status"] == "measured_not_listened"
    assert -25 < value["audio"]["rms_dbfs"] < -15
    assert value["audio"]["perceptual_quality"] == "not_assessed"
    assert any(issue["code"] == "repeated_layout" for issue in value["issues"])
    assert json.loads((tmp_path / value["evidence_directory"] / "review.json").read_text()) == value


def test_missing_audio_is_a_real_failure(tmp_path):
    value = review.review_render(encode(tmp_path / "silent.mp4", audio=False), plan(), tmp_path)
    assert value["passed"] is False
    assert any(issue["code"] == "missing_audio" and issue["severity"] == "error" for issue in value["issues"])


def test_local_model_unavailable_never_becomes_semantic_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VISUAL_REVIEW_ENABLED", True)
    monkeypatch.setattr(openrouter, "_chat_json", lambda *a, **kw: pytest.fail("Local-only sent frames to a hosted model"))
    value = review.review_render(encode(tmp_path / "movie.mp4"), plan(), tmp_path)
    assert value["vision"]["failures"][0]["reason"] == "no_vision_model_selected"
    assert value["vision"]["status"] == "unavailable"
    assert value["passed"] is None


def vision_answer(scenes):
    return {"shots": [{"shot_id": scene["id"], "confidence": .9,
        "observations": [{"sample_id": f"{scene['id']}:{phase}",
                          "visible": f"A white rectangular object occupies the left of a blue field during the {phase} phase."}
                         for phase, _ in review.PHASES],
        "assessments": {key: "pass" for key in review.CHECKS}, "issues": []} for scene in scenes]}


def test_mixed_shot_ids_and_caption_parroting_are_rejected():
    scenes = plan()["scenes"]
    answer = vision_answer(scenes)
    answer["shots"][0]["observations"][0]["sample_id"] = "shot-1:setup"
    with pytest.raises(ValueError, match="mixed shot identities"):
        review._check_observations(answer, scenes)
    answer = vision_answer(scenes)
    answer["shots"][0]["observations"][0]["visible"] = scenes[0]["title"]
    with pytest.raises(ValueError, match="repeated supplied text"):
        review._check_observations(answer, scenes)


def test_low_confidence_and_uncertain_model_cannot_approve():
    scenes = plan()["scenes"]
    answer = vision_answer(scenes)
    answer["shots"][0]["confidence"] = .7
    answer["shots"][1]["assessments"]["motion"] = "uncertain"
    result = review._check_observations(answer, scenes)
    assert all(row["reliable"] is False for row in result["shots"])
    answer = vision_answer(scenes)
    answer["shots"][0]["observations"][0]["visible"] = "Unknown objects are shown; I cannot identify the subject in this frame."
    assert review._check_observations(answer, scenes)["shots"][0]["reliable"] is False


def test_high_confidence_uncertainty_and_evidenced_error_are_not_reclassified_as_pass():
    # A validator boundary fixture, not a substituted review of any real film.
    # The retained local response exhibited this combination: recognized
    # objects, 0.95 confidence, uncertain categories, and an error issue.
    scenes = plan()["scenes"][:1]
    answer = vision_answer(scenes)
    row = answer["shots"][0]
    row["confidence"] = .95
    row["assessments"] = {key: "uncertain" for key in review.CHECKS}
    row["issues"] = [{"category": "subject_relevance", "severity": "error",
        "message": "The sampled objects do not establish the claimed physical event.",
        "samples": ["shot-0:action", "shot-0:result"], "repair": "none",
        "defect": "source_mismatch", "expected": "The source calls for a recognizable solar panel.",
        "observed": row["observations"][1]["visible"]}]
    original = deepcopy(answer)
    checked = review._check_observations(answer, scenes)["shots"][0]
    assert checked["confidence"] == .95 and checked["reliable"] is False
    assert checked["assessments"] == {key: "uncertain" for key in review.CHECKS}
    assert len(checked["observations"]) == 3 and checked["issues"] == row["issues"]
    assert answer == original


def evidenced_failure(scenes):
    answer = vision_answer(scenes)
    for row in answer["shots"]:
        row["assessments"]["subject_relevance"] = "fail"
        row["issues"] = [{"category": "subject_relevance", "severity": "error",
            "message": "The visible rectangle lacks the solar cells required to identify the panel.",
            "samples": [f"{row['shot_id']}:action"], "repair": "source_direction",
            "defect": "missing_subject", "expected": "The source requires an identifiable solar panel.",
            "observed": row["observations"][1]["visible"]}]
    return answer


def test_captured_affirmative_failure_cannot_become_repair_authority():
    # The exact contradictory message from the 2026-11-09 scene-007 receipt.
    # Supplying otherwise well-formed defect fields cannot rescue its polarity.
    scenes = plan()["scenes"][:1]
    answer = evidenced_failure(scenes)
    answer["shots"][0]["issues"][0]["message"] = (
        "The visible subjects (bullet, breastplate) and their actions (approach, impact, deformation) "
        "are fully relevant to the source narration 'In proof tests, bullets hit some steel breastplates'.")
    original = deepcopy(answer)
    with pytest.raises(ValueError, match="affirmative explanation"):
        review._check_observations(answer, scenes)
    assert answer == original


@pytest.mark.parametrize("defect_change, error", [
    ({"defect": "cropped_subject"}, "assessment category"),
    ({"observed": "The scene shows only a blue sky and no panel."}, "cited visible evidence"),
    ({"observed": "A white rectangular object occupies the left of a blue field during the result phase."}, "cited visible evidence"),
    ({"expected": "A white rectangular object occupies the left of a blue field during the action phase."}, "distinguish expectation"),
])
def test_failure_requires_specific_evidence_from_its_own_cited_sample(defect_change, error):
    scenes = plan()["scenes"][:1]
    answer = evidenced_failure(scenes)
    answer["shots"][0]["issues"][0].update(defect_change)
    with pytest.raises(ValueError, match=error):
        review._check_observations(answer, scenes)


def test_failure_without_defect_evidence_remains_untrusted_but_clean_passes_still_work():
    scenes = plan()["scenes"][:1]
    answer = evidenced_failure(scenes)
    for key in ("defect", "expected", "observed"):
        del answer["shots"][0]["issues"][0][key]
    original = deepcopy(answer)
    with pytest.raises(ValueError, match="lacks explicit defect evidence"):
        review._check_observations(answer, scenes)
    assert answer == original
    assert review._check_observations(vision_answer(scenes), scenes)["shots"][0]["reliable"] is True


def test_single_uncertain_criterion_is_not_upgraded_by_error_severity():
    scenes = plan()["scenes"][:1]
    answer = evidenced_failure(scenes)
    answer["shots"][0]["assessments"]["subject_relevance"] = "uncertain"
    original = deepcopy(answer)
    row = review._check_observations(answer, scenes)["shots"][0]
    assert row["assessments"] == {"subject_relevance": "uncertain", "composition": "pass", "motion": "pass"}
    assert row["reliable"] is False
    assert answer == original


def test_legacy_warning_remains_readable_without_invented_defect_evidence():
    scenes = plan()["scenes"][:1]
    answer = vision_answer(scenes)
    answer["shots"][0]["assessments"]["motion"] = "uncertain"
    answer["shots"][0]["issues"] = [{"category": "repetition", "severity": "warning",
        "message": "The samples are similar; intended motion cannot be determined.",
        "samples": ["shot-0:setup", "shot-0:action"], "repair": "none"}]
    original = deepcopy(answer)
    row = review._check_observations(answer, scenes)["shots"][0]
    assert row["reliable"] is False
    assert all(row["issues"][0][key] == "" for key in ("defect", "expected", "observed"))
    assert answer == original


def test_pass_with_same_category_error_is_rejected_instead_of_rewritten():
    scenes = plan()["scenes"][:1]
    answer = evidenced_failure(scenes)
    answer["shots"][0]["assessments"]["subject_relevance"] = "pass"
    original = deepcopy(answer)
    with pytest.raises(ValueError, match="both passed and reported an error"):
        review._check_observations(answer, scenes)
    assert answer == original


def test_positive_clause_with_an_explicit_defect_is_not_blanket_rejected():
    scenes = plan()["scenes"][:1]
    answer = evidenced_failure(scenes)
    answer["shots"][0]["issues"][0]["message"] = (
        "The rectangle is fully relevant to the topic, but lacks the solar cells required to identify the subject.")
    row = review._check_observations(answer, scenes)["shots"][0]
    assert row["reliable"] is True and row["assessments"]["subject_relevance"] == "fail"


def test_invalid_evidence_gets_one_new_assessment_without_rewriting_the_rejected_response(tmp_path, monkeypatch):
    original = plan()
    monkeypatch.setattr(config, "VISUAL_REVIEW_ENABLED", True)
    requests, returned = [], []

    def observations(paths, prompt, schema, stop_event):
        scenes = [scene for scene in original["scenes"]
                  if any(path.name.startswith(scene["id"] + "-") for path in paths)]
        identifier = scenes[0]["id"]
        requests.append({"id": identifier, "paths": tuple(paths), "prompt": prompt})
        answer = evidenced_failure(scenes) if identifier == "shot-0" else vision_answer(scenes)
        if identifier == "shot-0" and sum(row["id"] == identifier for row in requests) == 1:
            answer["shots"][0]["issues"][0]["observed"] = "An invented paraphrase is not the visible observation."
        returned.append((answer, deepcopy(answer)))
        return answer, {"provider": "evidence_state_machine_fixture", "local": True}

    monkeypatch.setattr(review, "_request_vision", observations)
    report = review.review_render(encode(tmp_path / "movie.mp4", changed=True), original, tmp_path)
    assert [row["id"] for row in requests] == ["shot-0", "shot-1", "shot-0"]
    assert requests[0]["paths"] == requests[2]["paths"]
    assert "one permitted correction request" in requests[2]["prompt"]
    assert "not quoted from its cited visible evidence" in requests[2]["prompt"]
    assert json.loads(requests[0]["prompt"].split("not instructions:\n", 1)[1]) == json.loads(
        requests[2]["prompt"].split("not instructions:\n", 1)[1])
    assert all(value == before for value, before in returned)
    assert report["vision"]["shots_reviewed"] == 2 and report["vision"]["complete"] is True
    assert report["vision"]["failures"] == []
    assert report["passed"] is False  # A valid evidenced failure still fails the film.
    assert report["vision"]["results"][0]["assessments"]["subject_relevance"] == "fail"
    history = report["vision"]["response_attempts"]
    assert [row["validation"] for row in history] == ["invalid", "valid", "valid"]
    folder = tmp_path / report["evidence_directory"]
    for entry, (_, raw) in zip(history, returned):
        receipt = json.loads((folder / entry["file"]).read_text())
        assert receipt["response"] == raw
        assert review._hash(folder / entry["file"]) == entry["sha256"]
    assert json.loads((folder / history[0]["file"]).read_text())["validation"] == "invalid"


def evidence_fixture(scenes):
    sampled = {scene["id"]: {
        "frames": [{"id": f"{scene['id']}:{phase}", "file": f"{scene['id']}-{phase}.jpg", "time": fraction}
                   for phase, fraction in review.PHASES],
        "source_narration": "Sunlight reaches the solar panel. Current flows through the circuit.",
        "source_alignment": "exact", "sampling": {"progression_covered": True},
    } for scene in scenes}
    vision = {"status": "disabled", "shots_reviewed": 0, "complete": False,
              "results": [], "failures": [], "batches": []}
    return sampled, vision


def test_repeated_invalid_evidence_stops_after_one_correction_and_never_authorizes_repairs(tmp_path, monkeypatch):
    scenes = plan()["scenes"][:1]
    sampled, vision = evidence_fixture(scenes)
    calls = []

    def observations(*args):
        calls.append(args)
        answer = evidenced_failure(scenes)
        answer["shots"][0]["issues"][0]["observed"] = "This quote is absent from all three observations."
        return answer, {"provider": "evidence_state_machine_fixture", "local": True}

    monkeypatch.setattr(review, "_request_vision", observations)
    review._review_visual_samples(scenes, sampled, tmp_path, 1, vision)
    assert len(calls) == 2
    assert vision["results"] == [] and vision["complete"] is False
    assert vision["failures"][0]["reason"] == "invalid_review_evidence"
    assert vision["failures"][0]["response_attempts"] == 2
    assert [row["validation"] for row in vision["response_attempts"]] == ["invalid", "invalid"]
    assert len(list(tmp_path.glob("vision-response-*.json"))) == 2


@pytest.mark.parametrize("uncertain", [False, True])
def test_valid_reviews_are_not_retried_to_increase_confidence(tmp_path, monkeypatch, uncertain):
    scenes = plan()["scenes"][:1]
    sampled, vision = evidence_fixture(scenes)
    calls = []

    def observations(*args):
        calls.append(args)
        answer = vision_answer(scenes)
        if uncertain:
            answer["shots"][0]["assessments"]["motion"] = "uncertain"
        return answer, {"provider": "evidence_state_machine_fixture", "local": True}

    monkeypatch.setattr(review, "_request_vision", observations)
    review._review_visual_samples(scenes, sampled, tmp_path, 1, vision)
    assert len(calls) == 1
    assert vision["complete"] is (not uncertain)
    assert vision["results"][0]["reliable"] is (not uncertain)


def test_hosted_budget_preserves_initial_coverage_before_evidence_corrections(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LLM_ROUTE", "auto")
    scenes = [{**deepcopy(plan()["scenes"][0]), "id": f"shot-{index}"} for index in range(12)]
    sampled, vision = evidence_fixture(scenes)
    completed = []

    class BudgetExhausted(RuntimeError):
        code = "call_budget_exhausted"

    def observations(paths, prompt, schema, stop_event):
        # Simulates the existing provider budget; no network/model call is made.
        if len(completed) == 8:
            raise BudgetExhausted("The unchanged hosted budget is exhausted.")
        batch = [scene for scene in scenes if any(path.name.startswith(scene["id"] + "-") for path in paths)]
        completed.append([scene["id"] for scene in batch])
        answer = evidenced_failure(batch)
        for row in answer["shots"]:
            row["issues"][0]["observed"] = "This paraphrase is not quoted from the cited observation."
        return answer, {"provider": "hosted_budget_fixture", "local": False}

    monkeypatch.setattr(review, "_request_vision", observations)
    review._review_visual_samples(scenes, sampled, tmp_path, 12, vision)
    assert completed[:6] == [[f"shot-{2 * index}", f"shot-{2 * index + 1}"] for index in range(6)]
    assert len(completed) == 8 and len(vision["response_attempts"]) == 8
    assert any(row["reason"] == "call_budget_exhausted" for row in vision["failures"])
    assert vision["complete"] is False and vision["results"] == []


def test_evidence_correction_keeps_source_and_sampling_uncertainty(tmp_path, monkeypatch):
    scenes = plan()["scenes"][:1]
    sampled, vision = evidence_fixture(scenes)
    sampled[scenes[0]["id"]]["source_alignment"] = "unverified"
    sampled[scenes[0]["id"]]["sampling"]["progression_covered"] = False
    calls = []

    def observations(*args):
        calls.append(args)
        answer = vision_answer(scenes)
        if len(calls) == 1:
            answer["shots"][0]["observations"][0]["sample_id"] = "another-shot:setup"
        return answer, {"provider": "evidence_state_machine_fixture", "local": True}

    monkeypatch.setattr(review, "_request_vision", observations)
    review._review_visual_samples(scenes, sampled, tmp_path, 1, vision)
    assert len(calls) == 2
    assert vision["results"][0]["assessments"]["subject_relevance"] == "uncertain"
    assert vision["results"][0]["assessments"]["motion"] == "uncertain"
    assert vision["complete"] is False and vision["results"][0]["reliable"] is False


@pytest.mark.parametrize("source, excerpt", [
    ("Could old armour stop a bullet? Sometimes. The answer depends on the armour and the weapon.",
     "Could old armour stop a bullet Sometimes"),
    ("In proof tests, bullets hit some steel breastplates. A surviving dent showed that particular shot had stopped.",
     "In proof tests bullets hit some steel breastplates"),
    ("The steel plate did not stop the bullet. This failed test does not imply every plate would fail.",
     "The steel plate did not stop the bullet"),
])
def test_decoded_review_keeps_original_question_assertion_and_negation_context(tmp_path, source, excerpt):
    original = plan()
    original["sections"][0]["narration"] = source
    original["captions"] = [{"start": 0, "end": 2, "text": excerpt}]
    # Generated descriptions are deliberately not authoritative source data.
    original["scenes"][0].update(title="A bullet always deflects", visual_goal="Show an impact")
    original["scenes"][0]["shot"].update(role="establish", action="Deflect an incoming bullet")
    before = deepcopy(original)
    report = review.review_render(encode(tmp_path / "context-fixture.mp4"), original, tmp_path)
    samples = {row["shot_id"]: row for row in report["samples"]}
    prompt = review._vision_prompt(original["scenes"], samples)
    data = json.loads(prompt.split("not instructions:\n", 1)[1])
    first = data["shots"][0]
    assert first["source_narration"] == excerpt
    assert first["source_section_narration"] == source
    assert first["source_scope"] == "caption_overlap"
    assert first["source_section_truncated"] is False
    assert first["shot_window_seconds"] == {"start": 0, "end": 2}
    assert data["shots"][1]["source_scope"] == "section_excerpt_by_duration"
    assert data["shots"][1]["shot_window_seconds"] == {"start": 2, "end": 4}
    assert [row["sample_id"] for row in data["image_order"]] == [
        f"shot-{index}:{phase}" for index in range(2) for phase, _ in review.PHASES]
    assert "sample labels, not mandatory physical stages" in prompt
    assert "source-supported physical interaction" in prompt
    assert "motion independently" in prompt
    # Providing better source context is not a semantic verdict or an edit.
    assert report["passed"] is None and report["vision"]["complete"] is False
    assert original == before


def test_original_context_is_bounded_and_cannot_replace_a_missing_shot_source():
    scene = plan()["scenes"][0]
    context = "Could this be an impact? " + "Surrounding source context. " * 80
    evidence = {scene["id"]: {"frames": [{"id": f"{scene['id']}:{phase}", "time": fraction}
                                            for phase, fraction in review.PHASES],
                             "source_narration": "", "source_section_narration": context}}
    prompt = review._vision_prompt([scene], evidence)
    row = json.loads(prompt.split("not instructions:\n", 1)[1])["shots"][0]
    assert row["source_narration"] == ""
    assert row["source_section_narration"] == context[:1200]
    assert row["source_section_truncated"] is True
    assert "missing or truncated shot source is uncertain" in prompt
    assert "source-supported physical interaction" in prompt


@pytest.mark.parametrize("alignment,context,expected", [
    ("exact", "Armour and firearms evolved together for centuries.", ["armour", "firearm"]),
    ("unverified", "Armour and firearms evolved together for centuries.", []),
    ("exact", "Armour was displayed. Firearms evolved together for centuries.", []),
])
def test_review_pair_identity_crosses_exact_sentence_cut_without_expanding_actions(alignment, context, expected):
    scene = plan()["scenes"][0]
    excerpt = "firearms evolved together for centuries."
    evidence = {scene["id"]: {
        "frames": [{"id": f"{scene['id']}:{phase}", "time": fraction} for phase, fraction in review.PHASES],
        "source_narration": excerpt, "source_section_narration": context,
        "source_sentence_context": context.split(". ")[-1], "source_alignment": alignment,
        "source_scope": "word_timing" if alignment == "exact" else "word_timing_unaligned"}}
    original = deepcopy(evidence)
    prompt = review._vision_prompt([scene], evidence)
    row = json.loads(prompt.split("not instructions:\n", 1)[1])["shots"][0]
    assert [subject["symbol"] for subject in row["source_subjects"]] == expected
    assert row["source_narration"] == excerpt
    assert "identity, never an extra action or outcome" in prompt
    assert evidence == original


def test_decoded_mail_shot_review_never_receives_next_shot_deflection_requirement(tmp_path):
    original = plan()
    source = "Mail uses linked rings. Steel plate deflects blows."
    original["sections"][0]["narration"] = source
    original["words"] = [{"word": word, "start": start, "end": start + .25, "section_id": "one"}
        for word, start in [("Mail", .1), ("uses", .5), ("linked", .9), ("rings", 1.4),
                            ("Steel", 2), ("plate", 2.4), ("deflects", 2.8), ("blows", 3.3)]]
    original["captions"] = [{"start": 0, "end": 1.4, "text": "Mail uses linked"},
                            {"start": 1.4, "end": 2.7, "text": "rings Steel plate"},
                            {"start": 2.7, "end": 4, "text": "deflects blows"}]
    report = review.review_render(encode(tmp_path / "scope-fixture.mp4"), original, tmp_path)
    samples = {row["shot_id"]: row for row in report["samples"]}
    prompt = review._vision_prompt(original["scenes"][:1], samples)
    data = json.loads(prompt.split("not instructions:\n", 1)[1])["shots"][0]
    assert data["source_narration"] == data["source_sentence_context"] == "Mail uses linked rings."
    assert data["source_section_omitted"] is True and data["source_section_narration"] == ""
    assert "deflects" not in prompt
    assert samples["shot-0"]["source_section_narration"] == source  # Kept in audit evidence.
    assert samples["shot-1"]["source_narration"] == "Steel plate deflects blows."
    assert "not to require every event in the sentence" in prompt
    assert report["passed"] is None  # Better source scope cannot approve pixels.


@pytest.mark.parametrize("reported_error", [False, True])
def test_unaligned_word_text_cannot_turn_an_assertive_model_into_approval(tmp_path, monkeypatch, reported_error):
    original = plan()
    original["words"] = [{"word": "Unrelated", "start": .2, "end": .7},
                         {"word": "narration", "start": 2.2, "end": 2.7}]
    monkeypatch.setattr(config, "VISUAL_REVIEW_ENABLED", True)

    def observations(paths, prompt, schema, stop_event):
        scenes = [scene for scene in original["scenes"]
                  if any(path.name.startswith(scene["id"] + "-") for path in paths)]
        answer = vision_answer(scenes)
        if reported_error:
            for row in answer["shots"]:
                row["assessments"]["subject_relevance"] = "fail"
                row["issues"] = [{"category": "subject_relevance", "severity": "error",
                    "message": "This boundary fixture reports an action outside the uncertain source.",
                    "samples": [f"{row['shot_id']}:action"], "repair": "source_direction",
                    "defect": "source_mismatch", "expected": "The source requires an identifiable solar panel.",
                    "observed": row["observations"][1]["visible"]}]
        return answer, {"provider": "semantic_boundary_fixture", "local": True}

    monkeypatch.setattr(review, "_request_vision", observations)
    report = review.review_render(encode(tmp_path / "unaligned-fixture.mp4", changed=True), original, tmp_path)
    assert all(row["source_alignment"] == "unverified" for row in report["samples"])
    assert all(row["assessments"]["subject_relevance"] == "uncertain" and row["reliable"] is False
               for row in report["vision"]["results"])
    assert report["vision"]["complete"] is False and report["passed"] is not True
    assert all(row["reliable"] is False for row in review._validated_reviews(original, report).values())
    assert review.repair_plan(original, report)[1] == []


def test_prompt_image_indices_are_exact_across_two_shots():
    scenes = plan()["scenes"]
    samples = {row["id"]: {"frames": [{"id": f"{row['id']}:{phase}", "time": row["start"] + fraction}
                                     for phase, fraction in review.PHASES]} for row in scenes}
    payload = json.loads(review._vision_prompt(scenes, samples).split("not instructions:\n", 1)[1])
    assert [row["image_index"] for row in payload["image_order"]] == list(range(6))
    assert [row["sample_id"] for row in payload["image_order"]] == [f"shot-{i}:{phase}" for i in range(2) for phase, _ in review.PHASES]


def test_late_contact_samples_decode_the_event_and_its_result(tmp_path):
    original = plan()
    shot = original["scenes"][1]
    shot["mechanism"] = {"version": 1, "kind": "armour", "material": "steel", "action": "deflect"}
    shot["beats"].append({"time": 1.7, "target": "scene", "action": "highlight"})
    original["words"] = [{"word": "deflects", "start": 3.7, "end": 3.85}]
    # This encoding is a technical color-box transition, not a model or a
    # claimed armour demonstration. The event occurs after the old 84% sample.
    value = review.review_render(encode(tmp_path / "late.mp4", changed=True, change_time=3.7), original, tmp_path)
    evidence = value["samples"][1]
    assert evidence["sampling"]["method"] == "scene_contact_beat"
    assert evidence["sampling"]["word_anchor_matches"] is True
    assert evidence["sampling"]["progression_covered"] is True
    assert evidence["frames"][0]["time"] < 3.7 <= evidence["frames"][1]["time"] < evidence["frames"][2]["time"] < 4
    assert max(evidence["metrics"]["central_stage_change"]) > 1.5
    assert not any(issue["code"] == "action_not_visible" and issue["shot_id"] == shot["id"] for issue in value["issues"])
    assert value["passed"] is None


def test_glancing_force_diagram_samples_bracket_its_change_without_inventing_projectile_contact():
    scene = plan()["scenes"][1]
    scene["mechanism"] = {"version": 1, "kind": "armour", "material": "steel", "action": "glance"}
    scene["beats"].append({"time": 1.7, "target": "scene", "action": "highlight"})
    samples, metadata = review._sample_times(scene, {"fps": 12, "duration": 4})
    assert metadata["method"] == "scene_contact_beat"
    assert metadata["contact_time"] == 3.7
    assert metadata["progression_covered"] is True
    assert samples[0][1] < 3.7 <= samples[1][1] < samples[2][1] < 4
    assert scene["mechanism"]["action"] == "glance"


def test_contact_on_last_frame_is_incomplete_instead_of_an_absent_action(tmp_path):
    original = plan()
    shot = original["scenes"][1]
    shot["mechanism"] = {"version": 1, "kind": "armour", "material": "steel", "action": "deflect"}
    shot["beats"].append({"time": 2 - 1 / 12, "target": "scene", "action": "highlight"})
    value = review.review_render(encode(tmp_path / "last-frame.mp4"), original, tmp_path)
    evidence = value["samples"][1]
    assert evidence["sampling"]["progression_covered"] is False
    assert evidence["sampling"]["result_window"] == "limited_by_shot_end"
    assert any(issue["code"] == "incomplete_action_sampling" for issue in value["issues"])
    assert not any(issue["code"] == "action_not_visible" for issue in value["issues"])


def test_source_redirection_uses_the_real_offline_director_and_preserves_narration(monkeypatch):
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", False)
    original = plan()
    original["scenes"][0].update(graphics_version=2,
        staging={"version": 1, "world": "generic", "action": "reveal", "camera": "wide"},
        items=[{"label": "Residents", "symbol": "people", "role": "subject"}])
    script = {"sections": deepcopy(original["sections"])}
    candidate, changes = review._source_redirection(original, "shot-0", script, "subject_relevance")
    assert changes and any(item["symbol"] == "solar" for item in candidate["scenes"][0]["items"])
    assert all(item["symbol"] != "people" for item in candidate["scenes"][0]["items"])
    for key in ("sections", "words", "captions", "duration", "width", "height", "fps"):
        assert candidate[key] == original[key]
    assert candidate["scenes"][1] == original["scenes"][1]
    assert candidate["last_edit"]["status"] == "local"
    assert "passed" not in candidate  # Directing a candidate is never visual approval.


def test_source_redirection_refuses_changed_narration_and_media(monkeypatch):
    from agents import visual_director
    original = plan()
    script = {"sections": deepcopy(original["sections"])}
    script["sections"][0]["narration"] = "An unrelated replacement script."
    with pytest.raises(ValueError, match="unchanged narration"):
        review._source_redirection(original, "shot-0", script, "subject_relevance")
    script = {"sections": deepcopy(original["sections"])}
    def change_media(value, *args, **kwargs):
        value["scenes"][0]["image"] = {"path": "/another-project/picture.jpg"}
        return value
    monkeypatch.setattr(visual_director, "regenerate_scene", change_media)
    with pytest.raises(ValueError, match="media identity"):
        review._source_redirection(original, "shot-0", script, "subject_relevance")


@pytest.mark.parametrize("old_action, new_action, allowed", [("reveal", "deflect", True), ("deflect", "reveal", False)])
def test_source_redirection_can_add_the_narrated_action_but_cannot_change_its_outcome(monkeypatch, old_action, new_action, allowed):
    from agents import visual_director
    original = plan()
    original["sections"][0]["narration"] = "Steel armour deflects the incoming bullet."
    shot = original["scenes"][0]
    shot["mechanism"] = {"version": 1, "kind": "armour", "material": "steel", "action": old_action}
    shot["shot"]["technique"] = "diagram3d"
    # A candidate boundary fixture exercises the validator; it is not an
    # inference response and does not assert any rendered review passed.
    def candidate(value, *args, **kwargs):
        value["scenes"][0]["mechanism"]["action"] = new_action
        return value
    monkeypatch.setattr(visual_director, "regenerate_scene", candidate)
    script = {"sections": original["sections"]}
    if allowed:
        value, changes = review._source_redirection(original, "shot-0", script, "motion")
        assert changes and value["scenes"][0]["mechanism"]["action"] == "deflect"
    else:
        with pytest.raises(ValueError, match="original supported physical outcome"):
            review._source_redirection(original, "shot-0", script, "motion")


def _material_redirection_plan():
    original = plan()
    original["sections"][0]["narration"] = "Steel armour deflects the incoming bullet."
    scene = original["scenes"][0]
    scene.update(graphics_version=2,
        staging={"version": 1, "world": "generic", "action": "reveal", "camera": "locked"},
        items=[{"label": "Steel armour", "symbol": "circuit", "role": "subject"},
               {"label": "Incoming bullet", "symbol": "building", "role": "support"}],
        mechanism={"version": 1, "kind": "armour", "material": "steel", "action": "reveal"})
    scene["shot"]["technique"] = "diagram3d"
    return original


@pytest.mark.parametrize("action, allowed", [("deflect", True), ("penetrate", False)])
def test_material_source_redirection_grounds_drawn_mechanism_not_hidden_actors(monkeypatch, action, allowed):
    from agents import visual_director
    original = _material_redirection_plan()

    def candidate(value, *args, **kwargs):
        value["scenes"][0]["mechanism"]["action"] = action
        return value

    monkeypatch.setattr(visual_director, "regenerate_scene", candidate)
    if allowed:
        changed, fields = review._source_redirection(original, "shot-0", {"sections": original["sections"]}, "motion")
        assert changed["scenes"][0]["mechanism"]["action"] == "deflect"
        assert set(fields) == {"mechanism"}
    else:
        with pytest.raises(ValueError, match="unsupported physical outcome"):
            review._source_redirection(original, "shot-0", {"sections": original["sections"]}, "motion")


def test_hidden_material_actors_plus_camera_cannot_qualify_as_subject_redirection(monkeypatch):
    from agents import visual_director
    original = _material_redirection_plan()

    def candidate(value, *args, **kwargs):
        scene = value["scenes"][0]
        for item in scene["items"]:
            item["symbol"] = "concept"  # Valid generic actors, but this builder never draws them.
        scene["shot"].update(framing="close", camera="push")
        return value

    monkeypatch.setattr(visual_director, "regenerate_scene", candidate)
    with pytest.raises(ValueError, match="did not change the depicted subject or action"):
        review._source_redirection(original, "shot-0", {"sections": original["sections"]}, "subject_relevance")


def test_generic_source_redirection_still_rejects_unsupported_actor_subjects(monkeypatch):
    from agents import visual_director
    original = plan()

    def candidate(value, *args, **kwargs):
        value["scenes"][0].update(graphics_version=2,
            staging={"version": 1, "world": "generic", "action": "reveal", "camera": "locked"},
            items=[{"label": "A building", "symbol": "building", "role": "subject"}])
        return value

    monkeypatch.setattr(visual_director, "regenerate_scene", candidate)
    with pytest.raises(ValueError, match="unsupported illustrated actors or actions"):
        review._source_redirection(original, "shot-0", {"sections": original["sections"]}, "subject_relevance")


@pytest.mark.parametrize("text,cut,actors,action,allowed", [
    ("Armour and firearms evolved together for centuries.", 2,
     [("Armour", "armour"), ("Firearms", "firearm")], "compare", True),
    ("Solar panels and batteries were connected, then evolved together.", 6,
     [("Solar panels", "solar"), ("Batteries", "battery")], "compare", True),
    ("Solar panels and batteries were connected, then evolved together.", 6,
     [("Solar panels", "solar"), ("Batteries", "battery")], "connect", False),
])
def test_source_redirection_recovers_cut_pair_identity_without_borrowing_earlier_action(
        monkeypatch, text, cut, actors, action, allowed):
    from agents import visual_director
    original = plan()
    original["sections"][0]["narration"] = text
    # Explicit fixture clock: the two-second cut splits the original sentence.
    # The second excerpt owns the relationship but not every paired name or
    # the preceding connection action. No media or model approval is simulated.
    tokens = text.split()
    original["words"] = []
    for index, token in enumerate(tokens):
        start = index * 2 / cut if index < cut else 2 + (index - cut) * 2 / (len(tokens) - cut)
        original["words"].append({"word": token.strip(".,"), "start": start,
                                  "end": start + .1, "section_id": "one"})
    original["scenes"][1].update(graphics_version=2,
        staging={"version": 1, "world": "generic", "action": "reveal", "camera": "locked"},
        items=[{"label": actors[1][0], "symbol": actors[1][1], "role": "subject"}])

    def candidate(value, *args, **kwargs):
        scene = value["scenes"][1]
        scene.update(template="comparison" if action == "compare" else "flow",
                     title=actors[0][0] + " and " + actors[1][0],
                     visual_goal="Show the explicitly named subjects",
                     staging={"version": 1, "world": "generic", "action": action, "camera": "locked"},
                     items=[{"label": label, "symbol": symbol, "role": "subject" if index == 0 else "support"}
                            for index, (label, symbol) in enumerate(actors)])
        scene["shot"].update(focus=scene["title"], action=action)
        return value

    monkeypatch.setattr(visual_director, "regenerate_scene", candidate)
    saved = deepcopy(original)
    if allowed:
        changed, fields = review._source_redirection(original, "shot-1", {"sections": original["sections"]}, "subject_relevance")
        assert fields and [item["symbol"] for item in changed["scenes"][1]["items"]] == [symbol for _, symbol in actors]
        assert changed["scenes"][0] == original["scenes"][0]
        assert all(changed[key] == original[key] for key in ("sections", "words", "captions", "duration", "width", "height", "fps"))
        assert "passed" not in changed
    else:
        with pytest.raises(ValueError, match="unsupported illustrated actors or actions"):
            review._source_redirection(original, "shot-1", {"sections": original["sections"]}, "subject_relevance")
    assert original == saved


def test_unreliable_semantic_failure_cannot_request_source_redirection(monkeypatch):
    monkeypatch.setattr(review, "_source_redirection", lambda *a, **k: pytest.fail("Unreliable evidence triggered editing"))
    original = plan()
    # Schema-validation fixture only: this is not presented as a live model
    # response or a passing visual review.
    rows = vision_answer(original["scenes"])
    row = rows["shots"][0]
    row["confidence"] = .4
    row["assessments"]["subject_relevance"] = "fail"
    row["issues"] = [{"category": "subject_relevance", "severity": "error",
                      "message": "The visible subject does not match the supplied narration.",
                      "samples": ["shot-0:action"], "repair": "source_direction",
                      "defect": "source_mismatch", "expected": "The source requires an identifiable solar panel.",
                      "observed": row["observations"][1]["visible"]}]
    report = {"plan_sha256": review._plan_hash(original), "vision": {"results": rows["shots"]},
              "issues": [review._issue("shot-0", "subject_relevance", row["issues"][0]["message"],
                  severity="error", source="vision", repair="source_direction", samples=["shot-0:action"])]}
    candidate, decisions = review.repair_plan(original, report, {"sections": original["sections"]})
    assert candidate == original and decisions == []


def test_safe_repair_preserves_content_assets_and_rejects_stale_report():
    original = plan()
    original["scenes"][1]["mechanism"] = {"version": 1, "kind": "armour", "material": "steel", "action": "deflect"}
    original["scenes"][1]["shot"]["technique"] = "diagram3d"
    issue = review._issue("shot-1", "repeated_layout", "Similar subject framing", repair="vary_framing")
    report = {"plan_sha256": review._plan_hash(original), "issues": [issue]}
    candidate, decisions = review.repair_plan(original, report)
    assert decisions and candidate["scenes"][1]["shot"]["framing"] == "close"
    for key in ("captions", "words", "sections", "duration", "fps", "width", "height"):
        assert candidate[key] == original[key]
    for index, scene in enumerate(original["scenes"]):
        for key in ("id", "section_id", "start", "duration", "mechanism", "items", "beats", "title"):
            assert candidate["scenes"][index].get(key) == scene.get(key)
    assert original["scenes"][1]["shot"]["framing"] == "wide"
    with pytest.raises(ValueError, match="older storyboard"):
        review.repair_plan(candidate, report)


def test_plan_changes_without_changed_rendered_pixels_are_rolled_back(tmp_path):
    video = encode(tmp_path / "movie.mp4")
    original = plan()
    digest = review._hash(video)
    path, accepted, report = review.review_and_repair(video, original, tmp_path, render=lambda candidate: str(video))
    assert accepted == original and review._hash(path) == digest
    assert report["repairs_applied"] == 0
    assert report["repair_history"][0]["accepted"] is False
    assert report["repair_history"][0]["decoded_stage_change"]["shot-1"] == 0
    assert (tmp_path / report["render"]["snapshot"]).is_file()


def test_real_repair_is_reencoded_rechecked_and_retains_both_attempts(tmp_path):
    video = encode(tmp_path / "movie.mp4")
    original = plan()
    digest = review._hash(video)
    seen = []
    def render(candidate):
        seen.append(deepcopy(candidate))
        return encode(video, changed=True)
    path, accepted, report = review.review_and_repair(video, original, tmp_path, render=render)
    assert len(seen) == 1 and accepted == seen[0] and accepted != original
    assert review._hash(path) != digest
    assert report["repairs_applied"] == 1 and report["shots_repaired"] == 1
    assert report["passed"] is None  # Real technical repair still does not invent a vision reviewer.
    assert report["repair_history"][0]["accepted"] is True
    assert report["repair_history"][0]["decoded_stage_change"]["shot-1"] > 1.5
    run = tmp_path / "render_review" / report["run_id"]
    assert all((run / f"attempt-{i}" / "encoded.mp4").is_file() for i in (0, 1))
    assert all((run / f"attempt-{i}" / "review.json").is_file() for i in (0, 1))


def test_renderer_failure_restores_the_reviewed_movie(tmp_path):
    video = encode(tmp_path / "movie.mp4")
    digest = review._hash(video)
    def fail(candidate):
        video.write_bytes(b"broken output")
        raise RuntimeError("render failed")
    with pytest.raises(RuntimeError, match="render failed"):
        review.review_and_repair(video, plan(), tmp_path, render=fail)
    assert review._hash(video) == digest


@pytest.fixture
def local_transport(tmp_path, monkeypatch):
    from PIL import Image
    image = tmp_path / "frame.jpg"
    Image.new("RGB", (40, 40), "teal").save(image)
    monkeypatch.setattr(config, "OLLAMA_MODEL", "test-vision:latest")
    monkeypatch.setattr(local_llm, "list_models", lambda *a, **kw: [{"id": "test-vision:latest", "digest": "a" * 64}])
    calls = []
    capabilities = ["completion", "vision"]
    def request(origin, endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        if endpoint == "/api/show":
            return {"model_info": {"general.architecture": "test"}, "capabilities": capabilities}
        return {"done": True, "done_reason": "stop", "message": {"content": '{"seen":true}'}}
    monkeypatch.setattr(local_llm, "_request_json", request)
    schema = {"type": "object", "additionalProperties": False, "required": ["seen"], "properties": {"seen": {"type": "boolean"}}}
    return image, schema, calls, capabilities


def test_local_vision_sends_real_images_only_after_capability_verification(local_transport):
    image, schema, calls, capabilities = local_transport
    capabilities.append("thinking")
    details = {}
    assert local_vision.review_json([image], "Describe this frame.", schema, provider_info=details) == {"seen": True}
    assert [endpoint for endpoint, _ in calls] == ["/api/show", "/api/chat"]
    message = calls[1][1]["body"]["messages"][-1]
    assert len(message["images"]) == 1 and len(message["images"][0]) > 100
    assert calls[1][1]["body"]["think"] is False
    assert details == {"provider": "ollama", "model": "test-vision:latest", "digest": "a" * 64, "local": True}


def test_text_only_local_model_never_receives_images(local_transport):
    image, schema, calls, capabilities = local_transport
    capabilities.remove("vision")
    with pytest.raises(local_llm.LocalLLMError) as error:
        local_vision.review_json([image], "Describe this frame.", schema)
    assert error.value.code == "vision_unsupported"
    assert [endpoint for endpoint, _ in calls] == ["/api/show"]


def test_remote_ollama_alias_is_rejected_before_inference(local_transport, monkeypatch):
    image, schema, calls, capabilities = local_transport
    monkeypatch.setattr(local_llm, "_request_json", lambda *a, **kw: {"remote_host": "remote.example"})
    with pytest.raises(local_llm.LocalLLMError) as error:
        local_vision.review_json([image], "Describe this frame.", schema)
    assert error.value.code == "cloud_model_rejected"


def test_repair_rolls_back_new_reliable_semantic_failure_even_when_labeled_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VISUAL_REVIEW_ENABLED", True)
    original = plan()
    video = encode(tmp_path / "movie.mp4", changed=True)
    digest = review._hash(video)
    state = {"rendered": False}

    def observations(paths, prompt, schema, stop_event):
        # Deliberate semantic state-machine fixtures, not live inference or a
        # claim about artistic quality. MP4 decoding/pixel checks stay real.
        scenes = [scene for scene in original["scenes"]
                  if any(path.name.startswith(scene["id"] + "-") for path in paths)]
        answer = vision_answer(scenes)
        for row in answer["shots"]:
            if row["shot_id"] != "shot-1":
                continue
            category = "subject_relevance" if state["rendered"] else "composition"
            row["assessments"][category] = "fail"
            row["issues"] = [{"category": category, "severity": "warning",
                "message": "This boundary fixture reports a new subject failure." if state["rendered"]
                           else "This boundary fixture requests closer composition.",
                "samples": ["shot-1:action"], "repair": "none" if state["rendered"] else "reframe_close",
                "defect": "source_mismatch" if state["rendered"] else "cropped_subject",
                "expected": "The subject must remain recognizable and fully in the frame.",
                "observed": row["observations"][1]["visible"]}]
        return answer, {"provider": "semantic_boundary_fixture", "local": True}

    def render(candidate):
        state["rendered"] = True
        return str(encode(video))

    monkeypatch.setattr(review, "_request_vision", observations)
    path, accepted, report = review.review_and_repair(video, original, tmp_path, render=render)
    decision = report["repair_history"][0]
    assert decision["decoded_stage_change"]["shot-1"] > 1.5
    assert decision["accepted"] is False and report["repairs_applied"] == 0
    assert accepted == original and review._hash(path) == digest
    after = json.loads((tmp_path / decision["after_report"] / "review.json").read_text())
    assert after["vision"]["complete"] is True and after["passed"] is False
    assert any(issue["code"] == "subject_relevance" and issue["severity"] == "warning" for issue in after["issues"])
    assert (tmp_path / decision["after_report"] / "encoded.mp4").is_file()
