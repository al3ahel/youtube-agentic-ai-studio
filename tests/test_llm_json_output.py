"""JSON output is opt-in at the provider boundary and keeps routing guarantees."""
from types import SimpleNamespace
import json
import threading

import pytest
import requests

import config
from agents import gemini_client
from apis import llm, local_llm, openrouter
from apis.llm_session import StructuredOutputError, request_scope


@pytest.fixture(autouse=True)
def offline_routes(monkeypatch):
    for name, value in {
        "LLM_ROUTE": "auto", "OLLAMA_MODEL": "", "OLLAMA_BASE_URL": "http://localhost:11434",
        "GEMINI_API_KEY": "", "GEMINI_MODEL": "gemini-2.5-flash",
        "GROQ_API_KEY": "", "GROQ_MODEL": "openai/gpt-oss-120b",
        "OPENROUTER_API_KEY": "", "OPENROUTER_MODEL": "openrouter/free",
        "OPENROUTER_DAILY_REQUEST_BUDGET": 45,
    }.items():
        monkeypatch.setattr(config, name, value, raising=False)
    monkeypatch.setattr(openrouter, "_usage", {})

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected provider or discovery request")

    monkeypatch.setattr(gemini_client, "_get_client", forbidden)
    monkeypatch.setattr(llm, "post_json", forbidden)
    monkeypatch.setattr(openrouter, "discover_models", forbidden)
    monkeypatch.setattr(local_llm, "_request_json", forbidden)


def _hosted(monkeypatch, provider="groq", *, text='{"title":"Complete script"}',
            reason="stop", cost=None, error=None):
    monkeypatch.setattr(config, provider.upper() + "_API_KEY", provider + "-private-test-key")
    calls = []

    def respond(url, payload, **options):
        calls.append((url, payload, options))
        if error is not None:
            raise error
        result = {"choices": [{"message": {"content": text}, "finish_reason": reason}]}
        if cost is not None:
            result["usage"] = {"cost": cost}
        return result

    monkeypatch.setattr(llm, "post_json", respond)
    return calls


def _gemini(monkeypatch, *, text='{"title":"Complete script"}', reason="STOP", error=None):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "gemini-private-test-key")
    calls = []

    def respond(**options):
        calls.append(options)
        if error is not None:
            raise error
        return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=reason)])

    monkeypatch.setattr(gemini_client, "_get_client", lambda: SimpleNamespace(
        models=SimpleNamespace(generate_content=respond)))
    return calls


def _local(monkeypatch, *, reason="stop", text='{"title":"Complete script"}', remote=False):
    monkeypatch.setattr(config, "LLM_ROUTE", "local")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "tiny:latest")
    calls = []
    records = {"models": [{"name": "tiny:latest", "model": "tiny:latest", "size": 1000000,
                          "digest": "a" * 64, "details": {"family": "example", "format": "gguf"}}]}

    def request(origin, endpoint, **options):
        calls.append((origin, endpoint, options))
        if endpoint == "/api/tags":
            return records
        if endpoint == "/api/show":
            return {"model_info": {"general.architecture": "example"}, "capabilities": ["completion"],
                    **({"remote_host": "https://ollama.com"} if remote else {})}
        assert endpoint == "/api/chat"
        return {"done": True, "done_reason": reason, "message": {"content": text}}

    monkeypatch.setattr(local_llm, "_request_json", request)
    return calls


@pytest.mark.parametrize("json_mode", [False, True])
def test_gemini_json_mime_is_opt_in_without_schema_or_budget_changes(monkeypatch, json_mode):
    from google.genai import types

    calls = _gemini(monkeypatch, reason=types.FinishReason.STOP)
    info = {}
    assert gemini_client.generate("Write a script", json_mode=json_mode, provider_info=info).startswith('{"title"')
    assert len(calls) == 1 and calls[0]["model"] == config.GEMINI_MODEL
    options = calls[0]["config"]
    assert isinstance(options, types.GenerateContentConfig)
    assert options.response_mime_type == ("application/json" if json_mode else None)
    assert bool(options.system_instruction) is json_mode
    assert options.response_schema is None and options.response_json_schema is None
    assert options.temperature == .9
    assert 0 < options.http_options.timeout <= 60000
    assert options.http_options.retry_options.attempts == 1
    assert info == {"provider": "gemini", "model": config.GEMINI_MODEL,
                    **({"finish_reason": "stop"} if json_mode else {})}


@pytest.mark.parametrize("provider", ["groq", "openrouter"])
@pytest.mark.parametrize("json_mode", [False, True])
def test_hosted_json_mode_is_sent_with_explicit_instruction_and_no_schema(monkeypatch, provider, json_mode):
    calls = _hosted(monkeypatch, provider)
    info = {}
    answer = llm.chat("Write a script", json_mode=json_mode, provider_info=info)
    assert answer == '{"title":"Complete script"}' and len(calls) == 1
    url, body, options = calls[0]
    assert url == {"groq": "https://api.groq.com/openai/v1/chat/completions",
                   "openrouter": "https://openrouter.ai/api/v1/chat/completions"}[provider]
    model = config.GROQ_MODEL if provider == "groq" else "openrouter/free"
    assert body["model"] == model and body["temperature"] == .9
    assert body["messages"][-1] == {"role": "user", "content": "Write a script"}
    if json_mode:
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][0]["role"] == "system"
        assert "JSON object" in body["messages"][0]["content"]
        assert body.get("provider") == ({"require_parameters": True} if provider == "openrouter" else None)
    else:
        assert set(body) == {"model", "messages", "temperature"}
        assert len(body["messages"]) == 1
    assert info == {"provider": provider, "model": model,
                    **({"finish_reason": "stop"} if json_mode else {})}
    assert 0 < options["timeout"] <= 60


@pytest.mark.parametrize("entry", [gemini_client.generate, llm.chat])
@pytest.mark.parametrize("json_mode", [False, True])
def test_local_json_mode_uses_official_format_after_installed_weights_checks(monkeypatch, entry, json_mode):
    calls = _local(monkeypatch)
    # All hosted keys are present, so an accidental local-only escape fails the test.
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setattr(config, name, "configured-private-test-key")
    info, stop = {}, threading.Event()
    with llm.generation_session(stop_event=stop):
        assert entry("Write a script", json_mode=json_mode, provider_info=info) == '{"title":"Complete script"}'
    assert [row[1] for row in calls] == ["/api/tags", "/api/show", "/api/chat"]
    assert all(row[0] == "http://127.0.0.1:11434" and row[2]["stop_event"] is stop for row in calls)
    body = calls[-1][2]["body"]
    assert body["model"] == "tiny:latest" and body["stream"] is False
    assert body.get("format") == ("json" if json_mode else None)
    assert len(body["messages"]) == (2 if json_mode else 1)
    assert body["options"]["num_predict"] == 8192  # No guessed provider token changes.
    assert calls[-1][2]["timeout"] == local_llm.MAX_GENERATION_SECONDS
    assert info == {"provider": "ollama", "model": "tiny:latest",
                    **({"finish_reason": "stop"} if json_mode else {})}


def test_json_fallback_keeps_job_failure_memory_without_leaking_mode_to_later_calls(monkeypatch):
    gemini_calls = _gemini(monkeypatch, error=TimeoutError())
    hosted_calls = _hosted(monkeypatch)
    with llm.generation_session():
        first, second = {}, {}
        gemini_client.generate("Write a script", json_mode=True, provider_info=first)
        gemini_client.generate("Write ordinary prose", provider_info=second)
    assert len(gemini_calls) == 1 and len(hosted_calls) == 2
    assert hosted_calls[0][1]["response_format"] == {"type": "json_object"}
    assert "response_format" not in hosted_calls[1][1]
    assert first == {"provider": "groq", "model": config.GROQ_MODEL, "finish_reason": "stop"}
    assert second == {"provider": "groq", "model": config.GROQ_MODEL}


def test_openrouter_json_object_does_not_demand_schema_or_change_selected_free_model(monkeypatch):
    calls = _hosted(monkeypatch, "openrouter")
    monkeypatch.setattr(config, "OPENROUTER_MODEL", "example/object-only:free")
    monkeypatch.setattr(openrouter, "discover_models", lambda: [{
        "id": "example/object-only:free", "supports_schema": False,
        "supported_parameters": ["response_format"], "supports_vision": False,
    }])
    assert llm.chat("Write a script", json_mode=True) == '{"title":"Complete script"}'
    assert calls[0][1]["model"] == "example/object-only:free"
    assert calls[0][1]["provider"] == {"require_parameters": True}
    assert "models" not in calls[0][1]  # No implicit or paid model fallback array.


def test_unsupported_json_endpoint_is_never_retried_without_constraint(monkeypatch, capsys):
    response = requests.Response()
    response.status_code = 400
    calls = _hosted(monkeypatch, "openrouter", error=requests.HTTPError(
        "PRIVATE_PROVIDER_BODY_WITH_PROMPT", response=response))
    with pytest.raises(RuntimeError) as error:
        llm.chat("Write a script", json_mode=True)
    assert len(calls) == 1
    assert calls[0][1]["response_format"] == {"type": "json_object"}
    assert calls[0][1]["provider"] == {"require_parameters": True}
    assert "PRIVATE_PROVIDER_BODY" not in str(error.value) + capsys.readouterr().out


@pytest.mark.parametrize("selected", ["example/paid", "example/removed:free"])
def test_json_mode_does_not_bypass_current_free_model_validation(monkeypatch, selected):
    calls = _hosted(monkeypatch, "openrouter")
    monkeypatch.setattr(config, "OPENROUTER_MODEL", selected)
    monkeypatch.setattr(openrouter, "discover_models", lambda: [])
    with pytest.raises(RuntimeError, match="paid_model_rejected|model_unavailable"):
        llm.chat("Write a script", json_mode=True)
    assert calls == [] and openrouter._usage == {}


def test_json_mode_keeps_openrouter_quota_guard_before_transport(monkeypatch):
    calls = _hosted(monkeypatch, "openrouter")
    monkeypatch.setattr(config, "OPENROUTER_DAILY_REQUEST_BUDGET", 1)
    openrouter._claim_request(config.OPENROUTER_API_KEY)
    with pytest.raises(RuntimeError, match="local_quota_exhausted"):
        llm.chat("Write a script", json_mode=True)
    assert calls == []


@pytest.mark.parametrize("cost", ["0.001", "NaN"])
def test_json_response_never_bypasses_reported_cost_validation(monkeypatch, cost):
    calls = _hosted(monkeypatch, "openrouter", cost=cost)
    with pytest.raises(RuntimeError, match="unexpected_cost"):
        llm.chat("Write a script", json_mode=True)
    assert len(calls) == 1


@pytest.mark.parametrize("provider", ["gemini", "groq", "openrouter", "ollama"])
def test_declared_truncation_is_safe_request_owned_metadata_for_writer_validation(monkeypatch, provider):
    if provider == "gemini":
        from google.genai import types
        calls = _gemini(monkeypatch, reason=types.FinishReason.MAX_TOKENS)
    elif provider == "ollama":
        calls = _local(monkeypatch, reason="length")
    else:
        calls = _hosted(monkeypatch, provider, reason="length")
    info = {}
    # Even a parseable complete object must be rejected by the writer if the
    # provider declared a limit. No hidden retry is added in the adapter.
    assert gemini_client.generate("Write a script", json_mode=True, provider_info=info) == '{"title":"Complete script"}'
    assert info["provider"] == provider and info["finish_reason"] == "length"
    assert len(calls) == (3 if provider == "ollama" else 1)


@pytest.mark.parametrize("provider", ["gemini", "groq", "openrouter", "ollama"])
def test_untrusted_finish_reason_is_not_returned_or_logged(monkeypatch, provider, capsys):
    marker = "PRIVATE_COMPLETION_METADATA"
    if provider == "gemini":
        _gemini(monkeypatch, reason=marker)
    elif provider == "ollama":
        _local(monkeypatch, reason=marker)
    else:
        _hosted(monkeypatch, provider, reason=marker)
    info = {}
    gemini_client.generate("Write a script", json_mode=True, provider_info=info)
    assert "finish_reason" not in info
    assert marker not in repr(info) + capsys.readouterr().out


def test_plain_local_truncation_error_is_unchanged(monkeypatch):
    _local(monkeypatch, reason="length")
    with pytest.raises(local_llm.LocalLLMError) as error:
        local_llm.generate("Write ordinary prose")
    assert error.value.code == "incomplete_response"


def test_local_json_mode_cannot_use_a_remote_alias(monkeypatch):
    calls = _local(monkeypatch, remote=True)
    with pytest.raises(local_llm.LocalLLMError) as error:
        gemini_client.generate("Write a script", json_mode=True)
    assert error.value.code == "cloud_model_rejected"
    assert [row[1] for row in calls] == ["/api/tags", "/api/show"]


@pytest.mark.parametrize("entry", [gemini_client.generate, llm.chat])
def test_json_requests_inherit_cancellation_before_any_provider_work(entry):
    stop = threading.Event()
    stop.set()
    with llm.generation_session(stop_event=stop), pytest.raises(InterruptedError):
        entry("Write a script", json_mode=True)


def test_stop_during_json_fallback_does_not_publish_success(monkeypatch):
    stop, info = threading.Event(), {}
    monkeypatch.setattr(config, "GROQ_API_KEY", "groq-private-test-key")

    def respond(*args, **kwargs):
        stop.set()
        return {"choices": [{"message": {"content": '{"title":"Late response"}'}, "finish_reason": "stop"}]}

    monkeypatch.setattr(llm, "post_json", respond)
    with llm.generation_session(stop_event=stop) as session, pytest.raises(InterruptedError):
        gemini_client.generate("Write a script", json_mode=True, provider_info=info)
    assert info == {} and session.last_success == {} and session.failures == {}


def test_adapters_do_not_repair_or_log_invalid_json_behind_the_writers_back(monkeypatch, capsys):
    invalid = '{"narration": PRIVATE_RAW_RESPONSE'
    calls = _hosted(monkeypatch, text=invalid)
    assert gemini_client.generate("Write a script", json_mode=True) == invalid
    assert len(calls) == 1 and "PRIVATE_RAW_RESPONSE" not in capsys.readouterr().out


@pytest.mark.parametrize("entry", [gemini_client.generate, llm.chat, local_llm.generate])
def test_json_mode_requires_an_explicit_boolean_before_provider_work(entry):
    with pytest.raises((ValueError, local_llm.LocalLLMError), match="json_mode"):
        entry("Write a script", json_mode="false")


def _invalid_json_error(*, status=400, payload=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload if payload is not None else {
        "error": {"code": "json_validate_failed", "message": "PRIVATE_PROMPT_OR_KEY",
                  "failed_generation": "PRIVATE_INVALID_NARRATION"},
    }).encode()
    return requests.HTTPError("PRIVATE_PROMPT_OR_KEY", response=response)


@pytest.mark.parametrize("entry", [gemini_client.generate, llm.chat])
def test_groq_content_validation_error_returns_to_caller_without_retry_or_poisoning(monkeypatch, capsys, entry):
    monkeypatch.setattr(config, "GROQ_API_KEY", "groq-private-test-key")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "router-private-test-key")
    calls = []

    def respond(url, payload, **options):
        calls.append((url, payload, options))
        if len(calls) == 1:
            raise _invalid_json_error()
        return {"choices": [{"message": {"content": '{"title":"Complete replacement"}'}, "finish_reason": "stop"}]}

    monkeypatch.setattr(llm, "post_json", respond)
    with llm.generation_session() as session, request_scope() as budget:
        info = {}
        with pytest.raises(StructuredOutputError) as error:
            entry("Write a script", json_mode=True, provider_info=info)
        assert str(error.value) == "The provider rejected the generated JSON as invalid."
        assert len(calls) == 1 and info == {} and session.failures == {} and session.last_success == {}
        first_deadline = budget.deadline
        # The caller can explicitly spend its one replacement in the SAME
        # budget. The adapter neither retries nor switches model/providers.
        answer = entry("Write a complete replacement", json_mode=True, provider_info=info)
        assert answer == '{"title":"Complete replacement"}' and budget.deadline == first_deadline
        assert session.failures == {}
    assert len(calls) == 2 and all("api.groq.com" in call[0] for call in calls)
    assert all(call[1]["response_format"] == {"type": "json_object"} for call in calls)
    assert all(call[1]["model"] == config.GROQ_MODEL for call in calls)
    assert "PRIVATE_" not in str(error.value) + capsys.readouterr().out


@pytest.mark.parametrize("provider,status,json_mode,payload", [
    ("groq", 400, True, {"error": {"code": "invalid_request_error", "message": "json_validate_failed"}}),
    ("groq", 400, True, {"code": "json_validate_failed"}),
    ("groq", 400, True, [{"error": {"code": "json_validate_failed"}}]),
    ("groq", 400, True, {"error": {"code": "json_validate_failed", "failed_generation": "x" * 65536}}),
    ("groq", 429, True, {"error": {"code": "json_validate_failed"}}),
    ("groq", 400, False, {"error": {"code": "json_validate_failed"}}),
    ("openrouter", 400, True, {"error": {"code": "json_validate_failed"}}),
])
def test_only_exact_bounded_groq_json_400_is_a_content_error(monkeypatch, provider, status, json_mode, payload):
    calls = _hosted(monkeypatch, provider, error=_invalid_json_error(status=status, payload=payload))
    with llm.generation_session() as session, pytest.raises(RuntimeError) as error:
        gemini_client.generate("Write a script", json_mode=json_mode)
    assert not isinstance(error.value, StructuredOutputError)
    assert len(calls) == 1 and session.failures


def test_stop_wins_over_groq_json_validation_replacement(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "groq-private-test-key")
    stop, calls = threading.Event(), []

    def respond(*args, **kwargs):
        calls.append(args)
        stop.set()
        raise _invalid_json_error()

    monkeypatch.setattr(llm, "post_json", respond)
    with llm.generation_session(stop_event=stop) as session, pytest.raises(InterruptedError):
        gemini_client.generate("Write a script", json_mode=True)
    assert len(calls) == 1 and session.failures == {}
