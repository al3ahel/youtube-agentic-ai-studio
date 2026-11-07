"""
LLM fallback chain — used when Gemini has no key or every Gemini model is
out of quota. All three speak the OpenAI chat-completions format.

  groq          free key   · very fast Llama models
  openrouter    free key   · ":free" community models
  pollinations  keyless    · optional last fallback, subject to service limits
"""
import time
from decimal import Decimal

import config
from apis.http import post_json
from apis import openrouter


def _real_key(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not value.strip().upper().startswith("YOUR_")


def providers() -> list:
    """(name, url, model, headers) for each usable fallback, in order."""
    out = []
    groq = getattr(config, "GROQ_API_KEY", "")
    if _real_key(groq):
        out.append(("groq", "https://api.groq.com/openai/v1/chat/completions",
                    getattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile"),
                    {"Authorization": f"Bearer {groq}"}))
    orouter = getattr(config, "OPENROUTER_API_KEY", "")
    if _real_key(orouter):
        out.append(("openrouter", "https://openrouter.ai/api/v1/chat/completions",
                    getattr(config, "OPENROUTER_MODEL", "openrouter/free") or "openrouter/free",
                    {"Authorization": f"Bearer {orouter}",
                     "HTTP-Referer": "https://github.com/raunakpatil/youtube-agentic-ai-studio",
                     "X-Title": "YT Agentic Studio"}))
    if getattr(config, "KEYLESS_LLM_FALLBACK", True):
        out.append(("pollinations", "https://text.pollinations.ai/openai", "openai", {}))
    return out


def chat(prompt: str, temperature: float = 0.9) -> str:
    errors = []
    for name, url, model, headers in providers():
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
        attempts = 2 if name == "openrouter" else 3
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
                }, timeout=180, headers=headers)
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
                # Free tiers rate-limit bursts (Pollinations answers 402/429) — back off and retry
                if attempt < 2 and any(c in msg for c in ("402", "429", "503", "Max retries",
                                                           "NameResolution", "timed out", "Connection")):
                    wait = 20 * (attempt + 1)
                    print(f"   ⚠ {name} is rate-limiting — retrying in {wait}s…")
                    time.sleep(wait)
                    continue
                # Provider exception bodies can contain request text or keys.
                # Keep diagnostic HTTP status, never the arbitrary raw body.
                reason = f"HTTP {status}" if isinstance(status, int) else "request failed"
                print(f"   ⚠ {name} failed: {reason}")
                errors.append(f"{name}: {reason}")
                break
    raise RuntimeError("All fallback LLMs failed — " + " | ".join(errors or ["none configured"]))
