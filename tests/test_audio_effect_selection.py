"""Scarce effect slots favor resolved causal contacts without losing sparsity."""
from copy import deepcopy

import pytest

from agents.audio_director import _select_effect_candidates


def candidate(label, start, *, contact=False):
    # Selection receives already-resolved inputs; the production resolver and
    # actual bundled files are exercised in test_audio_contact_timing.py.
    proof = {"method": "section_boundary"}
    if contact:
        proof = {"method": "automatic_visual_contact", "scene_id": "contact-shot",
                 "recording_family": "metal_contact", "recording_sha256": "a" * 64,
                 "visual_plan_sha256": "b" * 64, "measured_word_start": start}
    return {"event": {"section_id": label, "label": label},
            "asset": {"id": label, "sha256": "a" * 64}, "duration": 1.2,
            "timing": {"start": start, "timing_source": "visual_contact" if contact else "section_boundary",
                       "timing_provenance": proof}}


def labels(rows):
    return [row["event"]["label"] for row in rows]


def test_later_bound_contact_wins_the_single_slot_without_mutating_proposals():
    rows = [candidate("page turn", 5), candidate("illustrative hit", 19, contact=True)]
    before = deepcopy(rows)
    selected, omitted = _select_effect_candidates(rows, 1, 8)
    assert labels(selected) == ["illustrative hit"]
    assert [(row["event"]["label"], reason) for row, reason in omitted] == [
        ("page turn", "effect_budget_exhausted")]
    assert rows == before


def test_selection_priority_does_not_change_chronological_playback_order():
    rows = [candidate("last transition", 31), candidate("contact", 20, contact=True),
            candidate("first transition", 8)]
    selected, omitted = _select_effect_candidates(rows, 3, 8)
    assert labels(selected) == ["first transition", "contact", "last transition"]
    assert omitted == []


def test_contacts_keep_spacing_and_every_displaced_proposal_is_accounted_for():
    rows = [candidate("transition", 5), candidate("first contact", 14, contact=True),
            candidate("nearby contact", 19, contact=True), candidate("later contact", 28, contact=True)]
    selected, omitted = _select_effect_candidates(rows, 2, 8)
    assert labels(selected) == ["first contact", "later contact"]
    assert [(row["event"]["label"], reason) for row, reason in omitted] == [
        ("transition", "effect_budget_exhausted"), ("nearby contact", "effect_spacing_conflict")]
    assert sorted(labels(selected) + labels([row for row, _ in omitted])) == sorted(labels(rows))


def test_spacing_is_checked_on_both_sides_of_a_prioritized_contact():
    rows = [candidate("left conflict", 25), candidate("right conflict", 35),
            candidate("contact", 30, contact=True), candidate("distant transition", 10)]
    selected, omitted = _select_effect_candidates(rows, 3, 8)
    assert labels(selected) == ["distant transition", "contact"]
    assert labels([row for row, _ in omitted]) == ["left conflict", "right conflict"]
    assert {reason for _, reason in omitted} == {"effect_spacing_conflict"}


@pytest.mark.parametrize("contact", [False, True])
def test_equal_priority_and_time_preserve_original_proposal_order(contact):
    rows = [candidate("second named but first proposed", 20, contact=contact),
            candidate("first named but second proposed", 20, contact=contact)]
    selected, omitted = _select_effect_candidates(rows, 1, 8)
    assert selected == [rows[0]]
    assert omitted == [(rows[1], "effect_budget_exhausted")]


def test_zero_budget_records_every_proposal_without_selecting_any():
    rows = [candidate("later contact", 20, contact=True), candidate("earlier transition", 5)]
    selected, omitted = _select_effect_candidates(rows, 0, 8)
    assert selected == []
    assert labels([row for row, _ in omitted]) == ["earlier transition", "later contact"]
    assert len(omitted) == len(rows)
    assert {reason for _, reason in omitted} == {"effect_budget_exhausted"}


@pytest.mark.parametrize("change", [
    lambda row: row["timing"].update(timing_source="measured_word"),
    lambda row: row["timing"].update(timing_source="section_boundary"),
    lambda row: row["timing"]["timing_provenance"].update(method="explicit_model_cue"),
    lambda row: row["timing"]["timing_provenance"].pop("visual_plan_sha256"),
    lambda row: row["timing"]["timing_provenance"].pop("scene_id"),
    lambda row: row["timing"]["timing_provenance"].update(recording_sha256="unverified"),
    lambda row: row["asset"].update(sha256="c" * 64),
])
def test_contact_labels_or_incomplete_bindings_do_not_receive_priority(change):
    asserted = candidate("Verified dramatic impact", 20, contact=True)
    change(asserted)
    transition = candidate("ordinary transition", 5)
    selected, omitted = _select_effect_candidates([asserted, transition], 1, 8)
    assert selected == [transition]
    assert omitted == [(asserted, "effect_budget_exhausted")]


def test_ordinary_transitions_retain_the_existing_stable_chronological_policy():
    rows = [candidate("late", 30), candidate("early", 10), candidate("too close", 12),
            candidate("middle", 20)]
    selected, omitted = _select_effect_candidates(rows, 2, 8)
    assert labels(selected) == ["early", "middle"]
    assert [(row["event"]["label"], reason) for row, reason in omitted] == [
        ("too close", "effect_spacing_conflict"), ("late", "effect_budget_exhausted")]
