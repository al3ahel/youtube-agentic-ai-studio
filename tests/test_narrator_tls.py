"""Explicit trust bundles must never turn off voice transport verification."""
import ssl
import sys
from types import SimpleNamespace

import pytest

from video.narrator import _configure_voice_tls


def test_configured_ca_preserves_verification(tmp_path, monkeypatch):
    context = ssl.create_default_context()
    certificates = context.get_ca_certs(binary_form=True)
    assert certificates, "The test runtime needs its normal trusted CA store."
    bundle = tmp_path / "configured-ca.pem"
    bundle.write_text(ssl.DER_cert_to_PEM_cert(certificates[0]), encoding="ascii")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    monkeypatch.setitem(sys.modules, "edge_tts.communicate", SimpleNamespace(_SSL_CTX=context))
    _configure_voice_tls()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert certificates[0] in context.get_ca_certs(binary_form=True)


def test_invalid_explicit_bundle_does_not_disable_tls(tmp_path, monkeypatch):
    context = ssl.create_default_context()
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    monkeypatch.setitem(sys.modules, "edge_tts.communicate", SimpleNamespace(_SSL_CTX=context))
    with pytest.raises(RuntimeError, match="trusted CA bundle could not be loaded"):
        _configure_voice_tls()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_insecure_existing_context_is_rejected(monkeypatch):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    monkeypatch.setenv("SSL_CERT_FILE", "/unused.pem")
    monkeypatch.setitem(sys.modules, "edge_tts.communicate", SimpleNamespace(_SSL_CTX=context))
    with pytest.raises(RuntimeError, match="cannot safely apply"):
        _configure_voice_tls()
