import pytest

from app import security
from app.services import tls


def test_self_signed_round_trip_validates():
    cert_pem, key_pem = tls.generate_self_signed(["jarn.test"], ["192.0.2.202"])
    summary = tls.validate_custom(cert_pem.encode(), key_pem.encode())
    assert summary["self_signed"] is True
    assert "jarn.test" in summary["sans"] and "192.0.2.202" in summary["sans"]
    assert summary["days_left"] <= tls.SELF_SIGNED_DAYS


def test_mismatched_key_is_rejected():
    cert_pem, _ = tls.generate_self_signed(["a.test"], [])
    _, other_key = tls.generate_self_signed(["b.test"], [])
    with pytest.raises(ValueError, match="does not match"):
        tls.validate_custom(cert_pem.encode(), other_key.encode())


def test_encrypted_key_is_rejected():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    cert_pem, _ = tls.generate_self_signed(["c.test"], [])
    encrypted = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.BestAvailableEncryption(b"pw")).decode()
    with pytest.raises(ValueError, match="encrypted"):
        tls.validate_custom(cert_pem.encode(), encrypted.encode())


def test_seal_round_trip_and_legacy_passthrough():
    data = {"username": "root", "password": "hunter2"}
    sealed = security.seal(data)
    assert "hunter2" not in str(sealed)
    assert security.unseal(sealed) == data
    assert security.unseal(data) == data  # rows stored before encryption are returned unchanged


def test_tampered_ciphertext_is_rejected(monkeypatch):
    sealed = security.seal({"k": "v"})
    token = sealed[security.SEALED_MARKER]
    version, _, body = token.partition(":")
    import base64
    raw = bytearray(base64.b64decode(body))
    raw[-1] ^= 0x01
    tampered = {security.SEALED_MARKER: f"{version}:{base64.b64encode(bytes(raw)).decode()}"}
    with pytest.raises(Exception):
        security.unseal(tampered)


def test_missing_key_refuses_to_store(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "credential_encryption_key", None)
    with pytest.raises(RuntimeError, match="CREDENTIAL_ENCRYPTION_KEY"):
        security.seal({"x": 1})
