"""JARVIS license cryptography — Ed25519 signed license keys with key rotation.

Key architecture
----------------
- License keys are self-contained: they embed a payload + a 64-byte Ed25519
  signature over that payload. The app can verify a key's authenticity fully
  OFFLINE using only the baked-in public keyring (no secret ships in the app).
- The private key never ships in the app. It lives on the minting side (the
  license server / the mint tool) and is used to sign new keys.
- Rotation: the app embeds a *ring* of active public keys (``core/pubkeys.json``)
  so you can rotate the signing key without breaking previously-issued keys.
  Old public keys remain in the ring until you explicitly retire them.

Key format
----------
    JARV-XXXXX-XXXXX-...-XXXXX

A URL-safe base32 (RFC 4648) encoding of::

    payload(18 bytes) || signature(64 bytes)

where payload = version(1) | flags(1) | issue_secs_since_2020(4) | expiry_days(4) | key_id(8).

  ~132 base32 chars, grouped into 5-char blocks separated by '-'. The base32
  alphabet (A-Z2-7) never contains '-', so separators are safe to strip on parse.
"""
import base64
import json
import os
import struct
import time
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

PREFIX = "JARV"
VERSION = 0x01

FLAG_SUBSCRIPTION = 0x01
FLAG_DEVICE_BOUND = 0x02

# Seconds between 1970-01-01 and 2020-01-01, so a 32-bit field stays valid
# until ~2156 (well past any real license window).
_EPOCH_2020 = 1577836800

_PAYLOAD_LEN = 18
_SIGNATURE_LEN = 64
_RAW_LEN = _PAYLOAD_LEN + _SIGNATURE_LEN
_GROUP_LEN = 5

# Location of the embedded public keyring checked into the app.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
KEYRING_PATH = os.path.join(_THIS_DIR, "pubkeys.json")


class LicenseKeyError(Exception):
    """Raised for malformed / unusable license keys."""


# ─────────────────────────── low-level codec ───────────────────────────

def _b32_encode(raw):
    encoded = base64.b32encode(raw)
    return encoded.decode("ascii").rstrip("=")


def _b32_decode(text):
    padded = text.upper() + "=" * (-len(text) % 8)
    try:
        return base64.b32decode(padded.encode("ascii"))
    except Exception as e:
        raise LicenseKeyError("invalid key encoding") from e


def _normalize_key(key):
    if not key:
        return ""
    # Remove group separators / whitespace; base32 alphabet has no '-' so this
    # is safe. Preserves case (base32 is case-insensitive on decode).
    return "".join(ch for ch in str(key).strip() if ch not in "-_ \t")


# Public-key serialization uses plain base64 (raw Ed25519 key = 32 bytes).
def _b64_encode(raw):
    return base64.b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(text):
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.b64decode(padded.encode("ascii"))
    except Exception as e:
        raise LicenseKeyError("invalid public key encoding") from e


# ─────────────────────────── key model ───────────────────────────

def build_payload(flags=0, expiry_days=0, key_id=None, issue_time=None):
    if key_id is None:
        key_id = uuid.uuid4().bytes[:8]
    if len(key_id) != 8:
        raise LicenseKeyError("key_id must be 8 bytes")
    issue = int(issue_time if issue_time is not None else time.time()) - _EPOCH_2020
    exp = max(0, int(expiry_days))
    return (
        bytes([VERSION & 0xFF, flags & 0xFF])
        + struct.pack(">I", max(0, issue))
        + struct.pack(">I", exp)
        + key_id
    )


def parse_payload(payload):
    if len(payload) != _PAYLOAD_LEN:
        raise LicenseKeyError("bad payload length")
    version, flags = payload[0], payload[1]
    issue_secs = struct.unpack(">I", payload[2:6])[0]
    exp_days = struct.unpack(">I", payload[6:10])[0]
    key_id = payload[10:18]
    return {"version": version, "flags": flags,
            "issue_secs_since_2020": issue_secs,
            "expiry_days": exp_days, "key_id": key_id}


# ─────────────────────────── keypair helpers ───────────────────────────

def generate_keypair():
    """Return (private_pem, public_raw_b64). Private is PKCS8 PEM."""
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_b64 = _b64_encode(pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ))
    return priv_pem.decode("ascii"), pub_b64


def _load_private(priv_pem):
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(priv_pem.encode("ascii"), password=None)


def _load_public_raw(pub_b64):
    from cryptography.hazmat.primitives import serialization
    raw = _b64_decode(pub_b64)
    return Ed25519PublicKey.from_public_bytes(raw)


# ─────────────────────────── mint & verify ───────────────────────────

def mint_key(priv_pem, flags=0, expiry_days=0, key_id=None, issue_time=None):
    """Sign a new license key with the given private key (PKCS8 PEM)."""
    priv = _load_private(priv_pem)
    if not isinstance(priv, Ed25519PrivateKey):
        raise LicenseKeyError("private key is not Ed25519")
    payload = build_payload(flags=flags, expiry_days=expiry_days,
                            key_id=key_id, issue_time=issue_time)
    signature = priv.sign(payload)
    raw = payload + signature
    encoded = _b32_encode(raw)
    groups = [encoded[i:i + _GROUP_LEN] for i in range(0, len(encoded), _GROUP_LEN)]
    return PREFIX + "-" + "-".join(groups)


def parse_key(key):
    """Decode a key into (payload, signature) or raise LicenseKeyError."""
    norm = _normalize_key(key).upper()
    if not norm.startswith(PREFIX):
        raise LicenseKeyError("missing JARV prefix")
    body = norm[len(PREFIX):]
    if len(body) != 132:
        raise LicenseKeyError("unexpected key length")
    raw = _b32_decode(body)
    if len(raw) != _RAW_LEN:
        raise LicenseKeyError("bad decoded length")
    payload = raw[:_PAYLOAD_LEN]
    signature = raw[_PAYLOAD_LEN:]
    return payload, signature


def verify_key(key, public_raw_b64_list):
    """Verify a key offline against any of the given public keys (base64 raw).

    Returns (ok, reason, info). info is the parsed payload dict on success.
    """
    try:
        payload, signature = parse_key(key)
    except LicenseKeyError as e:
        return False, str(e), None
    parsed = parse_payload(payload)
    if parsed["version"] != VERSION:
        return False, "unsupported key version", parsed
    for pub_b64 in public_raw_b64_list:
        try:
            pub = _load_public_raw(pub_b64)
        except Exception:
            continue
        try:
            pub.verify(signature, payload)
        except InvalidSignature:
            continue
        except Exception:
            continue
        # signature valid
        if parsed["expiry_days"]:
            issue = parsed["issue_secs_since_2020"] + _EPOCH_2020
            if time.time() > issue + parsed["expiry_days"] * 86400:
                return False, "license expired", parsed
        return True, "", parsed
    return False, "signature not recognized", parsed


# ─────────────────────────── keyring (rotated pubkeys) ───────────────────────────

def load_keyring(keyring_path=None):
    """Return list of public key base64 strings from the embedded keyring."""
    if keyring_path is None:
        keyring_path = KEYRING_PATH
    if not os.path.exists(keyring_path):
        return []
    with open(keyring_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    entries = data if isinstance(data, list) else data.get("keys", [])
    return [e["pub"] for e in entries if e.get("pub")]


def save_keyring(entries, keyring_path=None):
    if keyring_path is None:
        keyring_path = KEYRING_PATH
    with open(keyring_path, "w", encoding="utf-8") as f:
        json.dump({"keys": entries}, f, indent=2)


def add_to_keyring(pub_b64, keyring_path=None, key_id=None):
    if keyring_path is None:
        keyring_path = KEYRING_PATH
    entries = _load_entries(keyring_path)
    entries.append({
        "id": key_id or uuid.uuid4().hex[:8],
        "created": int(time.time()),
        "pub": pub_b64,
    })
    save_keyring(entries, keyring_path)


def _load_entries(keyring_path):
    if not os.path.exists(keyring_path):
        return []
    with open(keyring_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("keys", [])
