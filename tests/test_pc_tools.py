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


def test_wifi_status():
    from core.tools.pc_tools import wifi_status
    result = wifi_status()
    assert isinstance(result, str)


def test_wifi_toggle():
    from core.tools.pc_tools import wifi_toggle
    result = wifi_toggle("on")
    assert isinstance(result, str)


def test_wifi_list():
    from core.tools.pc_tools import wifi_list
    result = wifi_list()
    assert isinstance(result, str)


def test_speed_test():
    from core.tools.pc_tools import speed_test
    result = speed_test()
    assert isinstance(result, str)


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


def test_open_folder():
    from core.tools.pc_tools import open_folder
    result = open_folder(os.path.expanduser("~/"))
    assert "Opened" in result


def test_get_brightness():
    from core.tools.pc_tools import get_brightness
    result = get_brightness()
    assert isinstance(result, str)


def test_shutdown_pc():
    from core.tools.pc_tools import shutdown_pc
    result = shutdown_pc("cancel")
    assert isinstance(result, str)


def test_set_wallpaper():
    from core.tools.pc_tools import set_wallpaper
    result = set_wallpaper("~/nonexistent.png")
    assert "not found" in result.lower() or "failed" in result.lower()


def test_maximize_window():
    from core.tools.pc_tools import maximize_window
    result = maximize_window("nonexistent_xyz_window")
    assert "No open window" in result


def test_snap_window():
    from core.tools.pc_tools import snap_window
    result = snap_window("nonexistent_xyz_window", "left")
    assert "No open window" in result


def test_screenshot_window():
    from core.tools.pc_tools import screenshot_window
    result = screenshot_window("nonexistent_xyz_window")
    assert "No open window" in result
