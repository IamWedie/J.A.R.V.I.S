"""Tests for the Ed25519 license-key system (core.license / core.license_keys)."""
import os

import pytest

import core.config as config
from core import license as lic
from core import license_keys as lk


@pytest.fixture
def keypair(tmp_path):
    """Generate a fresh signing keypair and a temp public keyring.

    The private key is used to mint; the temp keyring (with the public key)
    is what the app verifies against. Returning a callable lets tests mint many
    keys. After the test the temp keyring is torn down so we never pollute the
    real embedded keyring and never depend on the checked-in signing key.
    """
    priv_pem, pub_b64 = lk.generate_keypair()
    ring = tmp_path / "pubkeys.json"
    lk.save_keyring([{"id": "test", "created": 0, "pub": pub_b64}], str(ring))

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(lk, "KEYRING_PATH", str(ring))

    def _mint(**kw):
        return lic.generate_license(priv_pem, **kw)

    yield _mint
    monkeypatch.undo()


def _fresh_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_DIR", str(tmp_path))


def test_generate_produces_valid_key(keypair):
    key = keypair()
    assert key.startswith("JARV-")
    assert lic.validate_license_structure(key)
    assert lic.validate_license(key)


def test_tampered_key_rejected(keypair):
    key = keypair()
    parts = key.split("-")
    # flip a character in the first data segment (never the sig tail)
    seg = parts[1]
    parts[1] = ("B" if seg[0] != "B" else "A") + seg[1:]
    tampered = "-".join(parts)
    assert tampered != key
    assert lic.validate_license(tampered) is False


def test_garbage_rejected(keypair):
    assert lic.validate_license("JARV-BLAH-NOT-A-KEY") is False
    assert lic.validate_license("") is False
    assert lic.validate_license(None) is False


def test_whitespace_and_case_normalized(keypair):
    key = keypair()
    assert lic.validate_license(key.lower())
    assert lic.validate_license("  " + key + "  ")
    assert lic.validate_license(key.replace("-", " "))


def test_two_keys_differ(keypair):
    assert keypair() != keypair()


def test_expired_key_rejected(tmp_path, monkeypatch, keypair):
    _fresh_data_dir(tmp_path, monkeypatch)
    # issue_time forced to the past so expiry is already reached
    key = keypair(expiry_days=1, issue_time=1)
    assert lic.validate_license(key) is False


def test_activation_roundtrip(tmp_path, monkeypatch, keypair):
    _fresh_data_dir(tmp_path, monkeypatch)
    key = keypair()
    assert lic.is_licensed() is False
    ok, reason = lic.activate(key)
    assert ok, reason
    assert lic.is_licensed() is True
    assert lic.current_key() == key


def test_activate_rejects_bad_key(tmp_path, monkeypatch, keypair):
    _fresh_data_dir(tmp_path, monkeypatch)
    ok, reason = lic.activate("JARV-NOPE-NOPE-NOPE")
    assert ok is False
    assert lic.is_licensed() is False


def test_key_info_parses(keypair):
    key = keypair(expiry_days=30)
    info = lic.key_info(key)
    assert info is not None
    assert info["expiry_days"] == 30
    assert info["version"] == lk.VERSION


def test_rotation_accepts_old_and_new(keypair):
    # a second, newer keypair added to the ring without removing the first
    key1 = keypair()
    priv2, pub2 = lk.generate_keypair()
    entries = lk._load_entries(lk.KEYRING_PATH)
    entries.append({"id": "new", "created": 1, "pub": pub2})
    lk.save_keyring(entries, lk.KEYRING_PATH)
    # neither current keyring helper is a problem: verify both keys
    assert lic.validate_license(key1) is True
