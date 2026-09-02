"""JARVIS License System — Ed25519-signed license keys for paid distribution.

Keys are self-contained and verified OFFLINE using the public keyring baked
into the app (``core/pubkeys.json``). No secret is shipped to users; the
signing private key lives only on the minting side (the license server / mint
tool). Key rotation is supported: any public key currently in the ring can
verify, so issuing new keys never breaks previously-sold ones.

Keys look like:  JARV-XXXXX-XXXXX-...-XXXXX  (a base64url payload+signature)

Activation state is stored under the JARVIS data dir (``license.lic``).
"""
import os
import time

from core import config
from core import license_keys as lk


def generate_license(priv_pem, flags=0, expiry_days=0, key_id=None, issue_time=None):
    """Mint a new license key. Requires the signing private key (PKCS8 PEM).

    The packaged app does NOT have the private key; use this only on the
    minting side (license server or the ``scripts/mint_key.py`` tool).
    """
    return lk.mint_key(priv_pem, flags=flags, expiry_days=expiry_days,
                       key_id=key_id, issue_time=issue_time)


def _keyring():
    return lk.load_keyring()


def validate_license(key):
    """Return True if the key is structurally valid AND its Ed25519 signature
    matches one of the embedded public keys (and is not expired)."""
    ok, _reason, _info = lk.verify_key(key, _keyring())
    return ok


def validate_license_structure(key):
    """Return True if the key has a valid prefix/length/base64 form (no
    signature/expiry check) — useful for live UX before full verify."""
    try:
        lk.parse_key(key)
        return True
    except Exception:
        return False


def key_info(key):
    """Return parsed payload dict, or None if the key is malformed."""
    try:
        _payload, _sig = lk.parse_key(key)
        return lk.parse_payload(_payload)
    except Exception:
        return None


# ──────────────────── Activation state (local disk) ────────────────────

def _lic_path():
    return os.path.join(config.PROJECT_DIR, "license.lic")


def _read_field(path, name):
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        return ""
    return ""


def _write_activation(path, key, info):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"key={key}\n")
        f.write(f"activated={int(time.time())}\n")
        f.write(f"flags={info.get('flags', 0)}\n")
        f.write(f"expiry_days={info.get('expiry_days', 0)}\n")


def activate(key):
    """Validate and store an activated license. Returns (ok, reason).

    Works offline: verifies the embedded signature only. (Online server
    activation/revocation is layered on top by the caller when a server is
    configured.)
    """
    stripped = (key or "").strip()
    if not stripped:
        return False, "No license key entered."
    if not validate_license_structure(stripped):
        return False, "Invalid license format."
    okay, reason, info = lk.verify_key(stripped, _keyring())
    if not okay:
        return False, reason or "License key is not valid."
    _write_activation(_lic_path(), stripped, info or {})
    return True, ""


def is_licensed():
    """True if a valid (structurally + signature-valid) activation is stored."""
    path = _lic_path()
    key = _read_field(path, "key")
    if not key:
        return False
    return validate_license(key)


def current_key():
    return _read_field(_lic_path(), "key")


def auto_activate(server_validate=None):
    """Try to activate from config.LICENSE_KEY if present and not yet licensed.

    ``server_validate`` is an optional callable(key)->(ok, reason) used when a
    license server is configured; if it returns not-ok, activation is refused.
    Returns (activated, reason)."""
    key = (getattr(config, "LICENSE_KEY", "") or "").strip()
    if not key:
        return False, ""
    if is_licensed():
        return False, "already licensed"
    if server_validate is not None:
        ok, reason = server_validate(key)
        if not ok:
            return False, reason or "server rejected key"
    return activate(key)
