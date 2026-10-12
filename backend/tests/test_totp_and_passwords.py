import time

from app import security
from app.services import totp


def test_rfc6238_reference_vectors():
    # RFC 6238 Appendix B, SHA-1, 8-digit codes truncated to the last 6 digits
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of "12345678901234567890"
    assert totp.code_at(secret, 59) == "287082"
    assert totp.code_at(secret, 1111111109) == "081804"
    assert totp.code_at(secret, 1234567890) == "005924"


def test_verify_accepts_neighbouring_steps_only():
    secret = totp.new_secret()
    now = 1_700_000_000
    current = totp.code_at(secret, now)
    assert totp.verify(secret, current, now=now)
    assert totp.verify(secret, totp.code_at(secret, now + 30), now=now)      # one step ahead
    assert not totp.verify(secret, totp.code_at(secret, now + 120), now=now)  # four steps ahead
    assert not totp.verify(secret, "abcdef", now=now)
    assert not totp.verify(secret, "12345", now=now)


def test_secret_format_and_uri():
    secret = totp.new_secret()
    assert len(secret) == 32 and secret.isalnum() and secret.isupper()
    uri = totp.provisioning_uri(secret, "alice")
    assert uri.startswith("otpauth://totp/")
    assert "secret=" + secret in uri and "algorithm=SHA1" in uri and "digits=6" in uri and "period=30" in uri


def test_passwords_have_no_length_limit_and_are_not_truncated():
    long_a = "a" * 200
    hashed = security.hash_password(long_a)
    assert security.verify_password(long_a, hashed)
    # Differs after the 72nd byte, which bcrypt alone would ignore
    assert not security.verify_password("a" * 100 + "b", hashed)
    assert security.verify_password("x", security.hash_password("x"))
    assert not security.verify_password("", "")
