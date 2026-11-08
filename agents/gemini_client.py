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
from apis.llm_session import (BudgetExceeded, Failure, StructuredOutputError, check_stop, classify_failure,
                              effective_stop_event, request_scope, session_scope)

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
_SDK_UPGRADE_MESSAGE = (
    "Gemini needs google-genai 2.33.0 or newer with request timeout and retry controls. "
    "Activate this project's virtual environment, run python -m pip install -r requirements.txt, "
    "then restart the studio."
)


class GeminiSDKCompatibilityError(RuntimeError):
    """An old/missing SDK is an environment issue, not a key or quota failure."""


def _sdk_types():
    try:
        from google.genai import types
        supported = (hasattr(types, "HttpRetryOptions")
                     and "retry_options" in getattr(getattr(types, "HttpOptions", None), "model_fields", {})
                     and "http_options" in getattr(getattr(types, "GenerateContentConfig", None), "model_fields", {}))
    except ImportError:
        raise GeminiSDKCompatibilityError(_SDK_UPGRADE_MESSAGE) from None
    if not supported:
        raise GeminiSDKCompatibilityError(_SDK_UPGRADE_MESSAGE)
    return types


def gemini_configured() -> bool:
    key = getattr(config, "GEMINI_API_KEY", "") or ""
    return bool(key) and not key.upper().startswith("YOUR_")


def _get_client():
    """Rebuild the client if the key changed in Settings."""
    global _client, _client_key
    if _client is None or _client_key != config.GEMINI_API_KEY:
        types = _sdk_types()
        from google import genai
        _client = genai.Client(api_key=config.GEMINI_API_KEY,
                               http_options=types.HttpOptions(timeout=60000, retry_options=types.HttpRetryOptions(attempts=1)))
        _client_key = config.GEMINI_API_KEY
    return _client


def _is_gone(err: Exception) -> bool:
    """Model retired or unknown — retrying it is pointless."""
    return classify_failure(err).code == "model"


def _should_switch_model(err: Exception) -> bool:
    """Return True for any error code that means we should try the next model."""
    return classify_failure(err).code in {"model", "quota", "transient", "timeout", "connection"}


def _generate_gemini(prompt: str, starting_model: str | None, stop_event=None, *, provider_info=None,
                     has_fallback=False, json_mode=False) -> str:
    global last_provider
    types = _sdk_types()
    client = _get_client()
    chain = build_chain(starting_model or getattr(config, "GEMINI_MODEL", None))
    key = config.GEMINI_API_KEY
    stop_event = effective_stop_event(stop_event)
    from apis.openrouter import _redact
    with session_scope(stop_event) as session, request_scope() as budget:
        last_failure = Failure("request", "no configured Gemini model answered")
        for model in chain:
            check_stop(stop_event)
            previous = session.failure("gemini", key, model)
            if previous:
                print(f"   → Skipping Gemini {_redact(model)} for this generation: {previous.message} earlier.")
                last_failure = previous
                continue
            try:
                timeout = budget.timeout("gemini", alternatives=has_fallback)
                started = time.monotonic()
                print(f"   → Trying Gemini {_redact(model)} (up to {timeout:.0f}s)…")
                output_options = ({"response_mime_type": "application/json",
                                   "system_instruction": "Return one complete JSON object without Markdown or other text."}
                                  if json_mode else {})
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.9,
                        http_options=types.HttpOptions(timeout=max(1, round(timeout * 1000)),
                                                       retry_options=types.HttpRetryOptions(attempts=1)),
                        **output_options),
                )
                check_stop(stop_event)
                if not isinstance(response.text, str) or not response.text.strip():
                    raise ValueError("The model returned no text.")
                last_provider = model
                session.succeeded("gemini", model)
                if provider_info is not None:
                    provider_info.update(provider="gemini", model=model)
                    if json_mode:
                        candidates = getattr(response, "candidates", None) or []
                        reason = getattr(candidates[0], "finish_reason", None) if candidates else None
                        reason = getattr(reason, "value", reason)
                        normalized = ({"STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "content_filter",
                                       "RECITATION": "content_filter", "BLOCKLIST": "content_filter",
                                       "PROHIBITED_CONTENT": "content_filter"}.get(reason)
                                      if isinstance(reason, str) else None)
                        if normalized:
                            provider_info["finish_reason"] = normalized
                print(f"   ✓ Gemini {_redact(model)} answered in {time.monotonic() - started:.1f}s.")
                return _redact(response.text).strip()
            except InterruptedError:
                raise
            except BudgetExceeded as exc:
                if exc.provider:
                    session.failed("gemini", Failure("budget", "its retry time budget was reached"), key)
                raise
            except Exception as e:
                check_stop(stop_event)
                last_failure = classify_failure(e)
                print(f"   ⚠ Gemini {_redact(model)}: {last_failure.message}.")
                if last_failure.code in {"model", "quota", "transient"}:
                    session.failed("gemini", last_failure, key, model)
                    continue
                # A connection/timeout or account failure cannot be repaired by
                # immediately repeating the request against every model name.
                session.failed("gemini", last_failure, key)
                raise RuntimeError(f"Gemini {last_failure.message}.") from None
        session.failed("gemini", last_failure, key)
        raise RuntimeError(f"No configured Gemini model answered: {last_failure.message}.")


def generate(prompt: str, starting_model: str | None = None, *, stop_event=None,
             exclude_providers=(), provider_info=None, json_mode=False, json_schema=None) -> str:
    """Generate text; opt-in JSON output still requires caller schema validation."""
    with session_scope(stop_event), request_scope():
        return _generate(prompt, starting_model, stop_event=effective_stop_event(stop_event),
                         exclude_providers=exclude_providers, provider_info=provider_info,
                         json_mode=json_mode, json_schema=json_schema)


def _generate(prompt, starting_model=None, *, stop_event=None, exclude_providers=(), provider_info=None,
              json_mode=False, json_schema=None):
    global last_provider
    from apis import llm
    check_stop(stop_event)
    if not isinstance(json_mode, bool):
        raise ValueError("json_mode must be true or false.")

    if provider_info is not None:
        provider_info.clear()
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 60000:
        raise ValueError("AI requests must contain 1–60,000 characters.")
    if json_schema is not None:
        if not json_mode:
            raise ValueError("A structured schema requires JSON mode.")
        from apis.openrouter import _validate_plan_request
        _validate_plan_request(prompt, json_schema)
    from apis.openrouter import _redact
    prompt = _redact(prompt)
    route = getattr(config, "LLM_ROUTE", "auto")
    local_model = getattr(config, "OLLAMA_MODEL", "")
    local_endpoint = getattr(config, "OLLAMA_BASE_URL", "")
    with session_scope(stop_event) as session:
        previous_local = session.failure("ollama", local_endpoint, local_model)
    if route == "local" or local_model and not previous_local:
        from apis import local_llm
        try:
            print(f"   → Trying local Ollama {_redact(local_model)}…")
            local_info = {}
            text = local_llm.generate(prompt, stop_event=stop_event, json_mode=json_mode,
                                      provider_info=local_info,
                                      **({"json_schema": json_schema} if json_schema is not None else {}))
            check_stop(stop_event)
            last_provider = "Ollama · " + config.OLLAMA_MODEL
            if provider_info is not None:
                provider_info.update(provider="ollama", model=config.OLLAMA_MODEL)
                if json_mode and "finish_reason" in local_info:
                    provider_info["finish_reason"] = local_info["finish_reason"]
            with session_scope(stop_event) as session:
                session.succeeded("ollama", config.OLLAMA_MODEL)
            print(f"   ✓ Local Ollama {_redact(config.OLLAMA_MODEL)} answered.")
            return text
        except InterruptedError:
            raise
        except local_llm.LocalLLMError as exc:
            check_stop(stop_event)
            if route == "local":
                raise
            with session_scope(stop_event) as session:
                session.failed("ollama", Failure(exc.code, "the selected local model was unavailable"),
                               local_endpoint, local_model)
            print("   ⚠ Local model unavailable — trying configured free cloud providers…")
    elif local_model and previous_local:
        print("   → Skipping the unavailable local model for this generation; using configured free cloud fallback.")

    gemini_err = None
    with session_scope(stop_event) as session:
        previous = session.failure("gemini", config.GEMINI_API_KEY)
    if gemini_configured() and "gemini" not in exclude_providers and not previous:
        try:
            alternatives = any(provider[0] not in exclude_providers for provider in llm.providers())
            return _generate_gemini(prompt, starting_model, stop_event, provider_info=provider_info,
                                    has_fallback=alternatives, json_mode=json_mode)
        except InterruptedError:
            raise
        except Exception as e:
            check_stop(stop_event)
            gemini_err = e
            sdk_incompatible = isinstance(e, GeminiSDKCompatibilityError)
            with session_scope(stop_event) as session:
                if not session.failure("gemini", config.GEMINI_API_KEY):
                    failure = (Failure("sdk_upgrade", "an incompatible google-genai SDK was detected")
                               if sdk_incompatible else classify_failure(e))
                    session.failed("gemini", failure, config.GEMINI_API_KEY)
            if sdk_incompatible:
                print("   ⚠ " + _SDK_UPGRADE_MESSAGE)
            if not any(provider[0] not in exclude_providers for provider in llm.providers()):
                if sdk_incompatible:
                    raise RuntimeError(_SDK_UPGRADE_MESSAGE) from None
                raise RuntimeError("The configured free Gemini models are unavailable. Check the key and free quota, or select an installed local Ollama model.") from None
            print("   ⚠ Gemini unavailable — trying the configured free fallback providers…")
    elif gemini_configured() and previous and "gemini" not in exclude_providers:
        gemini_err = (GeminiSDKCompatibilityError(_SDK_UPGRADE_MESSAGE) if previous.code == "sdk_upgrade"
                      else RuntimeError("Gemini " + previous.message))
        print(f"   → Skipping Gemini for this generation: {previous.message} earlier; using configured free fallback.")
    else:
        print("   → Using configured free fallback LLMs")

    try:
        response_info = {}
        text = llm.chat(prompt, stop_event=stop_event, exclude_providers=exclude_providers,
                        provider_info=response_info, json_mode=json_mode)
        if provider_info is not None:
            provider_info.update(response_info)
        provider = response_info.get("provider")
        last_provider = (({"groq": "Groq", "openrouter": "OpenRouter", "ollama": "Ollama"}.get(provider, provider)
                          + " · " + response_info["model"]) if provider and response_info.get("model") else "configured free AI")
        return text
    except (InterruptedError, StructuredOutputError):
        raise
    except Exception as e:
        if isinstance(gemini_err, GeminiSDKCompatibilityError):
            raise RuntimeError(f"{_SDK_UPGRADE_MESSAGE} Configured free fallback: {_redact(str(e))}") from None
        if gemini_err:
            raise RuntimeError(f"Gemini is unavailable. {_redact(str(e))}") from None
        raise RuntimeError(
            f"{_redact(str(e))}\nChoose a local Ollama model or add a free-tier Gemini, Groq or OpenRouter key in Settings."
        ) from None
