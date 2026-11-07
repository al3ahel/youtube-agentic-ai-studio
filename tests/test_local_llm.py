"""Local inference stays local, uses installed weights, and stops promptly."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from apis import local_llm


def _model(name="tiny:latest", **changes):
    return {"name": name, "model": name, "size": 1000000, "digest": "a" * 64,
            "details": {"format": "gguf", "family": "example", "parameter_size": "1B", "quantization_level": "Q4_K_M"}, **changes}


def _details(**changes):
    return {"model_info": {"general.architecture": "example"}, "capabilities": ["completion"], **changes}


def _chat(**changes):
    return {"done": True, "done_reason": "stop", "message": {"role": "assistant", "content": "  A local answer.  "}, **changes}


def _responses(monkeypatch, tags=None, show=None, answer=None):
    calls = []
    values = {"/api/tags": {"models": [_model()]} if tags is None else tags,
              "/api/show": _details() if show is None else show,
              "/api/chat": _chat() if answer is None else answer}
    def request(base_url, endpoint, **kwargs):
        calls.append((base_url, endpoint, kwargs))
        return deepcopy(values[endpoint])
    monkeypatch.setattr(local_llm, "_request_json", request)
    return calls


@pytest.mark.parametrize("address", [
    "https://ollama.com", "http://192.168.1.5:11434", "http://127.0.0.1.evil.example:11434",
    "http://localhost@evil.example:11434", "http://user:password@localhost:11434", "http://[::ffff:127.0.0.1]:11434",
    "http://localhost:11434/api", "http://localhost:11434?proxy=evil", "http://localhost:11434/#x",
    "file:///tmp/ollama", "http://localhost:0", "http://localhost:65536", "http://localhost\n:11434", None,
])
def test_endpoint_rejects_nonlocal_or_ambiguous_origins(address, monkeypatch):
    if address is None:
        monkeypatch.setattr(local_llm.config, "OLLAMA_BASE_URL", "https://ollama.com", raising=False)
    with pytest.raises(local_llm.LocalLLMError) as error:
        local_llm.validate_base_url(address)
    assert error.value.code == "invalid_endpoint"


@pytest.mark.parametrize("address,expected", [
    ("http://localhost:11434/", "http://127.0.0.1:11434"),
    ("http://127.0.0.1:12345", "http://127.0.0.1:12345"),
    ("http://[::1]:11434", "http://[::1]:11434"),
    ("https://localhost", "https://127.0.0.1:443"),
])
def test_loopback_origin_is_canonical_and_localhost_does_not_need_dns(address, expected):
    assert local_llm.validate_base_url(address) == expected


def test_model_discovery_filters_cloud_aliases_missing_weights_and_nontext_models(monkeypatch):
    calls = _responses(monkeypatch, tags={"models": [
        _model(), _model("big:latest", size=9000000), _model("remote:cloud"),
        _model("innocent:latest", remote_host="https://ollama.com", remote_model="large"),
        _model("no-weights", size=0), _model("no-digest", digest=""),
        _model("embedding", capabilities=["embedding"]), _model("invalid", details={}),
    ]})
    found = local_llm.list_models()
    assert [row["id"] for row in found] == ["tiny:latest", "big:latest"]
    assert all(row["local"] is True for row in found)
    assert [call[1] for call in calls] == ["/api/tags"]


def test_generation_verifies_the_exact_local_tag_before_sending_a_redacted_prompt(monkeypatch):
    calls = _responses(monkeypatch)
    monkeypatch.setattr(local_llm.config, "GROQ_API_KEY", "a-sensitive-test-token")
    text = local_llm.generate("Write a line. a-sensitive-test-token", model="tiny", base_url="http://localhost:11434")
    assert text == "A local answer."
    assert [call[1] for call in calls] == ["/api/tags", "/api/show", "/api/chat"]
    assert all(call[0] == "http://127.0.0.1:11434" for call in calls)
    body = calls[-1][2]["body"]
    assert body["model"] == "tiny:latest" and body["stream"] is False
    assert body["options"]["num_predict"] == 8192
    assert body["messages"] == [{"role": "user", "content": "Write a line. [credential removed]"}]
    assert all("pull" not in call[1] for call in calls)


def test_automatic_local_choice_uses_a_small_installed_model_without_downloading(monkeypatch):
    calls = _responses(monkeypatch, tags={"models": [_model("big", size=2000000), _model("small", size=1000000)]})
    monkeypatch.setattr(local_llm.config, "OLLAMA_MODEL", "", raising=False)
    assert local_llm.generate("A short prompt") == "A local answer."
    assert calls[-1][2]["body"]["model"] == "small"


@pytest.mark.parametrize("name", ["gpt-oss:120b-cloud", "large:cloud", "foo/CLOUD", "https://ollama.com/model", 0, []])
def test_explicit_cloud_or_invalid_name_is_rejected_before_discovery(monkeypatch, name):
    calls = _responses(monkeypatch)
    with pytest.raises(local_llm.LocalLLMError):
        local_llm.generate("Prompt", model=name)
    assert calls == []


@pytest.mark.parametrize("tags,code", [({"models": []}, "no_local_models"), ({"models": [_model("different")]}, "model_not_installed")])
def test_missing_local_model_never_triggers_pull_or_inference(monkeypatch, tags, code):
    calls = _responses(monkeypatch, tags=tags)
    with pytest.raises(local_llm.LocalLLMError) as error:
        local_llm.generate("Prompt", model="tiny")
    assert error.value.code == code
    assert [call[1] for call in calls] == ["/api/tags"]


@pytest.mark.parametrize("details,code", [
    (_details(remote_host="https://ollama.com"), "cloud_model_rejected"),
    (_details(remote_model="large"), "cloud_model_rejected"),
    (_details(model_info={}), "local_model_unverified"),
    (_details(capabilities=["embedding"]), "unsupported_model"),
])
def test_show_recheck_rejects_remote_alias_or_unverified_weights_before_prompt(monkeypatch, details, code):
    calls = _responses(monkeypatch, show=details)
    with pytest.raises(local_llm.LocalLLMError) as error:
        local_llm.generate("Do not send this prompt to a remote service", model="tiny")
    assert error.value.code == code
    assert [call[1] for call in calls] == ["/api/tags", "/api/show"]


@pytest.mark.parametrize("answer,code", [
    (_chat(remote_host="https://ollama.com"), "cloud_model_rejected"),
    (_chat(done=False), "invalid_response"), (_chat(message={"content": ""}), "invalid_response"),
    (_chat(done_reason="length"), "incomplete_response"),
])
def test_generation_reports_incomplete_or_remote_output(monkeypatch, answer, code):
    _responses(monkeypatch, answer=answer)
    with pytest.raises(local_llm.LocalLLMError) as error:
        local_llm.generate("Prompt", model="tiny")
    assert error.value.code == code


@pytest.fixture
def loopback_server():
    release = threading.Event()
    received = threading.Event()
    responses = {"status": 200, "body": b'{"ok":true}', "wait": False, "headers": {}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            received.set()
            if responses["wait"]:
                release.wait(timeout=5)
            try:
                self.send_response(responses["status"])
                for key, value in responses["headers"].items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(responses["body"])))
                self.end_headers()
                self.wfile.write(responses["body"])
            except OSError:
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", responses, received, release
    release.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=1)


def test_transport_reads_local_json_without_using_environment_proxy(loopback_server, monkeypatch):
    base, _, _, _ = loopback_server
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    assert local_llm._request_json(base, "/api/tags") == {"ok": True}


def test_transport_rejects_redirects_without_following_remote_location(loopback_server):
    base, responses, _, _ = loopback_server
    responses.update(status=302, headers={"Location": "https://ollama.com/api/tags"})
    with pytest.raises(local_llm.LocalLLMError) as error:
        local_llm._request_json(base, "/api/tags")
    assert error.value.code == "request_failed"


def test_transport_bounds_response_before_reading_it(loopback_server, monkeypatch):
    base, responses, _, _ = loopback_server
    monkeypatch.setattr(local_llm, "MAX_RESPONSE_BYTES", 16)
    responses["body"] = b"x" * 50
    with pytest.raises(local_llm.LocalLLMError) as error:
        local_llm._request_json(base, "/api/tags")
    assert error.value.code == "response_too_large"


@pytest.mark.parametrize("cancel", [True, False])
def test_transport_interrupts_waiting_for_headers_on_stop_or_deadline(loopback_server, cancel):
    base, responses, received, release = loopback_server
    responses["wait"] = True
    stop = threading.Event()
    if cancel:
        def stop_when_connected():
            received.wait(timeout=1)
            stop.set()
        threading.Thread(target=stop_when_connected, daemon=True).start()
    started = time.monotonic()
    with pytest.raises(local_llm.LocalLLMError) as error:
        local_llm._request_json(base, "/api/tags", stop_event=stop, timeout=1 if cancel else .15)
    elapsed = time.monotonic() - started
    release.set()
    assert error.value.code == ("cancelled" if cancel else "timeout")
    assert elapsed < .8


def test_already_cancelled_request_never_opens_a_connection(monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(local_llm.http.client, "HTTPConnection", lambda *args, **kwargs: pytest.fail("Stopped request opened a socket"))
    with pytest.raises(local_llm.LocalLLMError) as error:
        local_llm._request_json("http://127.0.0.1:11434", "/api/tags", stop_event=stop)
    assert error.value.code == "cancelled"
