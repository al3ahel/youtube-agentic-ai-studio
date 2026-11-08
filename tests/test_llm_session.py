"""A job remembers failed free routes without poisoning another job or channel."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

try:
    import httpx
except ImportError:
    import httpx2 as httpx
import pytest
import requests

import config
from agents import gemini_client
from apis import llm, llm_session, local_llm, openrouter


@pytest.fixture
def routing(monkeypatch):
    monkeypatch.setattr(config, "LLM_ROUTE", "auto")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "gemini-fixture-secret-key")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(config, "GROQ_API_KEY", "groq-fixture-secret-key")
    monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(openrouter, "_usage", {})
    attempts = {"gemini": [], "fallback": []}
    def unexpected(**kwargs):
        pytest.fail("A test must explicitly implement its Gemini response")
    client = SimpleNamespace(models=SimpleNamespace(generate_content=unexpected))
    monkeypatch.setattr(gemini_client, "_get_client", lambda: client)
    def fallback(url, payload, **kwargs):
        attempts["fallback"].append((url, payload, kwargs))
        return {"choices": [{"message": {"content": "Free fallback answer"}}]}
    monkeypatch.setattr(llm, "post_json", fallback)
    return client, attempts


def http_error(status, message="Private response body must not be logged"):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(message, response=response)


def test_same_job_skips_timed_out_gemini_after_groq_answers(routing, capsys):
    client, calls = routing
    def timeout(**options):
        calls["gemini"].append(options)
        raise httpx.ReadTimeout("")  # Common SDK transport failure has no useful str(error).
    client.models.generate_content = timeout
    with llm.generation_session() as session:
        for stage in ("Script review", "Sound direction"):
            actual = {}
            assert gemini_client.generate(stage, provider_info=actual) == "Free fallback answer"
            assert actual == {"provider": "groq", "model": "openai/gpt-oss-120b"}
        assert session.last_success == actual
    assert len(calls["gemini"]) == 1 and len(calls["fallback"]) == 2
    assert calls["gemini"][0]["model"] == "gemini-2.5-flash"
    options = calls["gemini"][0]["config"].http_options
    assert options.timeout <= 30000 and options.retry_options.attempts == 1
    assert config.GEMINI_MODEL == "gemini-2.5-flash"
    logs = capsys.readouterr().out
    assert logs.count("Trying Gemini") == 1 and "Skipping Gemini for this generation: request timed out earlier" in logs
    assert "Groq openai/gpt-oss-120b answered" in logs and "Gemini gemini-2.5-flash answered" not in logs


def test_new_job_retries_the_original_preference_without_any_global_blacklist(routing):
    client, calls = routing
    def answer(**options):
        calls["gemini"].append(options["model"])
        if len(calls["gemini"]) == 1:
            raise TimeoutError()
        return SimpleNamespace(text="Gemini recovered")
    client.models.generate_content = answer
    with llm.generation_session():
        assert gemini_client.generate("First job") == "Free fallback answer"
    with llm.generation_session():
        assert gemini_client.generate("Next job") == "Gemini recovered"
    assert calls["gemini"] == ["gemini-2.5-flash", "gemini-2.5-flash"]
    assert len(calls["fallback"]) == 1


def test_standalone_calls_do_not_share_failure_memory(routing):
    client, calls = routing
    def timeout(**options):
        calls["gemini"].append(options["model"])
        raise TimeoutError()
    client.models.generate_content = timeout
    assert gemini_client.generate("One standalone request") == "Free fallback answer"
    assert gemini_client.generate("Another standalone request") == "Free fallback answer"
    assert len(calls["gemini"]) == len(calls["fallback"]) == 2


def test_failed_model_is_skipped_but_a_working_model_on_same_provider_is_retained(routing):
    client, calls = routing
    chain = gemini_client.build_chain(config.GEMINI_MODEL)
    def answer(**options):
        calls["gemini"].append(options["model"])
        if options["model"] == chain[0]:
            raise http_error(429)
        return SimpleNamespace(text="Working free Gemini model")
    client.models.generate_content = answer
    with llm.generation_session():
        assert gemini_client.generate("First stage") == "Working free Gemini model"
        assert gemini_client.generate("Next stage") == "Working free Gemini model"
    assert calls["gemini"] == [chain[0], chain[1], chain[1]] and not calls["fallback"]


def test_timeout_in_fallback_is_not_repeated_at_the_next_stage(routing, monkeypatch):
    _, calls = routing
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "openrouter-fixture-secret-key")
    monkeypatch.setattr(openrouter, "_select_model", lambda *a, **k: "openrouter/free")
    def hosted(url, payload, **kwargs):
        calls["fallback"].append(url)
        if "api.groq.com" in url:
            raise requests.ReadTimeout("")
        return {"choices": [{"message": {"content": "Free OpenRouter answer"}}]}
    monkeypatch.setattr(llm, "post_json", hosted)
    with llm.generation_session():
        assert llm.chat("First stage") == "Free OpenRouter answer"
        assert llm.chat("Next stage") == "Free OpenRouter answer"
    assert sum("api.groq.com" in url for url in calls["fallback"]) == 1
    assert sum("openrouter.ai" in url for url in calls["fallback"]) == 2


def test_aggregate_cloud_retry_budget_includes_all_models_and_fallbacks(routing, monkeypatch):
    client, _ = routing
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(llm_session, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "openrouter-fixture-secret-key")
    monkeypatch.setattr(openrouter, "_select_model", lambda *a, **k: "openrouter/free")
    def advance(seconds):
        clock.now += seconds
    monkeypatch.setattr(llm.time, "sleep", advance)
    attempts = []
    def fail_gemini(**options):
        timeout = options["config"].http_options.timeout / 1000
        attempts.append(("gemini", timeout))
        advance(timeout)
        raise http_error(503)
    def fail_hosted(url, payload, **kwargs):
        name = "groq" if "api.groq.com" in url else "openrouter"
        attempts.append((name, kwargs["timeout"]))
        advance(kwargs["timeout"])
        raise http_error(503) if name == "groq" else requests.ReadTimeout("")
    client.models.generate_content = fail_gemini
    monkeypatch.setattr(llm, "post_json", fail_hosted)
    with llm.generation_session(), pytest.raises(RuntimeError):
        gemini_client.generate("An unavailable hosted request")
    assert clock.now <= llm_session.CLOUD_REQUEST_SECONDS
    assert [name for name, timeout in attempts] == ["gemini", "gemini", "groq", "groq", "openrouter"]
    assert all(0 < timeout <= llm_session.CLOUD_ATTEMPT_SECONDS for _, timeout in attempts)


def test_inherited_job_stop_is_checked_when_caller_omits_stop_event(routing):
    _, calls = routing
    stopped = threading.Event()
    stopped.set()
    with llm.generation_session(stop_event=stopped), pytest.raises(InterruptedError):
        gemini_client.generate("Research/script callers omit stop_event")
    assert not calls["gemini"] and not calls["fallback"]


def test_stop_during_failure_does_not_fall_back_or_poison_provider_health(routing):
    client, calls = routing
    stopped = threading.Event()
    def response(**options):
        calls["gemini"].append(options["model"])
        if len(calls["gemini"]) == 1:
            stopped.set()
            raise httpx.ReadTimeout("")
        return SimpleNamespace(text="Valid response")
    client.models.generate_content = response
    with llm.generation_session(stop_event=stopped) as session:
        with pytest.raises(InterruptedError):
            gemini_client.generate("Cancelled stage")
        assert not session.failures and not calls["fallback"]
        stopped.clear()
        assert gemini_client.generate("Retry after an explicit cancellation reset") == "Valid response"


def test_simultaneous_channel_jobs_have_independent_failure_memory(routing):
    client, calls = routing
    gate, lock = threading.Barrier(2), threading.Lock()
    counts = {"slow channel": 0, "healthy channel": 0}
    def response(**options):
        owner = options["contents"]
        with lock:
            counts[owner] += 1
            first = counts[owner] == 1
        if first:
            gate.wait(timeout=5)
        if owner == "slow channel":
            raise httpx.ReadTimeout("")
        return SimpleNamespace(text="Healthy Gemini answer")
    client.models.generate_content = response
    def job(owner):
        with llm.generation_session() as session:
            responses = [gemini_client.generate(owner) for _ in range(2)]
            return responses, session.last_success
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = dict(zip(counts, pool.map(job, counts)))
    assert counts == {"slow channel": 1, "healthy channel": 2}
    assert jobs["slow channel"][0] == ["Free fallback answer"] * 2
    assert jobs["healthy channel"][0] == ["Healthy Gemini answer"] * 2
    assert jobs["slow channel"][1]["provider"] == "groq" and jobs["healthy channel"][1]["provider"] == "gemini"


def test_local_only_retains_long_local_inference_and_inherits_cancellation(routing, monkeypatch):
    _, calls = routing
    monkeypatch.setattr(config, "LLM_ROUTE", "local")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "local:model")
    stopped = threading.Event()
    observed = []
    def local(prompt, **kwargs):
        observed.append(kwargs)
        return "Local answer"
    monkeypatch.setattr(local_llm, "generate", local)
    with llm.generation_session(stop_event=stopped), llm_session.request_scope() as budget:
        assert gemini_client.generate("Local-only prompt") == "Local answer"
        assert budget.deadline is None, "Local inference must not consume a cloud request deadline"
    assert observed[0]["stop_event"] is stopped and "timeout" not in observed[0]
    assert not calls["gemini"] and not calls["fallback"]


def test_auto_local_failure_is_remembered_without_weakening_local_only(routing, monkeypatch):
    client, calls = routing
    monkeypatch.setattr(config, "OLLAMA_MODEL", "local:model")
    local_calls = []
    def failed_local(*args, **kwargs):
        local_calls.append(1)
        raise local_llm.LocalLLMError("unavailable", "The selected local model is unavailable")
    monkeypatch.setattr(local_llm, "generate", failed_local)
    client.models.generate_content = lambda **kwargs: SimpleNamespace(text="Gemini answer")
    with llm.generation_session():
        assert gemini_client.generate("First auto stage") == "Gemini answer"
        assert gemini_client.generate("Second auto stage") == "Gemini answer"
        monkeypatch.setattr(config, "LLM_ROUTE", "local")
        with pytest.raises(local_llm.LocalLLMError):
            gemini_client.generate("Explicit local-only request")
    assert len(local_calls) == 2 and not calls["fallback"]


def test_failure_logs_explain_status_without_exposing_private_error_body(routing, capsys):
    client, _ = routing
    secret = config.GEMINI_API_KEY
    def denied(**kwargs):
        raise http_error(403, "Private prompt contents " + secret)
    client.models.generate_content = denied
    with llm.generation_session():
        assert gemini_client.generate("Private prompt contents") == "Free fallback answer"
    logs = capsys.readouterr().out
    assert "HTTP 403" in logs and "Trying Gemini" in logs and "Groq openai/gpt-oss-120b answered" in logs
    assert secret not in logs and "Private prompt contents" not in logs


@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout(""), "timeout"), (requests.ConnectTimeout(""), "timeout"),
    (TimeoutError(), "timeout"), (http_error(429), "quota"), (http_error(503), "transient"), (http_error(403), "access")])
def test_failure_classification_uses_types_and_http_status(error, code):
    assert llm_session.classify_failure(error).code == code


@pytest.mark.parametrize("missing", ["HttpRetryOptions", "HttpOptions.retry_options", "GenerateContentConfig.http_options"])
def test_old_sdk_has_one_actionable_diagnostic_per_job_and_free_fallback_still_works(routing, monkeypatch, capsys, missing):
    from google.genai import types
    _, calls = routing
    if missing == "HttpRetryOptions":
        monkeypatch.delattr(types, "HttpRetryOptions")
    else:
        type_name, field = missing.split(".")
        model = getattr(types, type_name)
        monkeypatch.setattr(model, "model_fields", {key: value for key, value in model.model_fields.items() if key != field})
    checks = []
    actual_check = gemini_client._sdk_types
    def check():
        checks.append(1)
        return actual_check()
    monkeypatch.setattr(gemini_client, "_sdk_types", check)
    with llm.generation_session() as session:
        for stage in ("Script", "Sound"):
            responder = {}
            assert gemini_client.generate(stage, provider_info=responder) == "Free fallback answer"
            assert responder["provider"] == "groq"
        assert session.failure("gemini", config.GEMINI_API_KEY).code == "sdk_upgrade"
    assert checks == [1] and not calls["gemini"] and len(calls["fallback"]) == 2
    logs = capsys.readouterr().out
    assert logs.count("python -m pip install -r requirements.txt") == 1
    assert "google-genai 2.33.0 or newer" in logs and "Skipping Gemini for this generation" in logs
    assert "Check the key" not in logs and "quota" not in logs


def test_old_sdk_without_fallback_reports_upgrade_instead_of_key_or_quota(routing, monkeypatch, capsys):
    from google.genai import types
    monkeypatch.delattr(types, "HttpRetryOptions")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    with llm.generation_session():
        for _ in range(2):
            with pytest.raises(RuntimeError, match="python -m pip install -r requirements.txt") as failure:
                gemini_client.generate("No configured alternative")
            assert "Check the key" not in str(failure.value) and "quota" not in str(failure.value)
    assert capsys.readouterr().out.count("python -m pip install -r requirements.txt") == 1


def test_sdk_compatibility_diagnostic_never_prints_exception_body(routing, monkeypatch, capsys):
    private = "Private SDK body " + config.GEMINI_API_KEY
    def old_sdk():
        raise gemini_client.GeminiSDKCompatibilityError(private)
    monkeypatch.setattr(gemini_client, "_sdk_types", old_sdk)
    with llm.generation_session():
        assert gemini_client.generate("Private prompt") == "Free fallback answer"
    logs = capsys.readouterr().out
    assert "python -m pip install -r requirements.txt" in logs
    assert private not in logs and config.GEMINI_API_KEY not in logs and "Private prompt" not in logs


def test_old_cloud_sdk_does_not_block_explicit_local_only(routing, monkeypatch):
    from google.genai import types
    _, calls = routing
    monkeypatch.delattr(types, "HttpRetryOptions")
    monkeypatch.setattr(config, "LLM_ROUTE", "local")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "local:model")
    monkeypatch.setattr(local_llm, "generate", lambda *args, **kwargs: "Local result")
    with llm.generation_session():
        assert gemini_client.generate("Local-only prompt") == "Local result"
    assert not calls["gemini"] and not calls["fallback"]
