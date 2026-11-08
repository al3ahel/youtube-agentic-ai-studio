"""Ephemeral routing health and cloud retry budgets for one generation job.

No account preference or global provider blacklist is changed. A fresh job gets
fresh health; ContextVars keep simultaneous channel jobs independent. Local
inference retains its own longer, cancellable timeout and never spends the
cloud fallback budget.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import socket
import threading
import time

import requests

try:
    import httpx
    _HTTP_TIMEOUTS = (httpx.TimeoutException,)
    _HTTP_CONNECTIONS = (httpx.TransportError,)
except ImportError:  # The hosted fallback adapter does not require Gemini's SDK.
    _HTTP_TIMEOUTS = _HTTP_CONNECTIONS = ()
try:
    import httpx2
    _HTTP_TIMEOUTS += (httpx2.TimeoutException,)
    _HTTP_CONNECTIONS += (httpx2.TransportError,)
except ImportError:
    pass

CLOUD_REQUEST_SECONDS = 120
CLOUD_PROVIDER_SECONDS = 45
CLOUD_ATTEMPT_SECONDS = 30
SINGLE_PROVIDER_SECONDS = 60


@dataclass(frozen=True)
class Failure:
    code: str
    message: str
    status: int | None = None


def classify_failure(error):
    """Use typed transport/status information; never expose raw provider bodies."""
    current = error
    for _ in range(4):
        if isinstance(current, (TimeoutError, requests.Timeout, *_HTTP_TIMEOUTS)):
            return Failure("timeout", "request timed out")
        if isinstance(current, (ConnectionError, socket.gaierror, requests.ConnectionError, *_HTTP_CONNECTIONS)):
            return Failure("connection", "connection unavailable")
        response = getattr(current, "response", None)
        status = getattr(response, "status_code", None)
        if type(status) is not int:
            status = getattr(current, "code", None)
        if type(status) is int:
            if status in {401, 402, 403}:
                return Failure("access", f"HTTP {status}: credentials or free access unavailable", status)
            if status == 429:
                return Failure("quota", "HTTP 429: free quota or rate limit unavailable", status)
            if status == 404:
                return Failure("model", "HTTP 404: model unavailable", status)
            if status in {408, 504}:
                return Failure("timeout", f"HTTP {status}: request timed out", status)
            if status in {500, 502, 503}:
                return Failure("transient", f"HTTP {status}: provider temporarily unavailable", status)
            return Failure("request", f"HTTP {status}: request failed", status)
        cause = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        if cause is None or cause is current:
            break
        current = cause
    # Older SDKs wrap platform errors without typed status/transport details.
    # Match diagnostic fragments, but only return our fixed public messages.
    message = str(error).casefold()
    if any(text in message for text in ("timed out", "timeout", "readtimeout", "deadline exceeded")):
        return Failure("timeout", "request timed out")
    if any(text in message for text in ("10053", "10054", "connection", "connecterror", "remotedisconnected", "temporary failure", "nameresolution")):
        return Failure("connection", "connection unavailable")
    if any(text in message for text in ("404", "not_found", "not found", "is not supported")):
        return Failure("model", "model unavailable")
    if any(text in message for text in ("429", "resource_exhausted", "quota", "rate limit")):
        return Failure("quota", "free quota or rate limit unavailable")
    if any(text in message for text in ("401", "402", "403", "api_key_invalid", "invalid api key", "unauthenticated")):
        return Failure("access", "credentials or free access unavailable")
    if any(text in message for text in ("500", "502", "503", "unavailable", "overloaded", "high demand", "internal")):
        return Failure("transient", "provider temporarily unavailable")
    return Failure("request", "request failed")


@dataclass
class GenerationSession:
    stop_event: object = None
    failures: dict = field(default_factory=dict)
    last_success: dict = field(default_factory=dict)
    _lock: object = field(default_factory=threading.RLock, repr=False)

    @staticmethod
    def identity(provider, credential="", model=""):
        digest = hashlib.sha256(str(credential).encode()).hexdigest()
        return provider, digest, model

    def failure(self, provider, credential="", model=""):
        with self._lock:
            return (self.failures.get(self.identity(provider, credential))
                    or self.failures.get(self.identity(provider, credential, model)))

    def failed(self, provider, failure, credential="", model=""):
        with self._lock:
            self.failures[self.identity(provider, credential, model)] = failure

    def succeeded(self, provider, model):
        with self._lock:
            self.last_success = {"provider": provider, "model": model}


_session = ContextVar("llm_generation_session", default=None)
_request = ContextVar("llm_cloud_request", default=None)


@contextmanager
def generation_session(*, stop_event=None):
    """Wrap one runner job; optionally inspect the yielded session's responder."""
    session = GenerationSession(stop_event=stop_event)
    token = _session.set(session)
    request_token = _request.set(None)
    try:
        yield session
    finally:
        _request.reset(request_token)
        _session.reset(token)


@contextmanager
def session_scope(stop_event=None):
    current = _session.get()
    if current is not None:
        yield current
    else:
        with generation_session(stop_event=stop_event) as current:
            yield current


def effective_stop_event(value=None):
    current = _session.get()
    return value if value is not None else current.stop_event if current else None


def check_stop(stop_event=None):
    event = effective_stop_event(stop_event)
    if event is not None and event.is_set():
        raise InterruptedError("Stopped during AI generation")


class BudgetExceeded(RuntimeError):
    def __init__(self, provider=None):
        self.provider = provider
        super().__init__("The cloud generation retry budget was reached." if provider is None
                         else f"The {provider} retry budget was reached.")


@dataclass
class RequestBudget:
    deadline: float | None = None
    providers: dict = field(default_factory=dict)

    def timeout(self, provider, *, alternatives):
        now = time.monotonic()
        if self.deadline is None:
            self.deadline = now + CLOUD_REQUEST_SECONDS
        if now >= self.deadline:
            raise BudgetExceeded()
        limit = CLOUD_PROVIDER_SECONDS if alternatives else SINGLE_PROVIDER_SECONDS
        deadline = self.providers.setdefault(provider, now + limit)
        remaining = min(self.deadline, deadline) - now
        if remaining < .1:
            raise BudgetExceeded(provider)
        return min(CLOUD_ATTEMPT_SECONDS if alternatives else SINGLE_PROVIDER_SECONDS, remaining)


@contextmanager
def request_scope():
    existing = _request.get()
    if existing is not None:
        yield existing
        return
    value = RequestBudget()
    token = _request.set(value)
    try:
        yield value
    finally:
        _request.reset(token)
