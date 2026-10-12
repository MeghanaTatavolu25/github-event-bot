import hashlib
import hmac

import pytest
from cryptography.fernet import InvalidToken

from app.security import decrypt_token, encrypt_token, verify_signature

SECRET = "test-secret"
BODY = b'{"action": "opened"}'


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_matches_githubs_documented_example():
    # Fixed values from GitHub's docs, so we are not only testing our own code against itself.
    secret, body = "It's a Secret to Everybody", b"Hello, World!"
    header = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
    assert verify_signature(secret, body, header)


def test_accepts_correct_signature():
    assert verify_signature(SECRET, BODY, sign(SECRET, BODY))


def test_rejects_signature_made_with_other_secret():
    assert not verify_signature(SECRET, BODY, sign("other-secret", BODY))


def test_rejects_tampered_body():
    assert not verify_signature(SECRET, BODY + b" ", sign(SECRET, BODY))


def test_rejects_missing_header():
    assert not verify_signature(SECRET, BODY, None)


def test_fails_closed_when_secret_is_empty():
    # Anyone can compute a valid signature for an empty secret, so it must never pass.
    assert not verify_signature("", BODY, sign("", BODY))


def test_rejects_non_ascii_header_instead_of_crashing():
    # A hostile header must give "invalid" (401), not an exception (500).
    assert not verify_signature(SECRET, BODY, "sha256=é")


def test_token_encryption_round_trip_hides_the_token():
    encrypted = encrypt_token("gho_abc123")
    assert "gho_abc123" not in encrypted
    assert decrypt_token(encrypted) == "gho_abc123"


def test_tampered_encrypted_token_is_rejected():
    with pytest.raises(InvalidToken):
        decrypt_token(encrypt_token("gho_abc123")[:-4] + "AAAA")
