"""
Hosted free-tier fallback chain, used when Gemini is unavailable.

  groq          free key   · very fast Llama models
  openrouter    free key   · ":free" community models
The retired unauthenticated Pollinations generation URL is never contacted.
"""
import time
import threading
from decimal import Decimal

import config
from apis.http import post_json
from apis import openrouter

_DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
_GROQ_REPLACEMENTS = {
    "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
    "llama-3.1-8b-instant": "openai/gpt-oss-20b",
}
_migration_lock = threading.Lock()
_announced_migrations = set()


def effective_groq_model(value=None):
    """Retired free-tier defaults migrate at runtime; other choices stay intact."""
    selected = getattr(config, "GROQ_MODEL", _DEFAULT_GROQ_MODEL) if value is None else value
    selected = selected.strip() if isinstance(selected, str) else ""
    return _GROQ_REPLACEMENTS.get(selected, selected or _DEFAULT_GROQ_MODEL)


def _announce_groq_migration(model):
    saved = getattr(config, "GROQ_MODEL", "")
    saved = saved.strip() if isinstance(saved, str) else ""
    if saved not in _GROQ_REPLACEMENTS:
        return
    with _migration_lock:
        if saved in _announced_migrations:
            return
        _announced_migrations.add(saved)
    print(f"   → Saved Groq model {saved} retired from the free tier; using {model}.")


def _real_key(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not value.strip().upper().startswith("YOUR_")


def providers() -> list:
    """(name, url, model, headers) for each usable fallback, in order."""
    out = []
    groq = getattr(config, "GROQ_API_KEY", "")
    if _real_key(groq):
        out.append(("groq", "https://api.groq.com/openai/v1/chat/completions",
                    effective_groq_model(),
                    {"Authorization": f"Bearer {groq}"}))
    orouter = getattr(config, "OPENROUTER_API_KEY", "")
    if _real_key(orouter):
        out.append(("openrouter", "https://openrouter.ai/api/v1/chat/completions",
                    getattr(config, "OPENROUTER_MODEL", "openrouter/free") or "openrouter/free",
                    {"Authorization": f"Bearer {orouter}",
                     "HTTP-Referer": "https://github.com/raunakpatil/youtube-agentic-ai-studio",
                     "X-Title": "YT Agentic Studio"}))
    return out


def chat(prompt: str, temperature: float = 0.9, stop_event=None) -> str:
    if getattr(config, "LLM_ROUTE", "auto") == "local":
        from apis.local_llm import generate
        return generate(prompt, temperature=temperature, stop_event=stop_event)
    errors = []
    for name, url, model, headers in providers():
        if name == "groq":
            _announce_groq_migration(model)
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Stopped before AI generation")
        if name == "openrouter":
            try:
                # A legacy saved model may be paid or have disappeared. Never
                # submit it speculatively, and never remove the :free suffix.
                # Plain text generation needs no strict JSON-schema support.
                model = openrouter._select_model(model, require_schema=False)
            except openrouter.OpenRouterError as exc:
                print(f"   ⚠ OpenRouter skipped: {exc}")
                errors.append(f"openrouter: {exc.code}; select openrouter/free in Settings")
                continue
        attempts = 2
        for attempt in range(attempts):
            try:
                print(f"   → Fallback LLM: {name} ({openrouter._redact(str(model))})…")
                if name == "openrouter":
                    # Share the local free quota with the visual planner. Failed
                    # provider requests also consume this conservative budget.
                    openrouter._claim_request(getattr(config, "OPENROUTER_API_KEY", ""))
                d = post_json(url, {
                    "model": model,
                    "messages": [{"role": "user", "content": openrouter._redact(prompt)}],
                    "temperature": temperature,
                }, timeout=60, headers=headers)
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Stopped after AI generation")
                if name == "openrouter":
                    usage = d.get("usage") or {}
                    cost = Decimal(str(usage.get("cost", "0"))) if isinstance(usage, dict) else Decimal("NaN")
                    if not cost.is_finite() or cost > 0:
                        raise openrouter.OpenRouterError("unexpected_cost", "The provider reported invalid pricing for a free-only request.")
                content = d["choices"][0]["message"].get("content")
                text = openrouter._redact(content).strip() if isinstance(content, str) else ""
                if text:
                    return text
                errors.append(f"{name}: empty response")
                break
            except InterruptedError:
                raise
            except Exception as e:
                msg = str(e)
                if isinstance(e, openrouter.OpenRouterError):
                    print(f"   ⚠ {name} unavailable: {e}")
                    errors.append(f"{name}: {e.code}")
                    break
                response = getattr(e, "response", None)
                status = getattr(response, "status_code", None)
                if name == "openrouter":
                    # Free quotas often last until the next day. Retrying 402
                    # or 429 immediately burns quota without restoring service.
                    limited = status in {401, 402, 403, 429} or any(code in msg for code in ("401", "402", "403", "429"))
                    transient = status in {500, 502, 503, 504} or any(code in msg for code in ("500", "502", "503", "504", "timed out", "Connection"))
                    if not limited and transient and attempt + 1 < attempts:
                        print("   ⚠ Free OpenRouter model unavailable — retrying once…")
                        time.sleep(.25)
                        continue
                    reason = "quota_or_access_unavailable" if limited else "request_failed"
                    print(f"   ⚠ OpenRouter unavailable: {reason}")
                    errors.append(f"openrouter: {reason}")
                    break
                # Daily free quotas cannot be repaired by sleeping and repeating.
                if status not in {401, 402, 403, 429} and attempt + 1 < attempts and any(
                        c in msg for c in ("503", "502", "504", "timed out", "Connection")):
                    print(f"   ⚠ {name} temporarily unavailable — retrying once…")
                    if stop_event is not None:
                        if stop_event.wait(.25):
                            raise InterruptedError("Stopped before retrying AI generation")
                    else:
                        time.sleep(.25)
                    continue
                # Provider exception bodies can contain request text or keys.
                # Keep diagnostic HTTP status, never the arbitrary raw body.
                reason = f"HTTP {status}" if isinstance(status, int) else "request failed"
                print(f"   ⚠ {name} failed: {reason}")
                errors.append(f"{name}: {reason}")
                break
    raise RuntimeError("Free AI is unavailable — " + " | ".join(errors or ["configure a free provider key or an installed local Ollama model"]))
