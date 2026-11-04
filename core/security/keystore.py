"""DPAPI-protected storage for the audit HMAC key."""
from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path

from core.vault import decrypt, encrypt

KEY_BYTES = 32


class KeyStoreError(Exception):
    """Raised when an audit key file cannot be read, decrypted, or validated."""


def load_or_create_key(path) -> bytes:
    """Return the 32-byte audit key stored at *path*.

    A missing file is created with 32 random bytes, written atomically, and
    verified by decrypting what was written.  An existing file that fails to
    decrypt or does not yield exactly 32 bytes raises ``KeyStoreError``.
    """
    path = Path(path)
    if path.exists():
        try:
            encoded = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise KeyStoreError(f"cannot read audit key file {path}: {exc}") from exc
        return _decode_key(decrypt(encoded), path)

    key = os.urandom(KEY_BYTES)
    blob = encrypt(base64.b64encode(key).decode("ascii"))
    if not blob:
        raise KeyStoreError(f"DPAPI encryption failed for {path}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(blob, encoding="utf-8")
    os.replace(tmp, path)
    # Verify the persisted blob decrypts back to the exact key.
    written = path.read_text(encoding="utf-8").strip()
    round_trip = decrypt(written)
    if round_trip is None or _b64decode(round_trip, path) != key:
        raise KeyStoreError(f"audit key file failed verification after write: {path}")
    return key


def _decode_key(plaintext, path) -> bytes:
    if not plaintext:
        raise KeyStoreError(f"cannot decrypt audit key file {path}")
    key = _b64decode(plaintext, path)
    if len(key) != KEY_BYTES:
        raise KeyStoreError(
            f"audit key file {path} decrypted to {len(key)} bytes, expected {KEY_BYTES}"
        )
    return key


def _b64decode(text, path) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise KeyStoreError(f"audit key file {path} is not valid key material") from exc
