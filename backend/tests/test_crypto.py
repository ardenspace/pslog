"""Phase 2 — Fernet 마스터 키 + per-project secret 암복호화 테스트."""

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import (
    decrypt_secret,
    encrypt_secret,
    generate_webhook_secret,
)


def _set_master_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("pslog_FERNET_KEY", key)
    # config singleton reload — settings reads env at instantiation
    import importlib
    import app.config
    importlib.reload(app.config)
    return key


def test_encrypt_decrypt_round_trip(monkeypatch: pytest.MonkeyPatch):
    _set_master_key(monkeypatch)
    plaintext = "super-secret-webhook-token-32-bytes!"
    blob = encrypt_secret(plaintext)
    assert isinstance(blob, bytes)
    assert blob != plaintext.encode()
    assert decrypt_secret(blob) == plaintext


def test_decrypt_with_wrong_master_key_raises(monkeypatch: pytest.MonkeyPatch):
    _set_master_key(monkeypatch)
    blob = encrypt_secret("hello")

    # rotate to a different master key
    monkeypatch.setenv("pslog_FERNET_KEY", Fernet.generate_key().decode())
    import importlib
    import app.config
    importlib.reload(app.config)
    import app.core.crypto
    importlib.reload(app.core.crypto)

    from app.core.crypto import decrypt_secret as decrypt2
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        decrypt2(blob)


def test_generate_webhook_secret_length(monkeypatch: pytest.MonkeyPatch):
    _set_master_key(monkeypatch)
    s = generate_webhook_secret()
    # token_urlsafe(32) → 43자 url-safe base64
    assert isinstance(s, str)
    assert len(s) >= 43
