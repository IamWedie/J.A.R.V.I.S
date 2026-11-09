"""Task 10: capability risk aggregation and canonical scope matching.

All 16 tests are offline: URL checks use IP literals or ``localhost`` only,
filesystem assertions stay inside ``tmp_path``, and no test ever starts a
process or opens a shortcut. The junction test skips (with the reason) only
when Windows refuses ``mklink /J``; it never passes silently.
"""
from __future__ import annotations

import inspect
import os
import socket
import subprocess
from pathlib import Path

import pytest

from core.security.scopes import domain_allowed, inspect_url, resolve_within_roots
from core.security.capabilities import ScopeError, capability_risk
from core.security.apps import ResolvedApp, app_allowed, resolve_app


def make_junction(link, target):
    """Create a directory junction; skip explicitly when Windows refuses."""
    try:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
        )
    except OSError:
        pytest.skip("junction creation unavailable")
    if result.returncode != 0:
        pytest.skip("junction creation unavailable")


def test_path_inside_root_is_allowed(tmp_path):
    target = tmp_path / "docs" / "note.txt"
    target.parent.mkdir()
    target.write_text("content", encoding="utf-8")
    resolved = resolve_within_roots(str(target), [str(tmp_path)])
    assert resolved == target.resolve()
    assert resolved.is_absolute()


def test_dotdot_is_rejected(tmp_path):
    raw = str(tmp_path / "sub" / ".." / "note.txt")
    with pytest.raises(ScopeError) as excinfo:
        resolve_within_roots(raw, [str(tmp_path)])
    assert str(excinfo.value) == "dotdot"
    # Forward slashes and relative forms hit the same component rule.
    with pytest.raises(ScopeError) as excinfo:
        resolve_within_roots(str(tmp_path) + "/../note.txt", [str(tmp_path)])
    assert str(excinfo.value) == "dotdot"
    with pytest.raises(ScopeError) as excinfo:
        resolve_within_roots("../note.txt", [str(tmp_path)])
    assert str(excinfo.value) == "dotdot"
    # The exception never echoes the raw path.
    assert str(tmp_path) not in str(excinfo.value)
    # NTFS ADS streams and trailing dot/space components are rejected
    # before resolution.  ``Path`` may normalize some forms, so both the
    # joined form and the literal string are exercised.
    root = tmp_path
    for raw in (
        str(root / "...\\secret"),
        str(root / ".. \\secret"),
        str(root / "file.txt:stream"),
        str(root) + "\\...\\secret",
        str(root) + "\\.. \\secret",
        str(root) + "\\file.txt:stream",
    ):
        with pytest.raises(ScopeError) as excinfo:
            resolve_within_roots(raw, [str(root)])
        assert str(root) not in str(excinfo.value)


def test_unc_and_device_paths_are_rejected():
    roots = [r"C:\allowed"]
    for raw in (
        r"\\server\share\file.txt",
        r"\\?\C:\file.txt",
        r"\\.\COM1",
        "//server/share/file.txt",
        r"\Windows\system32\file.txt",
        "/etc/passwd",
    ):
        with pytest.raises(ScopeError) as excinfo:
            resolve_within_roots(raw, roots)
        assert str(excinfo.value) == "unc_path", raw
        assert "server" not in str(excinfo.value)


def test_drive_relative_path_is_rejected():
    for raw in (r"C:notes\file.txt", "C:", r"c:relative", "1:foo"):
        with pytest.raises(ScopeError) as excinfo:
            resolve_within_roots(raw, [r"C:\allowed"])
        assert str(excinfo.value) == "drive_relative", raw


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive roots are a Windows concern")
def test_case_insensitive_root_match(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    root = str(tmp_path)
    # Vary the case of the final directory segment of the root.
    varied = root[: len(root) - len(tmp_path.name)] + tmp_path.name.upper()
    assert resolve_within_roots(str(target), [varied]) == target.resolve()
    # Vary the case of the whole root, drive letter included.
    assert resolve_within_roots(str(target), [root.upper()]) == target.resolve()


def test_short_name_alias_is_rejected(tmp_path):
    # Pure string rejection: no alias ever has to be created on disk.
    raw = str(tmp_path) + r"\PROGRA~1\file.txt"
    with pytest.raises(ScopeError) as excinfo:
        resolve_within_roots(raw, [str(tmp_path)])
    assert str(excinfo.value) == "short_name"
    # The exception never echoes the raw path.
    assert str(tmp_path) not in str(excinfo.value)
    with pytest.raises(ScopeError) as excinfo:
        resolve_within_roots(r"C:\Users\ADMINI~1\file.txt", [r"C:\Users"])
    assert str(excinfo.value) == "short_name"


def test_junction_escape_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    link = root / "link"
    make_junction(link, outside)
    with pytest.raises(ScopeError) as excinfo:
        resolve_within_roots(str(link / "secret.txt"), [str(root)])
    # Rejected without leaking the raw path in the message.
    assert str(tmp_path) not in str(excinfo.value)


def test_domain_exact_and_wildcard_matching():
    assert domain_allowed("a.example.com", ["*.example.com"]) is True
    assert domain_allowed("A.Example.COM.", ["*.example.com"]) is True
    assert domain_allowed("example.com", ["example.com"]) is True
    assert domain_allowed("EXAMPLE.com.", ["example.com"]) is True
    # A wildcard matches exactly one extra label, never the bare suffix.
    assert domain_allowed("example.com", ["*.example.com"]) is False
    assert domain_allowed("a.b.example.com", ["*.example.com"]) is False
    # An exact pattern never matches a subdomain.
    assert domain_allowed("api.example.com", ["example.com"]) is False
    assert domain_allowed("example.com", []) is False


def test_domain_lookalike_and_trailing_dot_rejected():
    assert domain_allowed("evil-example.com", ["example.com"]) is False
    assert domain_allowed("example.com.evil.net", ["example.com"]) is False
    # Wildcard TLDs and bare stars never match anything.
    assert domain_allowed("example.com", ["*.com"]) is False
    assert domain_allowed("sub.example.com", ["*.com"]) is False
    assert domain_allowed("anything.example.com", ["*"]) is False
    # Schemes, paths, and ports inside a pattern never match.
    assert domain_allowed("example.com", ["https://example.com"]) is False
    assert domain_allowed("example.com", ["example.com/login"]) is False
    assert domain_allowed("example.com", ["example.com:8443"]) is False
    # More than one trailing dot is rejected on both sides.
    assert domain_allowed("example.com..", ["example.com"]) is False
    assert domain_allowed("example.com", ["example.com.."]) is False
    assert domain_allowed("", ["example.com"]) is False


def test_domain_ports_are_checked():
    allow = ["93.184.216.34"]
    assert inspect_url("http://93.184.216.34/", allow).allowlisted is True
    assert inspect_url("http://93.184.216.34/", allow).host == "93.184.216.34"
    assert inspect_url("http://93.184.216.34:80/", allow).allowlisted is True
    assert inspect_url("https://93.184.216.34:443/", allow).allowlisted is True
    # Ports other than 80/443 are rejected outright.
    blocked = inspect_url("http://93.184.216.34:8080/", allow)
    assert blocked.allowlisted is False
    assert blocked.host == ""
    # Out-of-range ports raise inside urlsplit and are rejected.
    assert inspect_url("http://93.184.216.34:99999/", allow).allowlisted is False
    # Only http/https are ever considered.
    assert inspect_url("ftp://93.184.216.34/", allow).allowlisted is False
    assert inspect_url("file:///C:/Windows/system32/calc.exe", allow).allowlisted is False


def test_url_userinfo_and_private_addresses_rejected():
    userinfo = inspect_url("http://user:pass@example.com/", ["example.com"])
    assert userinfo.allowlisted is False
    assert userinfo.host == ""
    private = ["127.0.0.1", "10.0.0.1", "169.254.1.1", "::1", "localhost"]
    for url in (
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.1.1/",
        "http://[::1]/",
        "http://localhost/",
    ):
        check = inspect_url(url, private)
        assert check.allowlisted is False, url
        assert check.host == "", url
    # Untrusted input never raises.
    for garbage in ("", "not a url", "http://", "http://[::1", None, 42):
        assert inspect_url(garbage, ["example.com"]).allowlisted is False
    # A DNS failure is a rejection, exercised with the resolver stubbed out
    # so no packet ever leaves the machine.
    original = socket.getaddrinfo

    def _fail(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    socket.getaddrinfo = _fail
    try:
        check = inspect_url("http://offline-host.example/", ["offline-host.example"])
    finally:
        socket.getaddrinfo = original
    assert check.allowlisted is False
    assert check.host == ""


def test_resolved_start_menu_shortcut_is_allowed(tmp_path):
    start_menu = tmp_path / "start_menu"
    start_menu.mkdir()
    shortcut = start_menu / "Notepad.lnk"
    shortcut.write_text("", encoding="utf-8")
    resolved = resolve_app("notepad", [str(start_menu)])
    assert resolved is not None
    assert isinstance(resolved, ResolvedApp)
    assert resolved.display_name == "Notepad"
    assert resolved.kind == "shortcut"
    assert Path(resolved.target).is_absolute()
    assert Path(resolved.target).resolve() == shortcut.resolve()
    # Normalization tolerates case and surrounding whitespace, nothing else.
    assert resolve_app("NOTEPAD", [str(start_menu)]) is not None
    assert resolve_app("  notepad  ", [str(start_menu)]) is not None
    assert app_allowed(resolved, ["notepad"], [str(start_menu)]) is True


def test_display_name_without_resolved_target_is_rejected(tmp_path):
    start_menu = tmp_path / "start_menu"
    start_menu.mkdir()
    name_only = ResolvedApp(display_name="notepad", target="", kind="shortcut")
    assert app_allowed(name_only, ["notepad"], [str(start_menu)]) is False
    ghost = ResolvedApp(
        display_name="notepad",
        target=str(tmp_path / "ghost.lnk"),
        kind="shortcut",
    )
    assert app_allowed(ghost, ["notepad"], [str(start_menu)]) is False
    not_a_shortcut = tmp_path / "Notepad.txt"
    not_a_shortcut.write_text("", encoding="utf-8")
    wrong_suffix = ResolvedApp(
        display_name="notepad", target=str(not_a_shortcut), kind="shortcut"
    )
    assert app_allowed(wrong_suffix, ["notepad"], [str(start_menu)]) is False
    # A resolvable shortcut still fails when its name is not allowlisted.
    shortcut = start_menu / "Notepad.lnk"
    shortcut.write_text("", encoding="utf-8")
    resolved = resolve_app("notepad", [str(start_menu)])
    assert app_allowed(resolved, ["calculator"], [str(start_menu)]) is False


def test_shortcut_outside_start_menu_is_rejected(tmp_path):
    start_menu = tmp_path / "start_menu"
    start_menu.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_lnk = outside / "Notepad.lnk"
    outside_lnk.write_text("", encoding="utf-8")
    # The scanner never walks outside the trusted roots.
    assert resolve_app("notepad", [str(start_menu)]) is None
    # A forged ResolvedApp naming an allowlisted app fails containment.
    forged = ResolvedApp(display_name="notepad", target=str(outside_lnk), kind="shortcut")
    assert app_allowed(forged, ["notepad"], [str(start_menu)]) is False


def test_fuzzy_app_match_is_not_used(tmp_path):
    start_menu = tmp_path / "start_menu"
    start_menu.mkdir()
    (start_menu / "Notepad.lnk").write_text("", encoding="utf-8")
    # Prefix, suffix, and token matches are all gone; only exact survives.
    assert resolve_app("note", [str(start_menu)]) is None
    assert resolve_app("pad", [str(start_menu)]) is None
    assert resolve_app("notepad app", [str(start_menu)]) is None
    assert resolve_app("", [str(start_menu)]) is None
    assert resolve_app("notepad", [str(start_menu)]) is not None
    from core.tools import pc_tools

    assert not hasattr(pc_tools, "APP_ALIASES")
    assert not hasattr(pc_tools, "_index_start_menu")
    assert not hasattr(pc_tools, "START_MENU_DIRS")
    source = inspect.getsource(pc_tools.launch_app)
    assert "resolve_app" in source
    assert "os.startfile" in source
    assert "cmd" not in source
    assert "shell" not in source
    assert "Popen" not in source
    # Missing apps and raw paths are reported, never launched.
    message = pc_tools.launch_app("definitely-no-such-app-xyz")
    assert "No installed app found" in message
    path_message = pc_tools.launch_app(str(tmp_path))
    assert "No installed app found" in path_message


def test_capability_risk_is_the_maximum():
    assert capability_risk([]) == "safe"
    assert capability_risk(["system.read"]) == "safe"
    assert capability_risk(["system.control", "filesystem.read"]) == "guarded"
    assert capability_risk(["filesystem.write", "system.control"]) == "sensitive"
    assert capability_risk(["audit.read", "media.control"]) == "sensitive"
    assert capability_risk(["phone.comms", "system.read"]) == "critical"
    with pytest.raises(ScopeError) as excinfo:
        capability_risk(["system.read", "not.a.capability"])
    assert str(excinfo.value) == "unknown_capability:not.a.capability"
    # Malformed input fails closed instead of aggregating to "safe".
    with pytest.raises(ScopeError) as excinfo:
        capability_risk(None)
    assert str(excinfo.value) == "unknown_capability:None"
    with pytest.raises(ScopeError) as excinfo:
        capability_risk("system.read")
    assert str(excinfo.value) == "invalid_capabilities"
    # Both import paths name the very same exception class.
    from core.security.scopes import ScopeError as ReExportedScopeError

    assert ReExportedScopeError is ScopeError
