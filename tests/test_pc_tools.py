"""Tests for core.tools.pc_tools module.

These tests must be hermetic: they must not open Explorer, toggle Wi-Fi, run a
network speed test, invoke ``shutdown``, or manipulate live windows. OS-facing
calls are monkeypatched; the signed application code is not changed.
"""
import os
import sys
from types import ModuleType, SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_system_info():
    from core.tools.pc_tools import system_info
    info = system_info()
    assert isinstance(info, str)
    assert "RAM" in info or "CPU" in info


def test_list_running_apps():
    from core.tools.pc_tools import list_running_apps
    apps = list_running_apps()
    assert isinstance(apps, str)
    assert len(apps) > 5


def test_get_volume():
    from core.tools.pc_tools import get_volume
    vol = get_volume()
    assert isinstance(vol, (str, int, float))


def test_get_clipboard():
    from core.tools.pc_tools import get_clipboard
    clip = get_clipboard()
    assert isinstance(clip, str)


def test_wifi_status():
    from core.tools.pc_tools import wifi_status
    result = wifi_status()
    assert isinstance(result, str)


def test_wifi_toggle(monkeypatch):
    from core.tools import pc_tools

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pc_tools.subprocess, "run", fake_run)

    result = pc_tools.wifi_toggle("on")

    assert result == "WiFi enabled via Wi-Fi."
    assert calls == [
        ["netsh", "interface", "set", "interface", "Wi-Fi", "admin=enable"]
    ]


def test_wifi_list():
    from core.tools.pc_tools import wifi_list
    result = wifi_list()
    assert isinstance(result, str)


def test_speed_test(monkeypatch):
    from core.tools import pc_tools

    class FakeSpeedtest:
        def __init__(self):
            self.results = SimpleNamespace(ping=12.345)

        def get_best_server(self):
            return {}

        def download(self):
            return 100_000_000

        def upload(self):
            return 50_000_000

    fake_module = ModuleType("speedtest")
    fake_module.Speedtest = FakeSpeedtest
    monkeypatch.setitem(sys.modules, "speedtest", fake_module)

    result = pc_tools.speed_test()

    assert result == (
        "Download: 100.0 Mbps\nUpload: 50.0 Mbps\nPing: 12 ms"
    )


def test_move_file():
    import tempfile, os
    from core.tools.pc_tools import move_file, delete_file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"test")
        src = f.name
    dst = src + ".moved"
    result = move_file(src, dst)
    assert "Moved" in result
    assert os.path.exists(dst)
    delete_file(dst)


def test_copy_file():
    import tempfile, os
    from core.tools.pc_tools import copy_file, delete_file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"test")
        src = f.name
    dst = src + ".copy"
    result = copy_file(src, dst)
    assert "Copied" in result
    assert os.path.exists(dst)
    delete_file(dst)
    delete_file(src)


def test_delete_file():
    import tempfile, os
    from core.tools.pc_tools import delete_file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"test")
        path = f.name
    result = delete_file(path)
    assert "Deleted" in result
    assert not os.path.exists(path)


def test_open_folder(tmp_path, monkeypatch):
    from core.tools import pc_tools

    opened = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)

    result = pc_tools.open_folder(str(tmp_path))

    assert result == f"Opened {tmp_path}"
    assert opened == [str(tmp_path)]


def test_get_brightness():
    from core.tools.pc_tools import get_brightness
    result = get_brightness()
    assert isinstance(result, str)


def test_shutdown_pc(monkeypatch):
    from core.tools import pc_tools

    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace()

    monkeypatch.setattr(pc_tools.subprocess, "Popen", fake_popen)

    result = pc_tools.shutdown_pc("cancel")

    assert result == "PC cancel initiated."
    assert calls == [("shutdown /a", {"shell": True})]


def test_set_wallpaper(tmp_path):
    from core.tools.pc_tools import set_wallpaper
    result = set_wallpaper(str(tmp_path / "nonexistent.png"))
    assert "not found" in result.lower()


def test_maximize_window(monkeypatch):
    from core.tools import pc_tools
    monkeypatch.setattr(pc_tools.gw, "getWindowsWithTitle", lambda title: [])
    result = pc_tools.maximize_window("nonexistent_xyz_window")
    assert result == "No open window matching 'nonexistent_xyz_window'."


def test_snap_window(monkeypatch):
    from core.tools import pc_tools
    monkeypatch.setattr(pc_tools.gw, "getWindowsWithTitle", lambda title: [])
    result = pc_tools.snap_window("nonexistent_xyz_window", "left")
    assert result == "No open window matching 'nonexistent_xyz_window'."


def test_screenshot_window(monkeypatch):
    from core.tools import pc_tools
    monkeypatch.setattr(pc_tools.gw, "getWindowsWithTitle", lambda title: [])
    result = pc_tools.screenshot_window("nonexistent_xyz_window")
    assert result == "No open window matching 'nonexistent_xyz_window'."
