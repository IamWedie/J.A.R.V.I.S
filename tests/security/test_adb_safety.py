import pytest

from core.net.adb_safe import (
    build_shell_command, quote_shell_arg, safe_package,
    safe_phone_number, safe_remote_path,
)

def test_safe_package_accepts_real_packages():
    assert safe_package("com.spotify.music") == "com.spotify.music"

@pytest.mark.parametrize("bad", [
    "com.app; rm -rf /", "com.app && reboot", "com.app|id",
    "../../data", "com.app`id`", "",
])
def test_safe_package_rejects_injection(bad):
    with pytest.raises(ValueError):
        safe_package(bad)

def test_safe_phone_number_rejects_injection():
    assert safe_phone_number("+21612345678") == "+21612345678"
    for bad in ("123; reboot", "123 456", "12", "1234567890123456"):
        with pytest.raises(ValueError):
            safe_phone_number(bad)

def test_safe_remote_path_rejects_traversal_and_metacharacters():
    assert safe_remote_path("/sdcard/Download/notes.txt") == "/sdcard/Download/notes.txt"
    for bad in ("/sdcard/../data", "/sdcard/a;rm", "C:/Users/x", "/sdcard/'x'"):
        with pytest.raises(ValueError):
            safe_remote_path(bad)

def test_shell_arguments_are_quoted():
    cmd = build_shell_command(["input", "text", "hello; reboot"])
    assert ";" not in cmd.replace("'hello; reboot'", "")
    assert quote_shell_arg("a b") == "'a b'"

def test_shell_command_has_no_shell_true(monkeypatch):
    from core.net import adb_controller
    calls = []
    monkeypatch.setattr(adb_controller, "_run", lambda args, timeout=15: calls.append(args) or "ok")
    adb_controller.open_app("com.spotify.music")
    assert calls
    assert calls[0][0] == "shell"
    assert "com.spotify.music" in calls[0][1]
    assert ";" not in calls[0][1]
    assert not hasattr(adb_controller, "_shell_raw")
    with pytest.raises(ValueError):
        adb_controller.open_app("com.spotify.music; reboot")
