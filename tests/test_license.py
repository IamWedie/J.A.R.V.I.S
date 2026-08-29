"""Tests for the license-key system (core.license)."""
import pytest

import core.config as config
from core import license as lic


@pytest.fixture(autouse=True)
def _secret():
    # tests need a stable secret; license module reads config.LICENSE_SECRET live
    config.LICENSE_SECRET = "test-secret-for-license-tests"
    yield
    config.LICENSE_SECRET = ""


def _fresh_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_DIR", str(tmp_path))
    return tmp_path


def test_generate_produces_valid_key(_secret):
    key = lic.generate_license()
    assert key.startswith("JARV-")
    assert lic.validate_license_structure(key)
    assert lic.validate_license(key)


def test_tampered_key_rejected(_secret):
    key = lic.generate_license()
    # flip one char in the body
    parts = key.split("-")
    parts[1] = ("B" if parts[1][0] != "B" else "A") + parts[1][1:]
    tampered = "-".join(parts)
    assert tampered != key
    assert lic.validate_license(tampered) is False


def test_typo_check_digit_rejected(_secret):
    key = lic.generate_license()
    # flipping check or body char should fail at structure parse
    parts = key.split("-")
    parts[1] = ("B" if parts[1][0] != "B" else "A") + parts[1][1:]
    tampered = "-".join(parts)
    assert lic.validate_license_structure(tampered) in (False,)
    assert lic.validate_license(tampered) is False


def test_garbage_rejected(_secret):
    assert lic.validate_license("JARV-BLAH-NOT-A-KEY") is False
    assert lic.validate_license("") is False
    assert lic.validate_license(None) is False


def test_whitespace_and_case_normalized(_secret):
    key = lic.generate_license()
    assert lic.validate_license(key.lower())
    assert lic.validate_license("  " + key + "  ")
    assert lic.validate_license(key.replace("-", " "))


def test_two_keys_differ(_secret):
    assert lic.generate_license() != lic.generate_license()


def test_activation_roundtrip(tmp_path, monkeypatch, _secret):
    _fresh_state(tmp_path, monkeypatch)
    key = lic.generate_license()
    assert lic.is_licensed() is False
    ok, reason = lic.activate(key)
    assert ok, reason
    assert lic.is_licensed() is True
    assert lic.current_key() == key


def test_activate_rejects_bad_key(tmp_path, monkeypatch, _secret):
    _fresh_state(tmp_path, monkeypatch)
    ok, reason = lic.activate("JARV-NOPE-NOPE-NOPE")
    assert ok is False
    assert lic.is_licensed() is False


def test_activate_requires_secret(tmp_path, monkeypatch):
    _fresh_state(tmp_path, monkeypatch)
    config.LICENSE_SECRET = ""
    ok, _ = lic.activate("JARV-XXXXX-XXXXX-XXXXX")
    assert ok is False
