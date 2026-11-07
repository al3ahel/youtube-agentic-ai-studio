"""
LLM Client — shared across all agents.

1. Gemini: tries supported text models starting from config.GEMINI_MODEL,
   falling back through the rest of the chain on quota / overload errors.
2. Installed local Ollama models can run without an API key. Local-only mode
   never sends a prompt to a hosted provider.
3. Configured Gemini/Groq free-tier accounts and strictly free OpenRouter models
   provide hosted options. Quota exhaustion never upgrades an account/model.
"""
import time
import config

# Full list of supported models in preference order.
# The actual runtime order is determined by build_chain() below,
# which rotates this list so config.GEMINI_MODEL comes first.
_ALL_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

# Which provider answered the last call — shown in the GUI
last_provider = None


def build_chain(starting_model: str | None = None) -> list:
    """
    Return MODEL_CHAIN rotated so `starting_model` is first.
    If starting_model is not in the list (typo / unknown), falls back to
    the default order so generation always has a working chain.
    """
    preferred = (starting_model or "").strip()
    if preferred and preferred in _ALL_MODELS:
        idx = _ALL_MODELS.index(preferred)
        return _ALL_MODELS[idx:] + _ALL_MODELS[:idx]
    return list(_ALL_MODELS)


_client = None
_client_key = None


def gemini_configured() -> bool:
    key = getattr(config, "GEMINI_API_KEY", "") or ""
    return bool(key) and not key.upper().startswith("YOUR_")


def _get_client():
    """Rebuild the client if the key changed in Settings."""
    global _client, _client_key
    if _client is None or _client_key != config.GEMINI_API_KEY:
        from google import genai
        from google.genai import types
        _client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=60000))
        _client_key = config.GEMINI_API_KEY
    return _client


def _is_gone(err: Exception) -> bool:
    """Model retired or unknown — retrying it is pointless."""
    msg = str(err)
    return any(k in msg for k in ["404", "NOT_FOUND", "not found", "is not supported"])


def _should_switch_model(err: Exception) -> bool:
    """Return True for any error code that means we should try the next model."""
    msg = str(err)
    return _is_gone(err) or any(k in msg for k in [
        "429", "RESOURCE_EXHAUSTED", "quota", "rate limit",  # 429 – quota
        "503", "UNAVAILABLE", "overloaded", "high demand",   # 503 – overloaded
        "500", "INTERNAL",                                    # 500 – transient
        # dropped / flaky connections — worth a retry, not a failed run
        "10053", "10054", "Connection aborted", "Connection reset", "ConnectError",
        "RemoteDisconnected", "timed out", "Timeout", "Temporary failure", "NameResolution",
    ])


def _generate_gemini(prompt: str, starting_model: str | None, stop_event=None) -> str:
    global last_provider
    from google.genai import types
    client = _get_client()
    chain  = build_chain(starting_model or getattr(config, "GEMINI_MODEL", None))

    for model in chain:
        for attempt in range(2):
            try:
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Stopped before AI generation")
                print(f"   → Using {model}…")
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.9),
                )
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Stopped after AI generation")
                if not isinstance(response.text, str) or not response.text.strip():
                    raise ValueError("The model returned no text.")
                from apis.openrouter import _redact
                last_provider = model
                return _redact(response.text).strip()

            except InterruptedError:
                raise
            except Exception as e:
                if _is_gone(e):
                    print(f"   ⚠ {model} unavailable — trying next model…")
                    break
                if _should_switch_model(e):
                    if any(code in str(e) for code in ("429", "RESOURCE_EXHAUSTED", "quota", "rate limit")):
                        print(f"   ⚠ {model} free quota unavailable — trying the next free option…")
                        break
                    if attempt == 0:
                        print(f"   ⚠ {model} temporarily unavailable — retrying once…")
                        if stop_event is not None:
                            if stop_event.wait(.5):
                                raise InterruptedError("Stopped before retrying AI generation")
                        else:
                            time.sleep(.5)
                    else:
                        print(f"   ⚠ {model} unavailable — trying the next free option…")
                else:
                    raise   # auth error or bug — surface immediately

    raise RuntimeError(
        "All Gemini models failed. 503/500 = temporary, retry in a few minutes; "
        "429 = quota, resets at midnight Pacific (https://aistudio.google.com/apikey)."
    )


def generate(prompt: str, starting_model: str | None = None, *, stop_event=None) -> str:
    global last_provider
    from apis import llm

    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 60000:
        raise ValueError("AI requests must contain 1–60,000 characters.")
    from apis.openrouter import _redact
    prompt = _redact(prompt)
    route = getattr(config, "LLM_ROUTE", "auto")
    if route == "local" or getattr(config, "OLLAMA_MODEL", ""):
        from apis import local_llm
        try:
            text = local_llm.generate(prompt, stop_event=stop_event)
            last_provider = "Ollama · " + config.OLLAMA_MODEL
            return text
        except InterruptedError:
            raise
        except local_llm.LocalLLMError:
            if route == "local":
                raise
            print("   ⚠ Local model unavailable — trying configured free cloud providers…")

    gemini_err = None
    if gemini_configured():
        try:
            return _generate_gemini(prompt, starting_model, stop_event)
        except InterruptedError:
            raise
        except Exception as e:
            gemini_err = e
            if not llm.providers():
                raise RuntimeError("The configured free Gemini models are unavailable. Check the key and free quota, or select an installed local Ollama model.") from None
            print("   ⚠ Gemini unavailable — trying the configured free fallback providers…")
    else:
        print("   → Using configured free fallback LLMs")

    try:
        text = llm.chat(prompt, stop_event=stop_event)
        last_provider = "fallback"
        return text
    except InterruptedError:
        raise
    except Exception as e:
        if gemini_err:
            raise RuntimeError(f"Gemini is unavailable. {_redact(str(e))}") from None
        raise RuntimeError(
            f"{_redact(str(e))}\nChoose a local Ollama model or add a free-tier Gemini, Groq or OpenRouter key in Settings."
        ) from None
