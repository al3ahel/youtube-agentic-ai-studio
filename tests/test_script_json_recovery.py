"""Incomplete model output must never become a saved or spoken script."""
from copy import deepcopy
import json
from types import SimpleNamespace
import threading

import pytest

import config
from agents import gemini_client, json_utils, scriptwriter
from apis import llm, llm_session


RESEARCH = {
    "topic": "The local river", "video_title": "Understanding the river",
    "key_points": ["The source describes the river's existing course."],
    "tags": ["river"], "news_evidence": "background_only", "as_of": "2026-11-08",
    "grounding": {"facts": "The dated field guide describes the river and its wooded banks.",
                  "sources": [{"title": "River field guide", "source": "Local archive",
                               "url": "https://example.org/river-guide", "published_at": "2020-01-01"}]},
}
PRIVATE_RESPONSE = "PRIVATE_MODEL_FRAGMENT_DO_NOT_FORWARD"
TRUNCATED_SCRIPT = (
    '{"title":"The river","sections":[{"narration":"The first section is complete."},'
    '{"narration":"' + PRIVATE_RESPONSE
)


def script_data():
    return {
        "title": "The river", "description": "A source-based look at the river.", "tags": ["river", "landscape"],
        "sections": [
            {"id": 99, "title": "Along the bank", "section_type": "intro",
             "narration": "  The river follows its course beside these trees.  ",
             "image_query": "river wooded banks", "bullet_points": ["The existing river course"],
             "caption_text": "Along the river", "duration_seconds": 9999},
            {"id": 99, "title": "A closing view", "section_type": "conclusion",
             "narration": "The field guide gives background rather than a new announcement.",
             "image_query": "river valley landscape", "duration_seconds": "model estimate is discarded"},
        ],
        "extra_metadata": {"kind": "valid JSON extension"},
    }


def answers(monkeypatch, responses):
    calls = []
    remaining = iter(responses)

    def generate(prompt, **kwargs):
        calls.append((prompt, kwargs))
        response = next(remaining)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(scriptwriter, "generate", generate)
    return calls


@pytest.mark.parametrize("wrapper", ["{}", "  {}\n", "```json\n{}\n```", "```\n{}\n```"])
def test_strict_parser_accepts_one_complete_object_and_preserves_string_contents(wrapper):
    original = {"narration": 'Keep literal ,} and ,] text, escaped "quotes", and a \\ backslash.',
                "nested": {"words": ["a", "b"], "number": 1.25}}
    raw = wrapper.format(json.dumps(original))
    assert json_utils.extract_complete_json(raw, "Scriptwriter") == original


@pytest.mark.parametrize("raw", [
    TRUNCATED_SCRIPT, '{"a":1', '{"a":"unfinished', '{"a":[1,2}',
    '{"a":1,}', '{"a":[1,2,]}', '{"a":2 + 3}',
    '{"a":1,"a":2}', '{"nested":{"a":1,"a":2}}',
    '{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}', '{"a":1e999}', '{"a":-1e999}',
    '[{"a":1}]', '"text"', "null", "17", "true",
    '{} {}', 'Prefix {}', '{} trailing prose',
    '```json\n{}', '```json\n{}\n```\n```json\n{}\n```',
    '```python\n{}\n```', PRIVATE_RESPONSE, "", None,
])
def test_strict_parser_rejects_incomplete_ambiguous_or_non_json_data_without_raw_excerpt(raw):
    with pytest.raises(ValueError) as failure:
        json_utils.extract_complete_json(raw, "Scriptwriter")
    assert "Scriptwriter" in str(failure.value)
    assert PRIVATE_RESPONSE not in str(failure.value)
    assert "Raw:" not in str(failure.value)


def test_strict_parser_rejects_oversized_response_before_it_can_be_adopted():
    raw = json.dumps({"unexpected": "x" * 200_000})
    with pytest.raises(ValueError, match="size|limit"):
        json_utils.extract_complete_json(raw)


def test_one_invalid_response_is_replaced_with_complete_valid_script(monkeypatch, capsys):
    original = script_data()
    calls = answers(monkeypatch, [TRUNCATED_SCRIPT, json.dumps(original)])
    stop = threading.Event()
    with config.profile_context({"CHANNEL_PRESET": "general", "VOICE_RATE": "+0%"}):
        result = scriptwriter.write_script(deepcopy(RESEARCH), stop_event=stop)
    assert len(calls) == 2
    assert all(options["json_mode"] is True and options["stop_event"] is stop for _, options in calls)
    assert calls[1][0].startswith(calls[0][0])
    assert all(PRIVATE_RESPONSE not in prompt for prompt, _ in calls)
    assert result["title"] == original["title"] and result["tags"] == original["tags"]
    assert result["extra_metadata"] == original["extra_metadata"]
    assert [row["id"] for row in result["sections"]] == [1, 2]
    assert [row["narration"] for row in result["sections"]] == [row["narration"].strip() for row in original["sections"]]
    assert [row["duration_seconds"] for row in result["sections"]] == [
        round(max(1, len(row["narration"].split()) / 135 * 60), 2) for row in original["sections"]]
    assert result["timing_source"] == "estimated_before_recording"
    report = capsys.readouterr()
    assert PRIVATE_RESPONSE not in report.out + report.err
    assert report.out.count("Requesting one complete replacement") == 1


def test_persistently_truncated_narration_stops_after_two_responses_without_silent_repair(monkeypatch, capsys):
    calls = answers(monkeypatch, [TRUNCATED_SCRIPT, TRUNCATED_SCRIPT])
    with pytest.raises(ValueError) as failure:
        scriptwriter.write_script(RESEARCH)
    assert len(calls) == 2
    message = str(failure.value)
    assert "Script" in message and "retry" in message.lower()
    assert "free/local model" in message or "configured" in message
    report = capsys.readouterr()
    assert PRIVATE_RESPONSE not in message + report.out + report.err
    assert all(PRIVATE_RESPONSE not in prompt for prompt, _ in calls)


@pytest.mark.parametrize("field,value", [
    ("title", ""), ("title", 12), ("title", "x" * 501),
    ("description", []), ("description", "x" * 10_001),
    ("tags", "river"), ("tags", ["river", 4]), ("tags", ["river"] * 51), ("tags", ["x" * 101]),
    ("sections", []), ("sections", [{"narration": "Only one section."}]),
    ("sections", [{"narration": "Too many sections."}] * 31),
    ("sections", "not a list"), ("sections", [{"narration": "First."}, "not a section"]),
])
def test_invalid_script_schema_gets_one_replacement_before_metadata_is_added(monkeypatch, field, value):
    invalid = script_data()
    invalid[field] = value
    calls = answers(monkeypatch, [json.dumps(invalid), json.dumps(script_data())])
    result = scriptwriter.write_script(RESEARCH)
    assert len(calls) == 2 and result["title"] == "The river"
    assert [row["id"] for row in result["sections"]] == [1, 2]


@pytest.mark.parametrize("field,value", [
    ("narration", "  "), ("narration", None), ("narration", ["spoken text"]), ("narration", "x" * 30_001),
    ("title", []), ("title", "x" * 501), ("section_type", 1), ("section_type", "x" * 33),
    ("image_query", False), ("image_query", "x" * 501), ("image_query_2", []),
    ("image_query_3", None), ("image_query_4", {}),
    ("caption_text", []), ("caption_text", "x" * 1001),
    ("bullet_points", "a point"), ("bullet_points", ["point", None]),
    ("bullet_points", ["point"] * 9), ("bullet_points", ["x" * 501]),
])
def test_invalid_section_metadata_is_not_saved_or_used_as_narration(monkeypatch, field, value):
    invalid = script_data()
    invalid["sections"][1][field] = value
    calls = answers(monkeypatch, [json.dumps(invalid), json.dumps(script_data())])
    result = scriptwriter.write_script(RESEARCH)
    assert len(calls) == 2 and len(result["sections"]) == 2
    assert result["sections"][1]["narration"] == script_data()["sections"][1]["narration"]


def test_missing_required_title_retries_but_missing_optional_fields_are_compatible(monkeypatch):
    minimal = {"title": "A small script", "sections": [{"narration": "Begin here."}, {"narration": "Finish here."}]}
    no_title = deepcopy(minimal)
    no_title.pop("title")
    calls = answers(monkeypatch, [json.dumps(no_title), json.dumps(minimal)])
    result = scriptwriter.write_script(RESEARCH)
    assert len(calls) == 2 and result["title"] == minimal["title"]
    assert "description" not in result and "tags" not in result
    assert [row["id"] for row in result["sections"]] == [1, 2]


@pytest.mark.parametrize("preset,video_type,style", [
    ("news", "normal", "A trend title is a lead"),
    ("asmr", "shorts", "No urgency, loud exclamations"),
])
def test_replacement_keeps_original_source_profile_format_and_estimated_timing(monkeypatch, preset, video_type, style):
    research = deepcopy(RESEARCH)
    before = deepcopy(research)
    calls = answers(monkeypatch, [TRUNCATED_SCRIPT, json.dumps(script_data())])
    profile = {"CHANNEL_NAME": "River profile", "CHANNEL_DESCRIPTION": "Faithful river stories for this audience.",
               "CHANNEL_PRESET": preset, "TARGET_DURATION_MINUTES": 3, "VOICE_RATE": "-15%"}
    with config.profile_context(profile):
        seconds, _, _, wpm = scriptwriter._word_budget(video_type)
        result = scriptwriter.write_script(research, video_type)
    assert len(calls) == 2 and calls[1][0].startswith(calls[0][0])
    original_prompt = calls[0][0]
    for required in (profile["CHANNEL_NAME"], profile["CHANNEL_DESCRIPTION"], style, research["topic"],
                     research["grounding"]["facts"], research["grounding"]["sources"][0]["url"],
                     "Retrieval time is not publication time or the event date."):
        assert required in original_prompt and required in calls[1][0]
    assert "background_only" in original_prompt
    assert research == before
    assert result["channel_preset"] == preset and result["video_type"] == video_type
    assert result["target_duration_seconds"] == seconds
    assert result["timing_source"] == "estimated_before_recording"
    assert all(row["duration_seconds"] == round(max(1, len(row["narration"].split()) / wpm * 60), 2)
               for row in result["sections"])


@pytest.mark.parametrize("cancel_at", [0, 1, 2])
def test_cancellation_before_or_during_replacement_never_returns_a_script(monkeypatch, cancel_at):
    stop = threading.Event()
    calls = []
    if cancel_at == 0:
        stop.set()

    def generate(prompt, **kwargs):
        calls.append(kwargs)
        if len(calls) == cancel_at:
            stop.set()
        return TRUNCATED_SCRIPT if len(calls) == 1 else json.dumps(script_data())

    monkeypatch.setattr(scriptwriter, "generate", generate)
    with llm_session.generation_session(stop_event=stop), pytest.raises(InterruptedError):
        scriptwriter.write_script(RESEARCH)
    assert len(calls) == cancel_at
    assert all(options["stop_event"] is stop for options in calls)


@pytest.mark.parametrize("error_type", [RuntimeError, TimeoutError, ValueError, InterruptedError])
@pytest.mark.parametrize("attempt", [1, 2])
def test_provider_exceptions_propagate_without_an_extra_writer_retry(monkeypatch, error_type, attempt):
    provider_error = error_type("Controlled adapter failure")
    responses = ([TRUNCATED_SCRIPT] if attempt == 2 else []) + [provider_error]
    calls = answers(monkeypatch, responses)
    with pytest.raises(error_type) as failure:
        scriptwriter.write_script(RESEARCH)
    assert failure.value is provider_error and len(calls) == attempt


def test_explicit_provider_json_rejection_uses_the_single_replacement(monkeypatch):
    calls = answers(monkeypatch, [llm_session.StructuredOutputError(), json.dumps(script_data())])
    result = scriptwriter.write_script(RESEARCH)
    assert len(calls) == 2 and result["title"] == "The river"
    assert "provider could not produce a valid JSON response" in calls[1][0]
    assert calls[1][0].startswith(calls[0][0])


@pytest.mark.parametrize("first", [TRUNCATED_SCRIPT, llm_session.StructuredOutputError()])
def test_provider_json_rejection_cannot_create_a_third_attempt(monkeypatch, first):
    calls = answers(monkeypatch, [first, llm_session.StructuredOutputError()])
    with pytest.raises(ValueError, match="after one automatic retry"):
        scriptwriter.write_script(RESEARCH)
    assert len(calls) == 2


def test_valid_json_reported_as_token_truncated_is_replaced(monkeypatch):
    calls = []

    def generate(prompt, **kwargs):
        calls.append((prompt, kwargs))
        kwargs["provider_info"].update(provider="groq", finish_reason="length" if len(calls) == 1 else "stop")
        return json.dumps(script_data())

    monkeypatch.setattr(scriptwriter, "generate", generate)
    result = scriptwriter.write_script(RESEARCH)
    assert len(calls) == 2 and len(result["sections"]) == 2
    assert calls[1][0].startswith(calls[0][0])


def test_two_token_limited_but_parseable_scripts_are_not_accepted(monkeypatch):
    calls = []

    def generate(prompt, **kwargs):
        calls.append(prompt)
        kwargs["provider_info"].update(provider="groq", finish_reason="length")
        return json.dumps(script_data())

    monkeypatch.setattr(scriptwriter, "generate", generate)
    with pytest.raises(ValueError, match="incomplete|limit|truncat"):
        scriptwriter.write_script(RESEARCH)
    assert len(calls) == 2


def test_invalid_unicode_in_unknown_metadata_gets_replaced_before_saving(monkeypatch):
    invalid = script_data()
    invalid["extra_metadata"] = {"provider_note": "\ud800"}
    calls = answers(monkeypatch, [json.dumps(invalid), json.dumps(script_data())])
    result = scriptwriter.write_script(RESEARCH)
    assert len(calls) == 2
    assert json.dumps(result, ensure_ascii=False).encode("utf-8")
    assert "UTF-8" in calls[1][0]


def test_replacement_shares_failed_provider_health_and_original_cloud_deadline(monkeypatch):
    for key, value in {"LLM_ROUTE": "auto", "OLLAMA_MODEL": "", "GEMINI_API_KEY": "fixture-gemini-key",
                       "GEMINI_MODEL": "gemini-2.5-flash", "GROQ_API_KEY": "fixture-groq-key",
                       "GROQ_MODEL": "openai/gpt-oss-120b", "OPENROUTER_API_KEY": ""}.items():
        monkeypatch.setattr(config, key, value)
    monkeypatch.setattr(gemini_client, "last_provider", gemini_client.last_provider)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(llm_session, "time", SimpleNamespace(monotonic=lambda: clock.now))
    gemini_calls, groq_calls = [], []

    def gemini(**kwargs):
        gemini_calls.append(kwargs)
        clock.now += 10
        raise TimeoutError()

    def groq(url, payload, **kwargs):
        assert url == "https://api.groq.com/openai/v1/chat/completions"
        groq_calls.append((payload, kwargs))
        clock.now += 7
        return {"choices": [{"message": {"content": TRUNCATED_SCRIPT if len(groq_calls) == 1 else json.dumps(script_data())},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(gemini_client, "_get_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=gemini)))
    monkeypatch.setattr(llm, "post_json", groq)
    monkeypatch.setattr(scriptwriter, "generate", gemini_client.generate)
    result = scriptwriter.write_script(RESEARCH)
    assert len(result["sections"]) == 2 and len(gemini_calls) == 1 and len(groq_calls) == 2
    assert groq_calls[1][1]["timeout"] < groq_calls[0][1]["timeout"]
    assert groq_calls[1][0]["messages"][0]["content"].startswith(groq_calls[0][0]["messages"][0]["content"])
    assert all(call[0]["response_format"] == {"type": "json_object"} for call in groq_calls)


@pytest.fixture
def saved_project(tmp_path, monkeypatch):
    from studio import preflight, runner
    import studio.state as run_state
    from video import narrator

    previous, was_stopped = deepcopy(run_state.state), run_state.stop_event.is_set()
    output = tmp_path / "project"
    output.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(config, "IMAGES_DIR", str(output / "images"))
    monkeypatch.setattr(config, "SCRIPT_REVIEW_ENABLED", False)
    monkeypatch.setattr(preflight, "check_environment", lambda *args: {"ok": True, "checks": []})
    monkeypatch.setattr(narrator, "generate_narration", lambda *a, **k: pytest.fail("A script-only run must not record speech"))
    journal = run_state.EventJournal()
    monkeypatch.setattr(run_state, "log_queue", journal)
    monkeypatch.setattr(runner, "log_queue", journal)
    old_script = script_data()
    for index, row in enumerate(old_script["sections"], 1):
        row.update(id=index, duration_seconds=4)
    files = {
        "script.json": json.dumps(old_script).encode(),
        "research.json": json.dumps(RESEARCH).encode(),
        "narration.wav": b"previous recording kept until a complete replacement exists",
        "narration.timings.json": b'{"version":1,"duration":8,"sections":[]}',
        "scene_plan.json": b'{"previous":"storyboard"}',
        "custom-thumbnail.jpg": b"previous custom thumbnail",
    }
    for name, content in files.items():
        (output / name).write_bytes(content)
    run_state.state.clear()
    run_state.state.update(run_state.fresh_project_state())
    run_state.state.update(script=deepcopy(old_script), research=deepcopy(RESEARCH), profile_id="default",
                           profile_name="Saved channel", description="A custom publishing description.",
                           audio_path=str(output / "narration.wav"), thumb_path=str(output / "custom-thumbnail.jpg"))
    run_state.stop_event.clear()
    try:
        yield output, files, deepcopy(run_state.state), journal
    finally:
        run_state.state.clear()
        run_state.state.update(previous)
        run_state.stop_event.set() if was_stopped else run_state.stop_event.clear()


@pytest.mark.parametrize("problem", ["truncated", "invalid_unicode"])
def test_failed_replacement_preserves_saved_project_and_never_reaches_narration(saved_project, monkeypatch, capsys, problem):
    from studio import runner
    from studio.state import state

    output, files, before, journal = saved_project
    raw = TRUNCATED_SCRIPT
    if problem == "invalid_unicode":
        invalid = script_data()
        invalid["extra_metadata"] = {"provider_note": "\ud800"}
        raw = json.dumps(invalid)
    calls = answers(monkeypatch, [raw, raw])
    runner.run_pipeline_thread(["script"], visual_mode="motion", profile_values={})
    assert len(calls) == 2 and state["error"] and state["stages"]["script"] == "error"
    assert not state["running"]
    for key in ("script", "research", "description", "audio_path", "thumb_path", "profile_id", "profile_name"):
        assert state[key] == before[key]
    assert all((output / name).read_bytes() == content for name, content in files.items())
    rows, _ = journal.read_after(0, timeout=0)
    assert any(row["kind"] == "error" for row in rows) and not any(row["kind"] == "done" for row in rows)
    report = capsys.readouterr()
    assert PRIVATE_RESPONSE not in state["error"] + report.out + report.err + json.dumps(rows)


def test_runner_commits_only_valid_replacement_then_invalidates_previous_media(saved_project, monkeypatch):
    from studio import runner
    from studio.state import state

    output, files, before, journal = saved_project
    replacement = script_data()
    replacement["title"] = "A complete replacement script"
    calls = []

    def generate(prompt, **kwargs):
        # Both model calls happen before the existing project is invalidated.
        assert state["script"] == before["script"] and state["audio_path"] == before["audio_path"]
        assert all((output / name).read_bytes() == content for name, content in files.items())
        calls.append((prompt, kwargs))
        return TRUNCATED_SCRIPT if len(calls) == 1 else json.dumps(replacement)

    monkeypatch.setattr(scriptwriter, "generate", generate)
    runner.run_pipeline_thread(["script"], visual_mode="motion", profile_values={})
    assert len(calls) == 2 and state["error"] is None and not state["running"]
    assert state["stages"]["script"] == "done"
    assert state["script"]["title"] == replacement["title"]
    assert state["script"]["timing_source"] == "estimated_before_recording"
    assert json.loads((output / "script.json").read_text()) == state["script"]
    assert state["research"] == before["research"] and (output / "research.json").read_bytes() == files["research.json"]
    assert state["audio_path"] is None and state["thumb_path"] is None
    assert not (output / "narration.wav").exists() and not (output / "narration.timings.json").exists()
    assert not (output / "scene_plan.json").exists()
    rows, _ = journal.read_after(0, timeout=0)
    assert any(row["kind"] == "done" for row in rows) and not any(row["kind"] == "error" for row in rows)
