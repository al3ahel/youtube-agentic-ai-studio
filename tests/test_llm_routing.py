"""Local-only means no cloud prompt, and retired keyless services stay disabled."""
import pytest

import config
from agents import gemini_client
from apis import llm, local_llm


def test_local_only_never_falls_through_to_cloud(monkeypatch):
    monkeypatch.setattr(config, "LLM_ROUTE", "local")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "local:small")
    monkeypatch.setattr(gemini_client, "_generate_gemini", lambda *a, **k: pytest.fail("local prompt sent to Gemini"))
    monkeypatch.setattr(llm, "chat", lambda *a, **k: pytest.fail("local prompt sent to hosted fallback"))
    def unavailable(*args, **kwargs):
        raise local_llm.LocalLLMError("unavailable", "Local server unavailable")
    monkeypatch.setattr(local_llm, "generate", unavailable)
    with pytest.raises(local_llm.LocalLLMError):
        gemini_client.generate("A local-only prompt")


def test_auto_prefers_selected_installed_local_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_ROUTE", "auto")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "local:small")
    monkeypatch.setattr(local_llm, "generate", lambda prompt, **kwargs: "Locally generated")
    monkeypatch.setattr(gemini_client, "_generate_gemini", lambda *a, **k: pytest.fail("unnecessary cloud prompt"))
    assert gemini_client.generate("A prompt") == "Locally generated"
    assert gemini_client.last_provider == "Ollama · local:small"


def test_legacy_keyless_flag_cannot_enable_an_undocumented_paid_route(monkeypatch):
    monkeypatch.setattr(config, "KEYLESS_LLM_FALLBACK", True)
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    assert llm.providers() == []


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434/"])
def test_local_endpoint_settings_accept_loopback_only(url):
    assert config._valid_setting("OLLAMA_BASE_URL", url)


@pytest.mark.parametrize("url", ["https://ollama.com", "http://169.254.169.254", "http://127.0.0.1:11434/private", "http://user:pass@localhost:11434", "http://localhost:11434?proxy=1"])
def test_local_endpoint_settings_reject_remote_or_credential_urls(url):
    assert not config._valid_setting("OLLAMA_BASE_URL", url)
