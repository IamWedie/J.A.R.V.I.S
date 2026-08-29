"""Tests for PIN security hardening (strong PIN + brute-force lockout).

Scoped to the public-release requirement: the remote-control approval PIN must
not be trivially guessable and must resist brute-force probing.
"""
import time

import core.config as config
from core import approval


def _reset():
    approval._pin_attempts.clear()
    approval.cancel_all()


def test_valid_pins():
    _reset()
    for pin in ("s3cure!Pass", "Jarvis-2026", "K9p4!zq7"):
        ok, reason = config.validate_pin(pin)
        assert ok, (pin, reason)


def test_weak_short_pin_rejected():
    _reset()
    ok, reason = config.validate_pin("1234")
    assert ok is False
    assert "short" in reason.lower() or "common" in reason.lower()


def test_common_default_pin_rejected():
    _reset()
    for pin in ("0910", "0000", "123456", "1111", "2580"):
        ok, _ = config.validate_pin(pin)
        assert ok is False, pin


def test_empty_pin_rejected():
    _reset()
    ok, _ = config.validate_pin("")
    assert ok is False


def test_lockout_after_max_attempts():
    _reset()
    max_attempts = config.PIN_MAX_ATTEMPTS
    # wrong PINs eat through attempts
    for _ in range(max_attempts):
        approval.resolve_by_pin("wrong", source_id="chat1")
    status = approval.pin_lockout_status("chat1")
    assert status["locked"] is True
    assert status["remaining"] > 0
    # while locked, even the correct PIN is refused
    ok, _ = approval.resolve_by_pin(config.JARVIS_PIN, source_id="chat1")
    assert ok is False


def test_lockout_does_not_affect_other_source():
    _reset()
    for _ in range(config.PIN_MAX_ATTEMPTS):
        approval.resolve_by_pin("wrong", source_id="chat1")
    assert approval.pin_lockout_status("chat1")["locked"] is True
    # a different source is unaffected
    status = approval.pin_lockout_status("chat2")
    assert status["locked"] is False
    assert status["attempts_left"] == config.PIN_MAX_ATTEMPTS


def test_success_resets_lockout():
    _reset()
    for _ in range(config.PIN_MAX_ATTEMPTS - 1):
        approval.resolve_by_pin("wrong", source_id="chat1")
    # correct PIN succeeds and clears the failure counter
    approval.create_request("shutdown_pc", "Shutdown", "telegram")
    ok, _ = approval.resolve_by_pin(config.JARVIS_PIN, source_id="chat1")
    assert ok is True
    status = approval.pin_lockout_status("chat1")
    assert status["locked"] is False
    assert status["attempts_left"] == config.PIN_MAX_ATTEMPTS
