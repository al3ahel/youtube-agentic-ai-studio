"""Legacy text generation must not bypass OpenRouter's free-only boundary."""
import json

import pytest
import requests

from apis import llm, openrouter


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_ROUTE", "auto", raising=False)
    monkeypatch.setattr(llm, "_announced_migrations", set())
    monkeypatch.setattr(llm.config, "GROQ_API_KEY", "", raising=False)
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "test-key-secret-123456789", raising=False)
    monkeypatch.setattr(llm.config, "OPENROUTER_MODEL", "openrouter/free", raising=False)
    monkeypatch.setattr(llm.config, "KEYLESS_LLM_FALLBACK", False, raising=False)
    monkeypatch.setattr(llm.config, "OPENROUTER_DAILY_REQUEST_BUDGET", 45, raising=False)
    monkeypatch.setattr(openrouter, "_usage", {})
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected live network request")

    monkeypatch.setattr(llm, "post_json", forbidden)
    monkeypatch.setattr(openrouter, "discover_models", forbidden)


def response(text="Useful explanation"):
    return {"choices": [{"message": {"content": text}}]}


@pytest.mark.parametrize("model", ["openai/paid-model", "meta-llama/llama-3.3-70b-instruct", "unsafe"])
def test_saved_paid_or_invalid_models_make_no_inference_call(monkeypatch, model):
    monkeypatch.setattr(llm.config, "OPENROUTER_MODEL", model)
    with pytest.raises(RuntimeError, match="select openrouter/free"):
        llm.chat("Explain this")


def test_retired_free_model_does_not_fall_back_to_paid_sibling(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
    monkeypatch.setattr(openrouter, "discover_models", lambda: [])
    with pytest.raises(RuntimeError, match="model_unavailable"):
        llm.chat("Explain this")


def test_plain_text_free_model_does_not_require_schema_capability(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_MODEL", "example/text:free")
    monkeypatch.setattr(openrouter, "discover_models", lambda: [
        {"id": "example/text:free", "supports_schema": False, "supports_vision": False}])
    calls = []
    monkeypatch.setattr(llm, "post_json", lambda url, payload, **kw: calls.append(payload) or response())
    assert llm.chat("Explain this") == "Useful explanation"
    assert calls[0]["model"] == "example/text:free"


def test_router_default_redacts_prompt_and_returned_text(monkeypatch, capsys):
    key = llm.config.OPENROUTER_API_KEY
    calls = []
    monkeypatch.setattr(llm, "post_json", lambda url, payload, **kw: calls.append((url, payload, kw)) or response("Done " + key))
    text = llm.chat("Source includes " + key)
    assert key not in text
    assert key not in json.dumps(calls[0][1])
    assert calls[0][2]["headers"]["Authorization"] == "Bearer " + key
    assert key not in capsys.readouterr().out


@pytest.mark.parametrize("status", [401, 402, 403, 429])
def test_free_quota_errors_not_retried_or_printed(monkeypatch, capsys, status):
    calls = []
    error_response = requests.Response()
    error_response.status_code = status

    def fail(*args, **kwargs):
        calls.append(args)
        raise requests.HTTPError("raw provider secret " + llm.config.OPENROUTER_API_KEY,
                                 response=error_response)

    monkeypatch.setattr(llm, "post_json", fail)
    with pytest.raises(RuntimeError, match="quota_or_access_unavailable"):
        llm.chat("Explain this")
    assert len(calls) == 1
    assert llm.config.OPENROUTER_API_KEY not in capsys.readouterr().out


def test_transient_outage_has_two_call_limit_and_no_paid_model(monkeypatch):
    calls = []

    def fail(url, payload, **kwargs):
        calls.append(payload["model"])
        raise requests.ConnectionError("Connection unavailable")

    monkeypatch.setattr(llm, "post_json", fail)
    with pytest.raises(RuntimeError):
        llm.chat("Explain this")
    assert calls == ["openrouter/free", "openrouter/free"]


def test_shared_free_budget_blocks_text_request_before_network(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_DAILY_REQUEST_BUDGET", 1)
    openrouter._claim_request(llm.config.OPENROUTER_API_KEY)
    with pytest.raises(RuntimeError, match="local_quota_exhausted"):
        llm.chat("Explain this")


def test_reported_charge_stops_response_without_fallback_to_paid(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "post_json", lambda *a, **k: calls.append(a) or {**response(), "usage": {"cost": 1}})
    with pytest.raises(RuntimeError, match="unexpected_cost"):
        llm.chat("Explain this")
    assert len(calls) == 1


@pytest.mark.parametrize("saved,expected", [
    ("llama-3.3-70b-versatile", "openai/gpt-oss-120b"),
    ("llama-3.1-8b-instant", "openai/gpt-oss-20b"),
    ("openai/gpt-oss-120b", "openai/gpt-oss-120b"),
    ("custom/future-model", "custom/future-model"),
    ("", "openai/gpt-oss-120b"),
])
def test_groq_runtime_migrates_only_known_retired_defaults(monkeypatch, saved, expected):
    monkeypatch.setattr(llm.config, "GROQ_API_KEY", "groq-free-account-test-key")
    monkeypatch.setattr(llm.config, "GROQ_MODEL", saved)
    providers = llm.providers()
    assert providers[0][0] == "groq" and providers[0][2] == expected
    assert llm.config.GROQ_MODEL == saved  # No settings mutation while routing.


def test_retired_groq_model_is_not_submitted_and_migration_note_is_once(monkeypatch, capsys):
    monkeypatch.setattr(llm.config, "GROQ_API_KEY", "groq-free-account-test-key")
    monkeypatch.setattr(llm.config, "GROQ_MODEL", "llama-3.3-70b-versatile")
    calls = []
    monkeypatch.setattr(llm, "post_json", lambda url, payload, **kwargs: calls.append(payload["model"]) or response())
    assert llm.chat("First prompt") == "Useful explanation"
    assert llm.chat("Second prompt") == "Useful explanation"
    assert calls == ["openai/gpt-oss-120b", "openai/gpt-oss-120b"]
    assert capsys.readouterr().out.count("retired from the free tier") == 1
    assert llm.config.GROQ_MODEL == "llama-3.3-70b-versatile"
