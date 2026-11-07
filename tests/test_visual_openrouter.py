"""Offline provider contract tests; no paid or real inference requests."""
import copy
import json

import pytest

from apis import openrouter as provider


KEY = "test-openrouter-secret-credential-0123456789"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title"],
          "properties": {"title": {"type": "string", "minLength": 1, "maxLength": 80}}}


class Response:
    def __init__(self, payload=None, status=200):
        self.payload = payload
        self.status_code = status
        self.content = json.dumps(payload).encode()
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


def answer(content=None, **kwargs):
    return {"choices": [{"message": {"content": content or '{"title":"Motion explains the process"}'},
                         "finish_reason": "stop"}], **kwargs}


def model(identifier="example/planner:free", **changes):
    record = {"id": identifier, "name": identifier, "context_length": 65536,
              "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
              "pricing": {"prompt": "0", "completion": "0", "request": "0"},
              "supported_parameters": ["response_format", "structured_outputs"]}
    record.update(changes)
    return record


@pytest.fixture(autouse=True)
def isolated_provider(monkeypatch):
    monkeypatch.setattr(provider.config, "OPENROUTER_API_KEY", "", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(provider.config, "VISUAL_PLANNER_MODEL", "openrouter/free", raising=False)
    monkeypatch.setattr(provider.config, "VISUAL_REVIEW_MODEL", "openrouter/free", raising=False)
    monkeypatch.setattr(provider.config, "OPENROUTER_DAILY_REQUEST_BUDGET", 45, raising=False)
    monkeypatch.setattr(provider, "_catalogue", (0.0, []))
    monkeypatch.setattr(provider, "_usage", {})
    monkeypatch.setattr(provider.time, "sleep", lambda _: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected live network request")

    monkeypatch.setattr(provider.requests, "get", forbidden)
    monkeypatch.setattr(provider.requests, "post", forbidden)


def enable(monkeypatch):
    monkeypatch.setattr(provider.config, "OPENROUTER_API_KEY", KEY, raising=False)


def test_catalogue_is_public_curated_and_cache_is_not_mutable(monkeypatch):
    vision = model("example/vision:free", architecture={"input_modalities": ["text", "image"], "output_modalities": ["text"]})
    records = [model(), vision, model("paid/model"),
               model("example/not-free:free", pricing={"prompt": "0", "completion": "0.01"}),
               model("example/media:free", architecture={"input_modalities": ["text"], "output_modalities": ["video"]}),
               model("example/sku:free", pricing_skus=[{"price": "0.4"}]),
               model("example/malformed:free", architecture={"input_modalities": [{}], "output_modalities": ["text"]}),
               model("example/invalid:free", architecture="text"),
               model("example/nan:free", pricing={"prompt": "NaN", "completion": "0"})]
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response({"data": records})

    monkeypatch.setattr(provider.requests, "get", get)
    result = provider.discover_models(api_key=KEY)
    assert {row["id"] for row in result} == {"example/planner:free", "example/vision:free"}
    assert next(row for row in result if row["supports_vision"])["supports_schema"]
    assert "Authorization" not in calls[0][1]["headers"]
    result[0]["pricing"]["prompt"] = "99"
    assert all(row["pricing"]["prompt"] == "0" for row in provider.discover_models())
    assert len(calls) == 1
    provider.discover_models(force=True)
    assert len(calls) == 2


@pytest.mark.parametrize("record", [model("example/negative:free", pricing={"prompt": "-1", "completion": "0"}),
                                  model("example/unknown:free", pricing={"prompt": "0"})])
def test_unknown_or_negative_pricing_is_not_free(record):
    assert provider._curate(record) is None


def test_catalogue_error_has_no_provider_body(monkeypatch):
    monkeypatch.setattr(provider.requests, "get", lambda *a, **k: Response({"error": KEY}, 502))
    with pytest.raises(provider.OpenRouterError) as error:
        provider.discover_models()
    assert error.value.code == "catalogue_unavailable"
    assert KEY not in str(error.value)


@pytest.mark.parametrize("key", ["", "YOUR_OPENROUTER_API_KEY"])
def test_missing_key_never_uses_network_or_reads_review_frames(monkeypatch, key):
    monkeypatch.setattr(provider.config, "OPENROUTER_API_KEY", key)
    with pytest.raises(provider.OpenRouterError) as error:
        provider.plan_json("Plan the animation", SCHEMA)
    assert error.value.code == "missing_key"
    assert provider.review_frames(["/nonexistent/image.png"], "Review") == {
        "status": "unavailable", "reason": "missing_key", "passed": None, "issues": []}


@pytest.mark.parametrize("selected", ["openai/paid-model", "example/model:free:paid", "https://invalid.test"])
def test_paid_and_invalid_model_ids_are_rejected_before_network(monkeypatch, selected):
    enable(monkeypatch)
    with pytest.raises(provider.OpenRouterError) as error:
        provider.plan_json("Plan the animation", SCHEMA, selected)
    assert error.value.code in {"paid_model_rejected", "invalid_model"}


@pytest.mark.parametrize("parameters", [[], ["response_format"]])
def test_explicit_free_model_must_have_current_strict_schema_support(monkeypatch, parameters):
    enable(monkeypatch)
    monkeypatch.setattr(provider.requests, "get", lambda *a, **k: Response({"data": [model(supported_parameters=parameters)]}))
    with pytest.raises(provider.OpenRouterError) as error:
        provider.plan_json("Plan the animation", SCHEMA, "example/planner:free")
    assert error.value.code == "unsupported_model"


def test_valid_free_router_json_redacts_secret_from_prompt(monkeypatch):
    enable(monkeypatch)
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response(answer())

    monkeypatch.setattr(provider.requests, "post", post)
    assert provider.plan_json("Plan using source " + KEY, SCHEMA)["title"]
    payload = calls[0][1]["json"]
    assert payload["model"] == "openrouter/free"
    assert payload["provider"]["require_parameters"] is True
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert KEY not in json.dumps(payload)
    assert calls[0][1]["headers"]["Authorization"] == "Bearer " + KEY
    assert calls[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("body", ['not json', '{"title":"Okay","script":"execute code"}', '{"title":17}'])
def test_malformed_or_extra_fields_have_two_attempt_limit(monkeypatch, body):
    enable(monkeypatch)
    calls = []
    monkeypatch.setattr(provider.requests, "post", lambda *a, **k: calls.append(k["json"]["model"]) or Response(answer(body)))
    with pytest.raises(provider.OpenRouterError) as error:
        provider.plan_json("Plan the animation", SCHEMA)
    assert error.value.code == "invalid_response"
    assert calls == ["openrouter/free", "openrouter/free"]


@pytest.mark.parametrize("status,code", [(429, "rate_limited"), (402, "access_denied"), (403, "access_denied")])
def test_quota_or_account_failures_do_not_retry_or_fallback(monkeypatch, status, code):
    enable(monkeypatch)
    calls = []
    monkeypatch.setattr(provider.requests, "post", lambda *a, **k: calls.append(k) or Response({"error": KEY}, status))
    with pytest.raises(provider.OpenRouterError) as error:
        provider.plan_json("Plan the animation", SCHEMA)
    assert error.value.code == code
    assert KEY not in str(error.value)
    assert len(calls) == 1


def test_failed_attempts_obey_parent_and_local_daily_budget(monkeypatch):
    enable(monkeypatch)
    calls = []
    monkeypatch.setattr(provider.requests, "post", lambda *a, **k: calls.append(k) or Response({}, 503))
    with provider.call_budget(1), pytest.raises(provider.OpenRouterError) as error:
        provider.plan_json("Plan the animation", SCHEMA)
    assert error.value.code == "call_budget_exhausted"
    monkeypatch.setattr(provider.config, "OPENROUTER_DAILY_REQUEST_BUDGET", 1)
    with pytest.raises(provider.OpenRouterError) as error:
        provider.plan_json("Plan the animation", SCHEMA)
    assert error.value.code == "local_quota_exhausted"
    assert len(calls) == 1


@pytest.mark.parametrize("change", [{"anyOf": []}, {"required": None}, {"description": KEY}])
def test_invalid_schema_or_schema_secret_rejected_before_request(monkeypatch, change):
    enable(monkeypatch)
    with pytest.raises(provider.OpenRouterError) as error:
        provider.plan_json("Plan the animation", {**SCHEMA, **change})
    assert error.value.code == "invalid_schema"


def test_reported_charge_rejected_without_retry(monkeypatch):
    enable(monkeypatch)
    calls = []
    monkeypatch.setattr(provider.requests, "post", lambda *a, **k: calls.append(k) or Response(answer(usage={"cost": 0.01})))
    with pytest.raises(provider.OpenRouterError) as error:
        provider.plan_json("Plan", SCHEMA)
    assert error.value.code == "unexpected_cost"
    assert len(calls) == 1


def test_optional_vision_review_resizes_and_rejects_invalid_frame_indices(monkeypatch, tmp_path):
    from PIL import Image
    enable(monkeypatch)
    image_path = tmp_path / "frame.png"
    Image.new("RGB", (1600, 900), "navy").save(image_path)
    replies = [
        {"passed": True, "summary": "Unreadable caption", "issues": [{"frame": 0, "severity": "error", "message": "Caption cropped"}]},
        {"passed": True, "summary": "Bad index", "issues": [{"frame": 4, "severity": "warning", "message": "Missing frame"}]},
    ]
    sent = []

    def post(*args, **kwargs):
        sent.append(kwargs["json"])
        return Response(answer(json.dumps(replies.pop(0))))

    monkeypatch.setattr(provider.requests, "post", post)
    reviewed = provider.review_frames([image_path], "Check labels and caption safety")
    assert reviewed["status"] == "reviewed" and reviewed["passed"] is False
    assert reviewed["frame_count"] == 1
    assert sent[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    invalid = provider.review_frames([image_path], "Check")
    assert invalid["reason"] == "invalid_response"
