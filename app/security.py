import base64
import hashlib
import hmac

from cryptography.fernet import Fernet

from app import config


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """True only if GitHub's signature matches what we calculate ourselves."""
    if not secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # Compare bytes, not str: compare_digest raises on non-ASCII str, and the header is attacker-controlled.
    return hmac.compare_digest(expected.encode(), signature_header.encode())


def _fernet() -> Fernet:
    # Fernet wants a 32-byte key; hashing SESSION_SECRET gives one without a second secret to manage.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(config.SESSION_SECRET.encode()).digest()))


def encrypt_token(token: str) -> str:
    """GitHub access tokens are stored encrypted, so a leaked database does not leak repo access."""
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()
