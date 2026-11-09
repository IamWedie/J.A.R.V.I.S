"""Tests for the signed secure-core tool manifest bundle (Task 8).

These tests only read files and inspect metadata: no network traffic, no
sleeping, no touching the user's real Desktop/Documents.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

from core.security.capabilities import CAPABILITY_MIN_RISK, RISK_ORDER
from core.security.manifest import manifest_from_dict, verify_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "core" / "security" / "tool_manifest.json"
SIGNATURE_PATH = REPO_ROOT / "core" / "security" / "tool_manifest.sig"

EXACT_CATALOGUE = frozenset(
    {
        "web_search",
        "launch_app",
        "set_volume",
        "get_volume",
        "media_key",
        "system_info",
        "top_processes",
        "list_running_apps",
        "set_reminder",
        "set_alarm",
        "list_reminders",
        "cancel_reminder",
        "search_files",
        "get_clipboard",
        "set_clipboard",
        "minimize_all_windows",
        "focus_window",
        "type_text",
        "open_url",
        "take_screenshot",
        "ocr_screenshot",
        "describe_screen",
        "remember_fact",
        "forget_fact",
        "recall_memories",
        "morning_briefing",
        "close_app",
        "delete_file",
        "lock_screen",
        "sleep_pc",
        "shutdown_pc",
    }
)

DISABLED_PREFIXES = ("phone_", "cast_", "net_", "telegram_", "wifi_")

HOME_ROOTS = [
    "~/Desktop",
    "~/Documents",
    "~/Downloads",
    "~/Pictures",
    "~/Videos",
    "~/Music",
]

FIXED_ENDPOINT_EGRESS = [
    "duckduckgo.com",
    "html.duckduckgo.com",
    "lite.duckduckgo.com",
    "bing.com",
    "www.bing.com",
    "wttr.in",
]


def _manifest():
    """Parse the committed manifest structurally (no signature check)."""
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return manifest_from_dict(data)


def test_bundle_verifies_with_compiled_key():
    from core.security.tool_pubkeys import TRUSTED_TOOL_KEYS

    manifest = verify_manifest(
        str(MANIFEST_PATH),
        str(SIGNATURE_PATH),
        code_root=str(REPO_ROOT),
        trusted_keys=TRUSTED_TOOL_KEYS,
        frozen=False,
        dev_mode=False,
    )
    assert manifest.verified is True
    assert len(manifest.tools) == 31


def test_bundle_contains_exact_secure_core_catalogue():
    manifest = _manifest()
    assert set(manifest.tools) == EXACT_CATALOGUE
    assert len(manifest.tools) == 31


def test_bundle_excludes_disabled_features():
    manifest = _manifest()
    for name, spec in manifest.tools.items():
        assert not name.startswith(DISABLED_PREFIXES), name
        assert "update" not in name, name
        assert spec.lane != "disabled", name
    features = {
        name: spec.feature for name, spec in manifest.tools.items() if spec.feature
    }
    assert features == {"web_search": "web_search"}


def test_every_tool_risk_covers_its_capabilities():
    manifest = _manifest()
    for spec in manifest.tools.values():
        floor = max(RISK_ORDER[CAPABILITY_MIN_RISK[c]] for c in spec.capabilities)
        assert RISK_ORDER[spec.risk] >= floor, spec.name


def test_scoped_tools_declare_allowlists():
    manifest = _manifest()

    launch = manifest.tools["launch_app"]
    assert launch.app_allowlist and "steam" in launch.app_allowlist
    assert launch.argument_scopes == {"app": ["query"]}

    search = manifest.tools["search_files"]
    assert list(search.filesystem_roots) == HOME_ROOTS
    assert search.argument_scopes == {"filesystem": ["location"]}

    delete = manifest.tools["delete_file"]
    assert list(delete.filesystem_roots) == HOME_ROOTS
    assert delete.argument_scopes == {"filesystem": ["path"]}

    open_url = manifest.tools["open_url"]
    assert open_url.egress_allowlist and "github.com" in open_url.egress_allowlist
    assert open_url.argument_scopes == {"domain": ["url"]}

    web = manifest.tools["web_search"]
    assert list(web.egress_allowlist) == FIXED_ENDPOINT_EGRESS

    briefing = manifest.tools["morning_briefing"]
    assert list(briefing.egress_allowlist) == FIXED_ENDPOINT_EGRESS


def test_assistant_adapters_are_callable():
    from core.tools import assistant_tools

    expected = {
        "set_reminder": ["minutes", "message"],
        "set_alarm": ["time", "message"],
        "list_reminders": [],
        "cancel_reminder": ["keyword"],
        "remember_fact": ["fact"],
        "forget_fact": ["substring"],
        "recall_memories": ["query"],
        "morning_briefing": [],
    }
    for name, params in expected.items():
        func = getattr(assistant_tools, name, None)
        assert callable(func), name
        assert list(inspect.signature(func).parameters) == params, name
