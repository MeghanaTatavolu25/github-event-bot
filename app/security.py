import hashlib
import hmac


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """True only if GitHub's signature matches what we calculate ourselves."""
    if not secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # Compare bytes, not str: compare_digest raises on non-ASCII str, and the header is attacker-controlled.
    return hmac.compare_digest(expected.encode(), signature_header.encode())