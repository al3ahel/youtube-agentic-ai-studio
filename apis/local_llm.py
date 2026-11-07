"""Bounded inference through an already-installed, genuinely local Ollama model.

Only loopback endpoints are accepted. Discovery and /api/show must both prove a
local model before a prompt is sent; remote aliases and cloud models are refused.
No model download, account, provider key, HTTP proxy, or cloud route is used.
"""
from __future__ import annotations

import http.client
import json
import math
import re
import socket
import threading
import time
from urllib.parse import urlsplit

import config

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PROMPT_CHARS = 60000
MAX_GENERATION_SECONDS = 180
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,179}\Z")
_DIGEST = re.compile(r"(?:sha256:)?[a-f0-9]{64}\Z")


class LocalLLMError(RuntimeError):
    """A safe user-facing failure without server bodies or prompt text."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def validate_base_url(value=None):
    """Return a canonical loopback origin; localhost never invokes external DNS."""
    if value is None:
        value = getattr(config, "OLLAMA_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
    if not isinstance(value, str) or not value or len(value) > 200 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise LocalLLMError("invalid_endpoint", "Use a local Ollama address such as http://127.0.0.1:11434.")
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or parts.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parts.username is not None or parts.password is not None
                or parts.path not in {"", "/"} or parts.query or parts.fragment):
            raise ValueError()
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise LocalLLMError("invalid_endpoint", "Ollama must run on this computer's loopback address, without a URL path or credentials.") from None
    host = "[::1]" if parts.hostname == "::1" else "127.0.0.1"
    return f"{parts.scheme}://{host}:{port}"


def _cancelled(stop_event):
    return stop_event is not None and stop_event.is_set()


def _check(stop_event, deadline):
    if _cancelled(stop_event):
        raise LocalLLMError("cancelled", "Local model generation was stopped.")
    if time.monotonic() >= deadline:
        raise LocalLLMError("timeout", "The local model took too long. Choose a smaller installed model or try again.")


def _request_json(base_url, endpoint, *, body=None, stop_event=None, timeout=10):
    """Close the live socket on stop/deadline, including while waiting for headers."""
    origin = validate_base_url(base_url)
    parts = urlsplit(origin)
    deadline = time.monotonic() + timeout
    _check(stop_event, deadline)
    connection_type = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parts.hostname, parts.port, timeout=min(2, timeout))
    finished = threading.Event()
    live_socket = [None]
    aborted = [None]
    response = None

    def monitor():
        while not finished.wait(.05):
            reason = "cancelled" if _cancelled(stop_event) else "timeout" if time.monotonic() >= deadline else None
            if reason:
                aborted[0] = reason
                sock = live_socket[0]
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                return

    watcher = threading.Thread(target=monitor, daemon=True, name="ollama-request-deadline")
    watcher.start()
    try:
        connection.connect()
        live_socket[0] = connection.sock
        _check(stop_event, deadline)
        connection.sock.settimeout(max(.05, deadline - time.monotonic()))
        encoded = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        connection.request("GET" if body is None else "POST", endpoint, body=encoded,
                           headers={"Content-Type": "application/json", "Accept": "application/json",
                                    "User-Agent": "YAAS-local-model/1.0"})
        response = connection.getresponse()
        _check(stop_event, deadline)
        if response.status != 200:
            code = "model_unavailable" if response.status == 404 else "request_failed"
            raise LocalLLMError(code, "Ollama could not serve the selected local model. Check that it is installed and running.")
        length = response.getheader("Content-Length")
        if length is not None and not 0 <= int(length) <= MAX_RESPONSE_BYTES:
            raise LocalLLMError("response_too_large", "The local model response exceeded the studio's size limit.")
        payload = bytearray()
        while True:
            _check(stop_event, deadline)
            chunk = response.read(min(65536, MAX_RESPONSE_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise LocalLLMError("response_too_large", "The local model response exceeded the studio's size limit.")
        _check(stop_event, deadline)
        parsed = json.loads(payload)
        if not isinstance(parsed, dict) or parsed.get("error"):
            raise LocalLLMError("invalid_response", "Ollama returned no usable local model response.")
        return parsed
    except LocalLLMError:
        raise
    except (OSError, http.client.HTTPException, ValueError, UnicodeError, TypeError):
        if aborted[0] or _cancelled(stop_event) or time.monotonic() >= deadline:
            _check(stop_event, deadline)
        raise LocalLLMError("unavailable", "Local Ollama is unavailable. Start Ollama and choose an installed model; the studio will not download one automatically.") from None
    finally:
        finished.set()
        if response is not None:
            response.close()
        connection.close()


def _local_name(value):
    return (isinstance(value, str) and _MODEL.fullmatch(value) and "cloud" not in value.lower()
            and "://" not in value and ".." not in value.split("/"))


def _local_record(row):
    if not isinstance(row, dict) or row.get("remote_host") or row.get("remote_model"):
        return None
    name = row.get("model") or row.get("name")
    if (not _local_name(name) or type(row.get("size")) is not int or row["size"] <= 0
            or not isinstance(row.get("digest"), str) or not _DIGEST.fullmatch(row["digest"])):
        return None
    details = row.get("details")
    if not isinstance(details, dict) or not details.get("format"):
        return None
    capabilities = row.get("capabilities")
    if isinstance(capabilities, list) and capabilities and "completion" not in capabilities:
        return None
    return {"id": name, "name": name, "model": name, "size": row["size"], "digest": row["digest"],
            "family": str(details.get("family", ""))[:100],
            "parameter_size": str(details.get("parameter_size", ""))[:40],
            "quantization_level": str(details.get("quantization_level", ""))[:40], "local": True}


def list_models(base_url=None, stop_event=None):
    """List installed local text models; cloud-only entries never reach the UI."""
    data = _request_json(base_url, "/api/tags", stop_event=stop_event)
    if not isinstance(data.get("models"), list):
        raise LocalLLMError("invalid_response", "Ollama returned an invalid installed-model list.")
    records = [record for row in data["models"][:1000] if (record := _local_record(row))]
    unique = {record["id"]: record for record in records}
    return sorted(unique.values(), key=lambda row: (row["size"], row["id"]))


def generate(prompt, model=None, base_url=None, temperature=.7, stop_event=None):
    """Generate text locally after verifying the selected installed model twice."""
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        raise LocalLLMError("invalid_prompt", "Provide a nonempty prompt within the local model's input limit.")
    if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature) or not 0 <= temperature <= 2):
        raise LocalLLMError("invalid_options", "Choose a model temperature from zero to two.")
    origin = validate_base_url(base_url)
    selected = model if model is not None else getattr(config, "OLLAMA_MODEL", "")
    if not isinstance(selected, str) or selected and not _local_name(selected):
        raise LocalLLMError("cloud_model_rejected", "Choose an installed local Ollama model; cloud models are disabled.")
    records = list_models(origin, stop_event=stop_event)
    if not records:
        raise LocalLLMError("no_local_models", "No installed local text model is available. Install a model in Ollama first; the studio will not pull one automatically.")
    if not selected:
        selected = records[0]["id"]
    # Ollama accepts an omitted :latest tag, but prompts still use the exact
    # installed tag returned by the local catalogue.
    record = next((row for row in records if row["id"] == selected or row["id"] == selected + ":latest"), None)
    if record is None:
        raise LocalLLMError("model_not_installed", "The selected model is not installed locally. Choose a model from Ollama's installed list.")
    selected = record["id"]
    details = _request_json(origin, "/api/show", body={"model": selected}, stop_event=stop_event)
    if details.get("remote_host") or details.get("remote_model"):
        raise LocalLLMError("cloud_model_rejected", "That Ollama alias points to a remote model. Choose an installed local model.")
    info, capabilities = details.get("model_info"), details.get("capabilities")
    if not isinstance(info, dict) or not info:
        raise LocalLLMError("local_model_unverified", "Ollama did not confirm local model weights. Choose another installed model.")
    if isinstance(capabilities, list) and capabilities and "completion" not in capabilities:
        raise LocalLLMError("unsupported_model", "Choose a local text-generation model rather than an embedding or audio-only model.")
    from apis.openrouter import _redact
    request_body = {"model": selected, "messages": [{"role": "user", "content": _redact(prompt)}],
                    "stream": False, "options": {"temperature": temperature, "num_predict": 8192}, "keep_alive": "5m"}
    result = _request_json(origin, "/api/chat", body=request_body, stop_event=stop_event,
                           timeout=MAX_GENERATION_SECONDS)
    if result.get("remote_host") or result.get("remote_model"):
        raise LocalLLMError("cloud_model_rejected", "Ollama reported a remote response. Disable cloud features in Ollama before trying again.")
    message = result.get("message")
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str) or not text.strip() or result.get("done") is not True:
        raise LocalLLMError("invalid_response", "The local model did not finish a usable text response.")
    if result.get("done_reason") == "length":
        raise LocalLLMError("incomplete_response", "The local model reached its output limit. Use a shorter request or another installed model.")
    return _redact(text).strip()
