"""Planning provenance fixtures are not live inference or visual approval."""
from copy import deepcopy

import pytest

import config
from agents import visual_director as director


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setattr(config, "SHOT_PLANNING_ENABLED", True)
    monkeypatch.setattr(config, "CHANNEL_PRESET", "general")
    monkeypatch.setattr(config, "VISUAL_AI_ENABLED", True)
    script = {"title": "Light and electricity", "sections": [{"id": "one",
              "narration": "Sunlight reaches solar panels. Electricity flows through the circuit."}]}
    timeline = {"duration": 4, "fps": 24, "width": 320, "height": 180,
                "sections": [{"id": "one", "start": 0, "duration": 4}]}
    plan = director.plan_scenes(script, timeline, use_ai=False)
    sources = {scene["id"]: {"narration": script["sections"][0]["narration"], "section": script["sections"][0]}
               for scene in plan["scenes"]}
    design = {"scenes": [{key: deepcopy(scene[key]) for key in
        ("id", "title", "visual_goal", "template", "items", "composition", "look", "rhythm", "staging", "shot") if key in scene}
        for scene in plan["scenes"]]}
    return script, timeline, plan, sources, design


def applied(plan, sources, design):
    candidate, journal = deepcopy(plan), {}
    director._apply_design(candidate, design, sources, provenance=journal)
    candidate["ai_status"] = {"status": "planned", "provenance": journal}
    director.refresh_model_provenance(candidate)
    return candidate


def test_every_rejected_scene_cannot_pass_the_model_contribution_gate(source, monkeypatch):
    script, timeline, baseline, _, design = source
    for row in design["scenes"]:
        row["title"] = "Unsupported 99999 panels"
    monkeypatch.setattr(director, "_visual_ai_available", lambda: True)
    monkeypatch.setattr(director, "_request_design", lambda *args, **kwargs: deepcopy(design))
    result = director.plan_scenes(script, timeline)
    report = result["ai_status"]["provenance"]
    assert result["ai_status"]["status"] == "planned"  # Backward-compatible response receipt.
    assert report["generated_scene_count"] == report["rejected_scene_count"] == len(result["scenes"])
    assert report["accepted_scene_count"] == report["rendered_scene_count"] == 0
    assert result["scenes"] == baseline["scenes"]
    assert all(row["reason"] == "unsupported_number" for row in report["scenes"])
    assert director.has_model_visual_contribution(result) is False


def test_fallback_retains_bounded_schema_location_but_never_raw_provider_text(source, monkeypatch):
    script, timeline, _, _, _ = source
    message = "Visual JSON has an unsupported value at $.scenes[0].staging.camera."
    def rejected(*args, **kwargs):
        raise director.openrouter.OpenRouterError("invalid_response", message)
    monkeypatch.setattr(director, "_visual_ai_available", lambda: True)
    monkeypatch.setattr(director, "_request_design", rejected)
    result = director.plan_scenes(script, timeline)
    assert result["ai_status"] == {"status": "fallback", "reason": "invalid_response", "detail": message}
    for unsafe in ("Authorization: Bearer test-secret. Raw user narration follows.",
                   "Visual JSON has an invalid type at $.secret_api_token."):
        detail = director._safe_planning_failure(RuntimeError(unsafe))
        assert "test-secret" not in detail and "secret_api_token" not in detail and "narration" not in detail
        assert len(detail) <= 300


def test_unchanged_inherited_choices_are_not_model_contributions(source):
    _, _, plan, sources, design = source
    result = applied(plan, sources, design)
    report = result["ai_status"]["provenance"]
    assert report["accepted_scene_count"] == len(plan["scenes"])
    assert report["changed_scene_count"] == report["rendered_scene_count"] == 0
    assert director.has_model_visual_contribution(result) is False


def test_actual_framing_choice_survives_but_notes_and_equivalent_cuts_do_not(source):
    _, _, plan, sources, design = source
    row = design["scenes"][0]
    row["shot"].update(framing="macro", focus="The panels in this source", action="Explain the supplied electricity", transition="match")
    result = applied(plan, sources, design)
    record = result["ai_status"]["provenance"]["scenes"][0]
    assert {change["field"] for change in record["changes"]} >= {"shot.framing", "shot.focus", "shot.action", "shot.transition"}
    assert record["rendered_nontext_fields"] == ["shot.framing"]
    assert director.has_model_visual_contribution(result) is True
    result["scenes"][0]["shot"]["framing"] = plan["scenes"][0]["shot"]["framing"]
    assert director.has_model_visual_contribution(result) is False


def test_later_collage_binding_does_not_credit_removed_actors_or_staging(source):
    _, _, plan, sources, design = source
    design["scenes"][0]["staging"]["camera"] = "detail"
    result = applied(plan, sources, design)
    assert director.has_model_visual_contribution(result) is True
    scene = result["scenes"][0]
    scene["image"] = {"path": "/saved/a.png"}
    scene["media_layers"] = [{"path": "/saved/a.png"}, {"path": "/saved/b.png"}]
    director._as_media_scene(scene)
    director.shot_planner.reconcile_media(scene)
    report = director.refresh_model_provenance(result)
    assert scene["shot"]["technique"] == "collage"
    assert report["rendered_nontext_scene_count"] == 0
    assert director.has_model_visual_contribution(result) is False


def test_local_actor_fallback_is_reported_separately(source):
    _, _, plan, sources, design = source
    first = plan["scenes"][0]
    first["staging"]["camera"] = "locked"
    design["_local_direction_scenes"] = [first["id"]]
    design["scenes"][0]["staging"]["camera"] = "detail"
    result = applied(plan, sources, design)
    record = result["ai_status"]["provenance"]["scenes"][0]
    assert "staging.camera" in record["local_fallback_fields"]
    assert "staging.camera" not in {change["field"] for change in record["changes"]}
    assert director.has_model_visual_contribution(result) is False


def test_mechanism_projection_ignores_hidden_actors_and_consumes_palette(source):
    _, _, plan, _, _ = source
    scene = plan["scenes"][0]
    scene["mechanism"] = {"version": 1, "kind": "armour", "material": "steel", "action": "reveal"}
    scene["shot"]["technique"] = "diagram3d"
    before = director.render_decisions(scene, plan)
    scene["staging"]["camera"] = "detail"
    scene["items"][0]["label"] = "A hidden label"
    scene["items"][0]["symbol"] = "people"
    assert director.render_decisions(scene, plan) == before
    scene["palette"] = "sand" if before["palette"] != "sand" else "slate"
    assert director.render_decisions(scene, plan) != before
    scene["mechanism"].update(material="ceramic", action="separate")
    scene["items"] = [{"label": "Ceramic front"}, {"label": "Hidden extra label"}]
    for technique in ("diagram3d", "stage2d"):
        scene["shot"]["technique"] = technique
        projection = director.render_decisions(scene, plan)
        assert projection["items.labels"] == ["Ceramic front"]


def test_repeated_local_mechanism_is_unchanged_and_its_removal_is_reported(source):
    _, _, plan, sources, design = source
    scene = plan["scenes"][0]
    scene["mechanism"] = {"version": 1, "kind": "armour", "material": "steel", "action": "reveal"}
    scene["staging"] = {"version": 1, "world": "generic", "action": "reveal", "camera": "locked"}
    scene["items"] = [{"label": "Steel armour", "symbol": "concept", "role": "subject"}]
    sources[scene["id"]] = {"narration": "Steel armour is revealed.", "section": {"narration": "Steel armour is revealed."}}
    row = design["scenes"][0]
    row.update(staging=deepcopy(scene["staging"]), items=deepcopy(scene["items"]), mechanisms=[deepcopy(scene["mechanism"])])
    retained = applied(plan, sources, design)
    assert not any(change["field"].startswith("mechanism.") for change in retained["ai_status"]["provenance"]["scenes"][0]["changes"])
    assert director.has_model_visual_contribution(retained) is False
    row["mechanisms"] = []
    removed = applied(plan, sources, design)
    assert "mechanism" not in removed["scenes"][0]
    assert "mechanism.kind" in removed["ai_status"]["provenance"]["scenes"][0]["rendered_nontext_fields"]
    # An accepted renderer change can still be a poor illustration. This
    # provenance journal never assigns a visual-quality pass.
    assert "passed" not in removed["ai_status"]["provenance"]


def test_automatic_camera_reconciliation_is_not_an_accepted_model_choice(source):
    _, _, plan, sources, design = source
    plan["scenes"][0]["shot"]["camera"] = "locked"
    design["scenes"][0]["shot"]["camera"] = "orbit"
    result = applied(plan, sources, design)
    record = result["ai_status"]["provenance"]["scenes"][0]
    assert result["scenes"][0]["shot"]["camera"] == "push"
    assert "shot.camera" in record["local_fallback_fields"]
    assert director.has_model_visual_contribution(result) is False


def _material_case(source, narration):
    _, timeline, _, _, _ = source
    script = {"title": "Material structure", "sections": [{"id": "one", "narration": narration}]}
    plan = director.plan_scenes(script, timeline, use_ai=False)
    sources = {scene["id"]: {"narration": narration, "section": script["sections"][0]} for scene in plan["scenes"]}
    return plan, sources


@pytest.mark.parametrize("builder", ["collage", "layers2d", "layers3d"])
def test_hidden_item_count_changes_cannot_borrow_visible_label_effects(source, builder):
    plan, sources = _material_case(source, "Ceramic protective layers separate to reveal their structure.")
    scene = plan["scenes"][0]
    scene["items"] = [{"label": "Ceramic front", "symbol": "concept", "role": "subject"}]
    scene["staging"] = {"version": 1, "world": "generic", "action": "reveal", "camera": "locked"}
    scene["shot"].update(technique="diagram3d" if builder == "layers3d" else "stage2d", camera="locked")
    row = {key: deepcopy(scene[key]) for key in
           ("id", "title", "visual_goal", "template", "items", "composition", "staging", "shot")}
    row["items"].append({"label": "Protective backing", "symbol": "concept", "role": "support"})
    result = applied(plan, sources, {"scenes": [row]})
    if builder == "collage":
        bound = result["scenes"][0]
        bound["image"] = {"path": "/saved/a.png"}
        bound["media_layers"] = [{"path": "/saved/a.png"}, {"path": "/saved/b.png"}]
        director._as_media_scene(bound)
        director.shot_planner.reconcile_media(bound)
    report = director.refresh_model_provenance(result)
    record = report["scenes"][0]
    assert {change["field"] for change in record["changes"]} >= {"items.labels", "items.actors", "items.values"}
    assert record["rendered_fields"] == ([] if builder == "collage" else ["items.labels"])
    assert record["rendered_nontext_fields"] == []
    assert director.has_model_visual_contribution(result) is False


@pytest.mark.parametrize("new_camera", [False, True])
def test_collage_narration_labels_never_count_as_rendered_model_choices(source, new_camera):
    _, _, plan, sources, _ = source
    scene = plan["scenes"][0]
    scene["image"] = {"path": "/saved/panel.png", "credit": "Solar panel · Collection"}
    scene["media_layers"] = [deepcopy(scene["image"]),
                             {"path": "/saved/circuit.png", "credit": "Circuit detail · Collection"}]
    director._as_media_scene(scene)
    director.shot_planner.reconcile_media(scene)
    scene["shot"].update(camera="locked", framing="wide")
    design = {"scenes": [{key: deepcopy(row[key]) for key in
        ("id", "title", "visual_goal", "template", "items", "composition", "shot") if key in row}
        for row in plan["scenes"]]}
    design["scenes"][0]["items"] = [{"label": "Sunlight and electricity from this source"}]
    if new_camera:
        design["scenes"][0]["shot"]["camera"] = "track"
    result = applied(plan, sources, design)
    record = result["ai_status"]["provenance"]["scenes"][0]
    assert "items.labels" in {change["field"] for change in record["changes"]}
    assert "items.labels" not in director.render_decisions(result["scenes"][0], result)
    assert "items.labels" not in record["rendered_fields"]
    assert record["rendered_nontext_fields"] == (["shot.camera"] if new_camera else [])
    assert director.has_model_visual_contribution(result) is new_camera
    assert result["scenes"][0]["media_layers"] == scene["media_layers"]


def _captured_mail_case(source):
    plan, sources = _material_case(source, "Mail armour uses linked rings.")
    # The captured shot covers only the mail sentence; steel belongs to its
    # section context, not this shot's source window.
    for entry in sources.values():
        entry["section"] = {**entry["section"], "narration":
            "Mail armour uses linked rings. Steel plate armour can deflect some blows away from the body."}
    # Relevant fields of the real local qwen3.5:4b response-0001, scene-003.
    # Only its ID is mapped to this isolated source/timeline fixture. This is
    # an acceptance regression, not a live inference or rendered approval.
    row = {"id": plan["scenes"][0]["id"], "template": "explain", "title": "Rings versus solid steel",
        "visual_goal": "Detail the structural difference between mail and plate armor.",
        "composition": "focus", "look": "blueprint", "rhythm": "measured", "palette": "sand",
        "staging": {"version": 1, "world": "generic", "action": "reveal", "camera": "wide"},
        "items": [{"label": "linked rings", "symbol": "circuit", "role": "subject"},
                  {"label": "solid steel plate", "symbol": "building", "role": "support"}],
        "shot": {"version": 1, "role": "detail", "framing": "macro", "technique": "stage2d",
                 "camera": "push", "transition": "cut", "focus": "Rings and solid steel",
                 "action": "Mail armour uses linked rings"},
        "mechanisms": [{"version": 1, "kind": "armour", "material": "mail", "action": "reveal"}]}
    return plan, sources, {"scenes": [row]}


def test_captured_grounded_mail_proposal_ignores_unrendered_legacy_actor_symbols(source):
    plan, sources, design = _captured_mail_case(source)
    result, journal, directed = deepcopy(plan), {}, set()
    director._apply_design(result, design, sources, provenance=journal, directed=directed)
    assert journal["scenes"][0]["status"] == "accepted"
    assert result["scenes"][0]["mechanism"] == design["scenes"][0]["mechanisms"][0]
    assert result["scenes"][0]["shot"] == design["scenes"][0]["shot"]
    assert not directed  # Hidden circuit/building metadata is not directed geometry.
    assert not director.shot_planner.grounding_issues(result["scenes"][0]["mechanism"], sources[result["scenes"][0]["id"]]["narration"])


@pytest.mark.parametrize("technique", ["stage2d", "diagram3d", "collage", "footage"])
def test_clearing_mechanism_or_requesting_unacquired_media_keeps_actor_grounding(source, technique):
    plan, sources, design = _captured_mail_case(source)
    design["scenes"][0]["mechanisms"] = []
    design["scenes"][0]["shot"]["technique"] = technique
    result = applied(plan, sources, design)
    record = result["ai_status"]["provenance"]["scenes"][0]
    assert record["status"] == "rejected" and record["reason"] == "unsupported_actors_or_action"
    assert result["scenes"] == plan["scenes"]


def test_unsupported_model_mechanism_cannot_replace_the_retained_grounded_mechanism(source):
    plan, sources, design = _captured_mail_case(source)
    design["scenes"][0]["mechanisms"][0].update(material="ceramic", action="penetrate")
    result = applied(plan, sources, design)
    record = result["ai_status"]["provenance"]["scenes"][0]
    assert record["status"] == "accepted"
    assert record["rejected_fields"]["mechanism"] == "unsupported_source_mechanism"
    assert result["scenes"][0]["mechanism"] == plan["scenes"][0]["mechanism"]


@pytest.mark.parametrize("overridden", [False, True])
def test_rejected_scene_rows_keep_separate_global_palette_provenance(source, overridden):
    _, _, plan, sources, design = source
    before = plan["creative_brief"]["palette"]
    after = "ocean" if before != "ocean" else "sand"
    design["creative_brief"] = {**deepcopy(plan["creative_brief"]), "palette": after}
    for row, scene in zip(design["scenes"], plan["scenes"]):
        row["title"] = "Unsupported 99999 panels"
        if overridden:
            scene["palette"] = before
    result = applied(plan, sources, design)
    report = result["ai_status"]["provenance"]
    assert report["accepted_scene_count"] == report["rendered_nontext_scene_count"] == 0
    assert all(row["status"] == "rejected" for row in report["scenes"])
    assert report["global_changes"] == [{"field": "creative_brief.palette", "before": before, "accepted": after,
        "rendered_scene_ids": [] if overridden else [scene["id"] for scene in result["scenes"]]}]
    assert report["rendered_global_decision_count"] == int(not overridden)
    assert director.has_model_visual_contribution(result) is not overridden
    result["creative_brief"]["palette"] = before
    assert director.has_model_visual_contribution(result) is False


def test_global_styles_ignored_by_shot_projection_cannot_qualify(source):
    _, _, plan, sources, design = source
    direction = deepcopy(plan["visual_direction"])
    direction["look"] = "poster" if direction["look"] != "poster" else "studio"
    direction["rhythm"] = "brisk" if direction["rhythm"] != "brisk" else "calm"
    design["visual_direction"] = direction
    for row in design["scenes"]:
        row["title"] = "Unsupported 99999 panels"
    result = applied(plan, sources, design)
    report = result["ai_status"]["provenance"]
    assert report["accepted_global_change_count"] == 2
    assert report["rendered_global_decision_count"] == 0
    assert all(change["rendered_scene_ids"] == [] for change in report["global_changes"])
    assert director.has_model_visual_contribution(result) is False
    report["global_changes"] *= 2
    assert director.has_model_visual_contribution(result) is False  # Over the three-field bound.


@pytest.mark.parametrize("invalid", ["two_subjects", "five_actors", "incompatible_staging"])
def test_structurally_invalid_row_is_rejected_without_discarding_other_model_rows(source, monkeypatch, invalid):
    script, timeline, _, _, _ = source
    timeline = deepcopy(timeline)
    timeline["duration"] = timeline["sections"][0]["duration"] = 8
    baseline = director.plan_scenes(script, timeline, use_ai=False)
    assert len(baseline["scenes"]) == 2
    design = {"visual_direction": deepcopy(baseline["visual_direction"]),
              "creative_brief": deepcopy(baseline["creative_brief"]),
              "scenes": [{key: deepcopy(scene[key]) for key in
                  ("id", "title", "visual_goal", "template", "items", "composition", "staging", "shot")}
                  for scene in baseline["scenes"]]}
    bad, good = design["scenes"]
    if invalid == "two_subjects":
        bad["items"] = [{"label": "Solar panels", "symbol": "solar", "role": "subject"},
                        {"label": "Sunlight", "symbol": "solar", "role": "subject"}]
    elif invalid == "five_actors":
        bad["items"] = [{"label": "Sunlight", "symbol": "concept", "role": "subject" if index == 0 else "support"}
                        for index in range(5)]
    else:
        bad["staging"].update(world="generic", action="float")
    good["shot"]["framing"] = "close" if good["shot"]["framing"] != "close" else "wide"
    untouched_proposal = deepcopy(design)
    monkeypatch.setattr(director, "_visual_ai_available", lambda: True)
    monkeypatch.setattr(director, "_request_design", lambda *args, **kwargs: deepcopy(design))
    result = director.plan_scenes(script, timeline)
    report = result["ai_status"]["provenance"]
    rejected, accepted = report["scenes"]
    assert result["ai_status"]["status"] == "planned"
    assert rejected["status"] == "rejected" and rejected["reason"] == "invalid_actor_or_staging_structure"
    assert rejected["changes"] == rejected["local_fallback_fields"] == rejected["rendered_fields"] == []
    assert result["scenes"][0] == baseline["scenes"][0]
    assert accepted["status"] == "accepted" and accepted["rendered_nontext_fields"] == ["shot.framing"]
    assert result["scenes"][1]["shot"]["framing"] == good["shot"]["framing"]
    assert report["rejected_scene_count"] == report["accepted_scene_count"] == 1
    assert report["global_changes"] == [] and report["rendered_global_decision_count"] == 0
    assert result["visual_direction"] == baseline["visual_direction"]
    assert result["creative_brief"] == baseline["creative_brief"]
    assert design == untouched_proposal  # Invalid roles/actions were never rewritten into valid model output.
    assert director.has_model_visual_contribution(result) is True
