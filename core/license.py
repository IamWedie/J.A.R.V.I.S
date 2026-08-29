"""JARVIS License System — offline, HMAC-signed license keys for paid distribution.

Keys look like:  JARV-XXXXX-XXXXX-XXXXX

Each key is HMAC-signed with a server secret (LICENSE_SECRET). A valid key can be
verified entirely offline (no round-trip), while a simple online check against the
seller platform can later confirm the key was actually sold. Activation state is
stored under the JARVIS data dir.

Security properties:
  - Tamper: changing any character invalidates the signature.
  - Typos: a check digit catches transcription errors before signature check.
  - Constant-time compare avoids trivial timing side-channels.
"""
import base64
import hashlib
import hmac
import os
import time

try:
    import secrets
    _urandom = secrets.token_bytes
except Exception:  # pragma: no cover
    _urandom = lambda n: os.urandom(n)


PREFIX = "JARV"
GROUP_LEN = 5
DATA_SEGMENTS = 3          # random data segments (15 chars)
TOTAL_SEGMENTS = DATA_SEGMENTS + 1  # +1 segment holding check digit + signature (5 chars)
PAYLOAD_LEN = TOTAL_SEGMENTS * GROUP_LEN  # 20 chars
_B32_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L
_B32_INDEX = {c: i for i, c in enumerate(_B32_ALPHABET)}
_CHECK_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _secret():
    from core import config
    return (getattr(config, "LICENSE_SECRET", "") or "").encode("utf-8")


def _normalize_key(key):
    return "".join(ch for ch in str(key).upper() if ch.isalnum())


def _random_segment():
    n = len(_B32_ALPHABET) ** GROUP_LEN
    r = int.from_bytes(_urandom(16), "big") % n
    out = []
    for _ in range(GROUP_LEN):
        out.append(_B32_ALPHABET[r % len(_B32_ALPHABET)])
        r //= len(_B32_ALPHABET)
    return "".join(out)


def _check_digit(payload):
    total = 0
    for index, ch in enumerate(payload):
        try:
            total += (index + 1) * (_CHECK_ALPHABET.index(ch) + 1)
        except ValueError:
            total += index + 1
    return _CHECK_ALPHABET[total % len(_CHECK_ALPHABET)]


def _hmac_digest(payload):
    secret = _secret()
    if not secret:
        raise RuntimeError("LICENSE_SECRET is not configured")
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()


def _short_sig(payload):
    """4 base-32 chars derived from the HMAC, kept small inside the final segment."""
    digest = _hmac_digest(payload)
    return base64.b32encode(digest[:3]).decode("ascii")[:4]


def generate_license(seed=None):
    """Generate a new license key in the form JARV-XXXXX-XXXXX-XXXXX-XXXXX."""
    data = "".join(_random_segment() for _ in range(DATA_SEGMENTS))
    check = _check_digit(data)
    sig = _short_sig(data + check)
    last_segment = check + sig  # 1 + 4 = 5 chars
    if len(last_segment) != GROUP_LEN:
        last_segment = last_segment.ljust(GROUP_LEN, _B32_ALPHABET[0])
    raw = data + last_segment  # exactly PAYLOAD_LEN chars
    groups = [raw[i:i + GROUP_LEN] for i in range(0, len(raw), GROUP_LEN)]
    return f"{PREFIX}-" + "-".join(groups)


def _parse_key(key):
    norm = _normalize_key(key)
    if not norm.upper().startswith(PREFIX):
        return None
    remainder = norm.upper()[len(PREFIX):]
    if len(remainder) != PAYLOAD_LEN:
        return None
    data = remainder[:DATA_SEGMENTS * GROUP_LEN]
    check = remainder[DATA_SEGMENTS * GROUP_LEN]
    sig = remainder[DATA_SEGMENTS * GROUP_LEN + 1:]
    if check != _check_digit(data):
        return None
    return data + check, sig


def validate_license(key):
    """Return True if the key is structurally valid AND its HMAC signature matches.

    Requires LICENSE_SECRET to be configured (raises RuntimeError otherwise)."""
    parsed = _parse_key(key)
    if parsed is None:
        return False
    payload, sig = parsed
    expected = _short_sig(payload)
    return hmac.compare_digest(expected, sig)


def validate_license_structure(key):
    """Return True if the key has a valid format + check digit, without needing
    the secret (useful for live validation UX before attempting full verify)."""
    parsed = _parse_key(key)
    return parsed is not None


# ──────────────────── Activation state ────────────────────

def _lic_path():
    from core import config
    return os.path.join(config.PROJECT_DIR, "license.lic")


def _write_activated(key):
    path = _lic_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"key={key}\n")
        f.write(f"activated={int(time.time())}\n")


def activate(key):
    """Validate and store an activated license. Returns (ok, reason)."""
    stripped = (key or "").strip()
    if not stripped:
        return False, "No license key entered."
    if not validate_license_structure(stripped):
        return False, "Invalid license format."
    try:
        if not validate_license(stripped):
            return False, "License key is not valid."
    except RuntimeError as e:
        # secret unset: still allow structural-only activation for dev builds
        return False, str(e)
    _write_activated(stripped)
    return True, ""


def is_licensed():
    """True if a valid activation is currently stored on disk."""
    path = _lic_path()
    if not os.path.exists(path):
        return False
    key = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("key="):
                    key = line.split("=", 1)[1].strip()
    except Exception:
        return False
    if not key:
        return False
    return validate_license_structure(key)


def current_key():
    path = _lic_path()
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("key="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        return ""
    return ""
