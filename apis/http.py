"""
Shared HTTP helpers — one session with a polite User-Agent, JSON helpers
and a tiny thread-safe TTL cache so the dashboard doesn't hammer free APIs.
"""
import functools
import os
import re
import threading
import time

import requests

USER_AGENT = (
    "YTAgenticStudio/2.0 "
    "(+https://github.com/raunakpatil/youtube-agentic-ai-studio)"
)

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT

# Pixabay and the YouTube Data API take their key as a query parameter, and
# requests puts the full URL into its error text. Those messages are printed
# to the run log and returned to the browser, so strip the credential first.
_SECRET_PARAM = re.compile(
    r"(?i)\b((?:api[_-]?key|key|access_token|token|client_secret)=)[^&\s'\"<>)]+")


def redact(text) -> str:
    """Error text with any key=… / token=… query value removed."""
    return _SECRET_PARAM.sub(r"\1[redacted]", str(text))


def _send(send, url, **kwargs) -> requests.Response:
    try:
        r = send(url, **kwargs)
        r.raise_for_status()
        return r
    except requests.RequestException as exc:
        message = redact(exc)
        if message == str(exc):
            raise
        try:
            safe = type(exc)(message, request=exc.request, response=exc.response)
        except TypeError:
            safe = requests.RequestException(message, request=exc.request, response=exc.response)
        # `from None`: the chained original still carries the unredacted URL.
        raise safe from None


def get(url: str, params: dict | None = None, timeout: float = 12,
        headers: dict | None = None, stream: bool = False) -> requests.Response:
    return _send(_session.get, url, params=params, timeout=timeout,
                 headers=headers, stream=stream)


def get_json(url: str, params: dict | None = None, timeout: float = 12,
             headers: dict | None = None):
    return get(url, params=params, timeout=timeout, headers=headers).json()


def post_json(url: str, payload: dict, timeout: float = 60,
              headers: dict | None = None):
    return _send(_session.post, url, json=payload, timeout=timeout, headers=headers).json()


def download(url: str, path: str, timeout: float = 30,
             max_bytes: int = 64 * 1024 * 1024) -> str:
    """Stream a URL to disk. Returns the path; a failed download leaves no file."""
    r = get(url, timeout=timeout, stream=True)
    try:
        size = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("The download exceeded the size limit.")
                f.write(chunk)
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    finally:
        r.close()
    return path


def ttl_cache(seconds: float):
    """Memoise a function's result per-arguments for `seconds`."""
    def deco(fn):
        store: dict = {}
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.time()
            with lock:
                hit = store.get(key)
                if hit and now - hit[0] < seconds:
                    return hit[1]
            val = fn(*args, **kwargs)
            with lock:
                # Keys are free text (topics, search queries); without this the
                # expired results of every past lookup would be kept forever.
                for old in [k for k, (at, _) in store.items() if now - at >= seconds]:
                    del store[old]
                store[key] = (now, val)
            return val

        def cache_clear():
            with lock:
                store.clear()

        def cache_size():
            with lock:
                return len(store)

        wrapper.cache_clear = cache_clear
        wrapper.cache_size = cache_size
        return wrapper
    return deco
