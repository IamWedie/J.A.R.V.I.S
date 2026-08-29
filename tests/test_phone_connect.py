"""Tests for the automatic phone re-connect logic (core.net.adb_controller)."""
import importlib

from core.net import adb_controller


def test_connect_phone_returns_true_when_already_connected(monkeypatch):
    monkeypatch.setattr(adb_controller, "_is_connected", lambda: True)
    monkeypatch.setattr(adb_controller, "_candidate_hosts", lambda: [])
    assert adb_controller.connect_phone() is True


def test_connect_phone_auto_connects_on_ip_change(monkeypatch):
    calls = []

    def fake_is_connected():
        return False

    def fake_candidate_hosts():
        return [("192.168.2.44", 5555)]

    def fake_connect(host, port, timeout=8):
        calls.append((host, port))
        return True

    monkeypatch.setattr(adb_controller, "_is_connected", fake_is_connected)
    monkeypatch.setattr(adb_controller, "_candidate_hosts", fake_candidate_hosts)
    monkeypatch.setattr(adb_controller, "_connect", fake_connect)

    assert adb_controller.connect_phone() is True
    assert calls == [("192.168.2.44", 5555)]


def test_connect_phone_returns_false_when_unreachable(monkeypatch):
    monkeypatch.setattr(adb_controller, "_is_connected", lambda: False)
    monkeypatch.setattr(adb_controller, "_candidate_hosts", lambda: [("192.168.2.44", 5555)])
    monkeypatch.setattr(adb_controller, "_connect", lambda host, port, timeout=8: False)
    assert adb_controller.connect_phone() is False


def test_candidate_hosts_deduplicates(monkeypatch):
    seen = []
    monkeypatch.setattr(adb_controller, "_mdns_services", lambda: [
        "Honor Phone _adb-tls-connect._tcp.local. 192.168.1.6:39595",
        "",
        "Honor Phone _adb-tls-connect._tcp.local. 192.168.1.6:39595",
    ])
    monkeypatch.setattr(adb_controller, "_phone_devices_from_netdiscovery", lambda: [])
    for host, port in adb_controller._candidate_hosts():
        seen.append((host, port))
    # deduplicated: same host:port appears once
    assert len(seen) == len(set(seen))
    assert ("192.168.1.6", 39595) in seen
