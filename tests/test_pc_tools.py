"""Tests for core.tools.pc_tools module."""
import sys
import os
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
