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

    A missing file is created with 32 random bytes via ``O_CREAT|O_EXCL`` —
    exactly one process can win the creation race; a loser reads and
    decrypt-verifies the winner's file instead of overwriting it.  The
    winner's blob is verified by decrypting what was written.  An existing
    file that fails to decrypt or does not yield exactly 32 bytes raises
    ``KeyStoreError``.
    """
    path = Path(path)
    if path.exists():
        return _read_key(path)

    key = os.urandom(KEY_BYTES)
    blob = encrypt(base64.b64encode(key).decode("ascii"))
    if not blob:
        raise KeyStoreError(f"DPAPI encryption failed for {path}")
    data = blob.encode("utf-8")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # Another process won the race: adopt its key, never overwrite it.
        return _read_key(path)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    # Verify the persisted blob decrypts back to the exact key.
    written = path.read_text(encoding="utf-8").strip()
    round_trip = decrypt(written)
    if round_trip is None or _b64decode(round_trip, path) != key:
        raise KeyStoreError(f"audit key file failed verification after write: {path}")
    return key


def _read_key(path) -> bytes:
    try:
        encoded = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise KeyStoreError(f"cannot read audit key file {path}: {exc}") from exc
    return _decode_key(decrypt(encoded), path)


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
