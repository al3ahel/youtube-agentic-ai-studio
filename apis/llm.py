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
from apis.llm_session import (BudgetExceeded, Failure, check_stop, classify_failure,
                              effective_stop_event, generation_session, request_scope, session_scope)

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


def chat(prompt: str, temperature: float = 0.9, stop_event=None, *,
         exclude_providers=(), provider_info=None) -> str:
    """Try the configured free chain, optionally skipping a failed provider.

    ``provider_info`` is filled only by the request that returned the text; it
    avoids guessing the responder from which credentials happen to be saved.
    """
    with session_scope(stop_event) as session, request_scope() as budget:
        return _chat(prompt, temperature, effective_stop_event(stop_event), exclude_providers,
                     provider_info, session, budget)


def _chat(prompt, temperature, stop_event, exclude_providers, provider_info, session, budget):
    check_stop(stop_event)
    if provider_info is not None:
        provider_info.clear()
    if getattr(config, "LLM_ROUTE", "auto") == "local":
        from apis.local_llm import generate
        text = generate(prompt, temperature=temperature, stop_event=stop_event)
        check_stop(stop_event)
        session.succeeded("ollama", getattr(config, "OLLAMA_MODEL", ""))
        if provider_info is not None:
            provider_info.update(provider="ollama", model=getattr(config, "OLLAMA_MODEL", ""))
        return text
    errors = []
    configured = [provider for provider in providers() if provider[0] not in exclude_providers]
    for index, (name, url, model, headers) in enumerate(configured):
        check_stop(stop_event)
        credential = headers.get("Authorization", "")
        previous = session.failure(name, credential)
        if previous:
            print(f"   → Skipping {name.title()} for this generation: {previous.message} earlier.")
            errors.append(f"{name}: {previous.message}")
            continue
        if name == "groq":
            _announce_groq_migration(model)
        alternatives = any(not session.failure(other[0], other[3].get("Authorization", ""))
                           for other in configured[index + 1:])
        if name == "openrouter":
            # Model discovery is part of the same bounded hosted request, not
            # an unaccounted extra wait before starting its provider budget.
            budget.timeout(name, alternatives=alternatives)
            try:
                # A legacy saved model may be paid or have disappeared. Never
                # submit it speculatively, and never remove the :free suffix.
                # Plain text generation needs no strict JSON-schema support.
                model = openrouter._select_model(model, require_schema=False)
            except openrouter.OpenRouterError as exc:
                check_stop(stop_event)
                print(f"   ⚠ OpenRouter skipped: {exc}")
                errors.append(f"openrouter: {exc.code}; select openrouter/free in Settings")
                session.failed(name, Failure(exc.code, "the selected free model is unavailable"), credential)
                continue
        attempts = 2
        for attempt in range(attempts):
            try:
                check_stop(stop_event)
                timeout = budget.timeout(name, alternatives=alternatives)
                started = time.monotonic()
                print(f"   → Trying {name.title()} {openrouter._redact(str(model))} (up to {timeout:.0f}s)…")
                if name == "openrouter":
                    # Share the local free quota with the visual planner. Failed
                    # provider requests also consume this conservative budget.
                    openrouter._claim_request(getattr(config, "OPENROUTER_API_KEY", ""))
                d = post_json(url, {
                    "model": model,
                    "messages": [{"role": "user", "content": openrouter._redact(prompt)}],
                    "temperature": temperature,
                }, timeout=timeout, headers=headers)
                check_stop(stop_event)
                if name == "openrouter":
                    usage = d.get("usage") or {}
                    cost = Decimal(str(usage.get("cost", "0"))) if isinstance(usage, dict) else Decimal("NaN")
                    if not cost.is_finite() or cost > 0:
                        raise openrouter.OpenRouterError("unexpected_cost", "The provider reported invalid pricing for a free-only request.")
                content = d["choices"][0]["message"].get("content")
                text = openrouter._redact(content).strip() if isinstance(content, str) else ""
                if text:
                    session.succeeded(name, model)
                    if provider_info is not None:
                        provider_info.update(provider=name, model=model)
                    print(f"   ✓ {name.title()} {openrouter._redact(str(model))} answered in {time.monotonic() - started:.1f}s.")
                    return text
                errors.append(f"{name}: empty response")
                session.failed(name, Failure("response", "an empty response was returned"), credential)
                break
            except InterruptedError:
                raise
            except BudgetExceeded as exc:
                check_stop(stop_event)
                if exc.provider is None:
                    raise RuntimeError("Free AI generation reached its bounded retry time; try again when a configured provider is available.") from None
                failure = Failure("budget", "its retry time budget was reached")
                session.failed(name, failure, credential)
                errors.append(f"{name}: {failure.message}")
                print(f"   ⚠ {name.title()}: {failure.message}; trying the next configured free option.")
                break
            except Exception as e:
                check_stop(stop_event)
                if isinstance(e, openrouter.OpenRouterError):
                    print(f"   ⚠ {name} unavailable: {e}")
                    errors.append(f"{name}: {e.code}")
                    session.failed(name, Failure(e.code, "the free service is unavailable"), credential)
                    break
                failure = classify_failure(e)
                print(f"   ⚠ {name.title()}: {failure.message}.")
                # A timed-out request has already consumed its useful wait;
                # immediately repeating it doubles the delay before fallback.
                if failure.code in {"transient", "connection"} and attempt + 1 < attempts:
                    print(f"   → Retrying {name.title()} once within this request's time budget…")
                    if stop_event is not None:
                        if stop_event.wait(.25):
                            raise InterruptedError("Stopped before retrying AI generation")
                    else:
                        time.sleep(.25)
                    continue
                session.failed(name, failure, credential)
                reason = ("quota_or_access_unavailable" if name == "openrouter" and failure.code in {"quota", "access"}
                          else failure.message)
                errors.append(f"{name}: {reason}")
                break
    raise RuntimeError("Free AI is unavailable — " + " | ".join(errors or ["configure a free provider key or an installed local Ollama model"]))
