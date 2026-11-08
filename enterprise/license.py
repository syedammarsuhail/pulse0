"""Pulse0 license key validation.

Key format:  PULSE0-XXXX-XXXX-XXXX-SIG
  Parts 1-3: random hex (12 hex chars = 48 bits of entropy)
  SIG:       first 8 chars of HMAC-SHA256(parts, secret)

The signing secret is compiled into this module so that keys can be verified
offline without calling a license server.  A future version may add online
activation / seat counting.
"""
import hashlib
import hmac
import os
import re
import secrets
import time

_LICENSE_SECRET = b"p0-ent-2026-kdf-hmac-v1"

_KEY_RE = re.compile(r"^PULSE0-([0-9A-Fa-f]{4})-([0-9A-Fa-f]{4})-([0-9A-Fa-f]{4})-([0-9A-Fa-f]{8})$")


def _sign_parts(parts: str) -> str:
    return hmac.new(_LICENSE_SECRET, parts.encode(), hashlib.sha256).hexdigest()[:8]


def generate_license_key() -> str:
    parts = secrets.token_hex(6).upper()
    chunked = f"{parts[:4]}-{parts[4:8]}-{parts[8:12]}"
    sig = _sign_parts(chunked)
    return f"PULSE0-{chunked}-{sig.upper()}"


def validate_license(key: str) -> bool:
    if not key:
        return False
    m = _KEY_RE.match(key.strip())
    if not m:
        return False
    parts = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    expected = _sign_parts(parts)
    return hmac.compare_digest(m.group(4).lower(), expected.lower())


LICENSE_OK = validate_license(os.environ.get("PULSE0_LICENSE_KEY", ""))
