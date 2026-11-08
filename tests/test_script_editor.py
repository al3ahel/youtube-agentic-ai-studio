"""The editorial agent keeps valid sources/identity and rejects unsafe rewrites."""
from copy import deepcopy
import json
import threading

import pytest

import config
from agents import script_editor


SCRIPT = {"title": "A river explained", "tags": ["river"], "sections": [
    {"id": 1, "title": "Beginning", "narration": "Water moves through this river and supports many living things along its banks.", "duration_seconds": 6},
    {"id": 2, "title": "Development", "narration": "Trees protect the soil while the river carries water toward the sea each day.", "duration_seconds": 6},
]}


def response(script=SCRIPT):
    return {"notes": ["A clearer spoken explanation."], "sections": [{"id": row["id"], "narration": row["narration"]} for row in script["sections"]]}


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(config, "SCRIPT_REVIEW_ENABLED", True)
    monkeypatch.setattr(config, "CHANNEL_PRESET", "general")


def test_valid_review_preserves_metadata_and_reuses_original_identity(monkeypatch):
    changed = response()
    changed["sections"][0]["narration"] = "This river carries water and supports many living things along its green banks."
    monkeypatch.setattr(script_editor, "generate", lambda *args, **kwargs: json.dumps(changed))
    result = script_editor.review_script(SCRIPT, {})
    assert result["quality_review"]["status"] == "reviewed"
    assert result["quality_review"]["rewritten"] is True
    assert result["title"] == SCRIPT["title"] and result["tags"] == SCRIPT["tags"]
    assert result["sections"][0]["title"] == "Beginning"
    assert SCRIPT["sections"][0]["narration"].startswith("Water moves")
    assert "do not verify" in result["quality_review"]["qualification"]


@pytest.mark.parametrize("change", [
    lambda value: value["sections"][0].update(id=12),
    lambda value: value["sections"][0].update(narration="Scientists say that 92 percent of all water is now toxic and dangerous everywhere."),
    lambda value: value["sections"][0].update(narration='Experts say "this river cures disease" and suggest visiting its banks every single morning.'),
    lambda value: value["sections"][0].update(narration="[LOUD MUSIC] Water moves through this river and supports many living things along its banks."),
    lambda value: value["sections"].pop(),
    lambda value: value.update(source="https://invented.invalid"),
])
def test_invalid_or_worse_edit_keeps_the_original_script(monkeypatch, change):
    changed = response()
    change(changed)
    monkeypatch.setattr(script_editor, "generate", lambda *args, **kwargs: json.dumps(changed))
    result = script_editor.review_script(SCRIPT, {})
    assert result["sections"] == SCRIPT["sections"]
    assert result["quality_review"]["status"] == "original_kept"


def test_no_model_is_a_reported_local_check_not_a_failed_video(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("No free quota")
    monkeypatch.setattr(script_editor, "generate", fail)
    result = script_editor.review_script(SCRIPT, {})
    assert result["sections"] == SCRIPT["sections"]
    assert result["quality_review"]["status"] == "original_kept"


def test_disabled_editor_and_news_calm_rules_are_explicit(monkeypatch):
    monkeypatch.setattr(config, "SCRIPT_REVIEW_ENABLED", False)
    monkeypatch.setattr(script_editor, "generate", lambda *args, **kwargs: pytest.fail("disabled editor called a provider"))
    assert script_editor.review_script(SCRIPT, {})["quality_review"]["status"] == "disabled"
    sensational = deepcopy(SCRIPT)
    sensational["sections"][0]["narration"] += " This is the shocking truth."
    assert script_editor.assess_script(sensational, "news")["score"] < script_editor.assess_script(SCRIPT, "news")["score"]


def test_news_review_cannot_turn_uncertainty_into_a_confirmed_fact(monkeypatch):
    monkeypatch.setattr(config, "CHANNEL_PRESET", "news")
    original = {"sections": [{"id": 1, "title": "Finding", "duration_seconds": 10,
        "narration": "According to the preliminary report, the agency has not confirmed a cause. Further measurements are needed before the team can draw conclusions."}]}
    attempted = "According to the preliminary report, the agency has now confirmed a cause. Further measurements support the conclusions that the team has already drawn."
    monkeypatch.setattr(script_editor, "generate", lambda *args, **kwargs: json.dumps({
        "notes": ["A clearer delivery."], "sections": [{"id": 1, "narration": attempted}]}))
    result = script_editor.review_script(original, {"grounding": {"facts": [original["sections"][0]["narration"]]}})
    assert result["sections"] == original["sections"]
    assert result["quality_review"]["rewritten"] is False
    assert result["quality_review"]["status"] == "suggestions"


def test_truncated_editor_narration_keeps_original_script_exactly(monkeypatch):
    original = deepcopy(SCRIPT)
    raw = json.dumps(response(original))
    # The old tolerant parser supplied a closing quote/brackets and accepted
    # the incomplete final word as an apparently successful editorial change.
    raw = raw[:raw.rfind("day.")] + "da"
    monkeypatch.setattr(script_editor, "generate", lambda *args, **kwargs: raw)
    result = script_editor.review_script(original, {})
    assert result["quality_review"]["status"] == "original_kept"
    assert result["quality_review"]["rewritten"] is False
    assert {key: result[key] for key in original} == original == SCRIPT


@pytest.mark.parametrize("trailing_comma", [False, True])
def test_editor_does_not_rewrite_literal_comma_braces(monkeypatch, trailing_comma):
    original = deepcopy(SCRIPT)
    original["sections"][0]["narration"] += " The literal marks ,} and ,] remain unchanged."
    raw = json.dumps(response(original))
    if trailing_comma:
        raw = raw[:-1] + ",}"
    monkeypatch.setattr(script_editor, "generate", lambda *args, **kwargs: raw)
    result = script_editor.review_script(original, {})
    assert result["quality_review"]["status"] == ("original_kept" if trailing_comma else "reviewed")
    assert result["quality_review"]["rewritten"] is False
    assert {key: result[key] for key in original} == original


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_editor_metadata_keeps_original(monkeypatch, number):
    raw = json.dumps(response()).replace('["A clearer spoken explanation."]', f"[{number}]")
    monkeypatch.setattr(script_editor, "generate", lambda *args, **kwargs: raw)
    result = script_editor.review_script(SCRIPT, {})
    assert result["quality_review"]["status"] == "original_kept"
    assert result["quality_review"]["rewritten"] is False
    assert {key: result[key] for key in SCRIPT} == SCRIPT


@pytest.mark.parametrize("field", ["notes", "narration"])
def test_invalid_utf8_editor_text_keeps_original(monkeypatch, field):
    changed = response()
    if field == "notes":
        changed["notes"] = ["An invalid Unicode note: \ud800"]
    else:
        changed["sections"][0]["narration"] += " \ud800"
    # JSON permits an escaped lone surrogate syntactically, but the committed
    # UTF-8 script cannot contain it. Reject notes as well as spoken text.
    raw = json.dumps(changed, ensure_ascii=True)
    monkeypatch.setattr(script_editor, "generate", lambda *args, **kwargs: raw)
    result = script_editor.review_script(SCRIPT, {})
    assert result["quality_review"]["status"] == "original_kept"
    assert result["quality_review"]["rewritten"] is False
    assert {key: result[key] for key in SCRIPT} == SCRIPT
    json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")


def test_complete_json_with_provider_output_limit_keeps_original(monkeypatch):
    changed = response()
    changed["sections"][0]["narration"] = "This river carries water and supports many living things along its green banks."
    def limited_response(prompt, **kwargs):
        assert kwargs["json_mode"] is True
        kwargs["provider_info"].update(provider="fixture", finish_reason="length")
        return json.dumps(changed)
    monkeypatch.setattr(script_editor, "generate", limited_response)
    result = script_editor.review_script(SCRIPT, {})
    assert result["quality_review"]["status"] == "original_kept"
    assert result["quality_review"]["rewritten"] is False
    assert {key: result[key] for key in SCRIPT} == SCRIPT


def test_editor_json_request_propagates_cancellation(monkeypatch):
    stop = threading.Event()
    def cancelled_response(prompt, **kwargs):
        assert kwargs["json_mode"] is True and kwargs["stop_event"] is stop
        raise InterruptedError("Stopped during editorial review")
    monkeypatch.setattr(script_editor, "generate", cancelled_response)
    with pytest.raises(InterruptedError, match="Stopped during editorial review"):
        script_editor.review_script(SCRIPT, {}, stop_event=stop)
