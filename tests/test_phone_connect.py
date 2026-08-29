"""Tests for the strict VPN-only phone connection logic (core.net.adb_controller).

JARVIS connects ONLY to the configured PHONE_ADDR (private VPN address) and never
scans the LAN, so wireless debugging is never exposed on public networks.
"""
from core import config
from core.net import adb_controller


def test_connect_phone_returns_false_without_addr(monkeypatch):
    monkeypatch.setattr(config, "PHONE_ADDR", "")
    monkeypatch.setattr(adb_controller, "_is_connected", lambda: False)
    monkeypatch.setattr(adb_controller, "_connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("_connect must not be called when no addr")))
    assert adb_controller.connect_phone() is False


def test_connect_phone_true_when_already_connected(monkeypatch):
    monkeypatch.setattr(config, "PHONE_ADDR", "100.100.1.5")
    monkeypatch.setattr(adb_controller, "_is_connected", lambda: True)
    assert adb_controller.connect_phone() is True


def test_connect_phone_connects_to_addr_and_verifies_serial(monkeypatch):
    monkeypatch.setattr(config, "PHONE_ADDR", "100.100.1.5")
    monkeypatch.setattr(config, "PHONE_PORT", 5555)
    monkeypatch.setattr(config, "PHONE_SERIAL", "ABC123")
    monkeypatch.setattr(adb_controller, "_is_connected", lambda: False)
    monkeypatch.setattr(adb_controller, "_connect", lambda host, port, timeout=8: True)
    monkeypatch.setattr(adb_controller, "_verify_serial", lambda: True)
    assert adb_controller.connect_phone() is True


def test_connect_phone_refuses_on_serial_mismatch(monkeypatch):
    monkeypatch.setattr(config, "PHONE_ADDR", "100.100.1.5")
    monkeypatch.setattr(config, "PHONE_PORT", 5555)
    monkeypatch.setattr(adb_controller, "_is_connected", lambda: False)
    monkeypatch.setattr(adb_controller, "_connect", lambda host, port, timeout=8: True)
    monkeypatch.setattr(adb_controller, "_verify_serial", lambda: False)
    disconnected = []
    monkeypatch.setattr(adb_controller, "_disconnect", lambda host, port: disconnected.append((host, port)))
    assert adb_controller.connect_phone() is False
    assert disconnected == [("100.100.1.5", 5555)]


def test_connect_phone_false_when_connect_fails(monkeypatch):
    monkeypatch.setattr(config, "PHONE_ADDR", "100.100.1.5")
    monkeypatch.setattr(config, "PHONE_PORT", 5555)
    monkeypatch.setattr(adb_controller, "_is_connected", lambda: False)
    monkeypatch.setattr(adb_controller, "_connect", lambda host, port, timeout=8: False)
    assert adb_controller.connect_phone() is False


def test_verify_serial_matches(monkeypatch):
    monkeypatch.setattr(config, "PHONE_SERIAL", "ABC123")
    monkeypatch.setattr(adb_controller, "_shell", lambda cmd, timeout=15: "ABC123")
    assert adb_controller._verify_serial() is True


def test_verify_serial_mismatch(monkeypatch):
    monkeypatch.setattr(config, "PHONE_SERIAL", "ABC123")
    monkeypatch.setattr(adb_controller, "_shell", lambda cmd, timeout=15: "DIFFERENT")
    assert adb_controller._verify_serial() is False


def test_verify_serial_skipped_when_unset(monkeypatch):
    monkeypatch.setattr(config, "PHONE_SERIAL", "")
    assert adb_controller._verify_serial() is True
