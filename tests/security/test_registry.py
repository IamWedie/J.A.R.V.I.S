"""Tests for the feature- and dependency-filtered tool registry (Task 9)."""
from __future__ import annotations

import os
import shutil
import sys

import pytest

from core.security import canonical  # noqa: F401  (keep cached: test 5 resolves it)
from core.security.features import Features
from core.security.manifest import (
    Manifest,
    ManifestError,
    ToolSpec,
    manifest_from_dict,
)

TOOLMOD_SOURCE = "def echo(value):\n    return value\n"


def _write_pkg(root):
    """Create a real package ``pkg`` with ``toolmod.echo`` under *root*."""
    pkg_dir = root / "pkg"
    pkg_dir.mkdir(exist_ok=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "toolmod.py").write_text(TOOLMOD_SOURCE, encoding="utf-8")


def _spec(
    name,
    handler="pkg.toolmod:echo",
    *,
    description="Echo the input.",
    parameters=None,
    feature=None,
    optional_dependency=None,
    enabled_by_default=True,
):
    return ToolSpec(
        name=name,
        title=name.replace("_", " ").title(),
        description=description,
        handler=handler,
        parameters=(
            {"type": "object", "properties": {}}
            if parameters is None
            else parameters
        ),
        capabilities=(),
        risk="safe",
        lane="core",
        timeout_seconds=10,
        enabled_by_default=enabled_by_default,
        version="1.0.0",
        code_files=(),
        feature=feature,
        optional_dependency=optional_dependency,
    )


def _manifest(*specs):
    return Manifest(
        schema_version=1, tools={spec.name: spec for spec in specs}, verified=True
    )


def _tool_dict(name):
    return {
        "name": name,
        "title": name.title(),
        "description": "Echo the input.",
        "handler": "pkg.toolmod:echo",
        "parameters": {"type": "object", "properties": {}},
        "capabilities": [],
        "risk": "safe",
        "lane": "core",
        "timeout_seconds": 10,
        "enabled_by_default": True,
        "version": "1.0.0",
        "code_files": [],
    }


def _purge_pkg_modules():
    """Drop cached ``pkg`` modules so each test resolves them from its own tmp_path."""
    for key in [k for k in sys.modules if k == "pkg" or k.startswith("pkg.")]:
        del sys.modules[key]


def _present(path):
    """True when *path* (compared by normcase/realpath) is an active sys.path entry."""
    target = os.path.normcase(os.path.realpath(path))
    return any(
        entry and os.path.normcase(os.path.realpath(entry)) == target
        for entry in sys.path
    )


def test_enabled_tool_is_exposed(tmp_path):
    from core.security.registry import ToolRegistry

    _write_pkg(tmp_path)
    spec = _spec("echo")
    orig = list(sys.path)
    _purge_pkg_modules()
    try:
        sys.path.append(str(tmp_path))
        registry = ToolRegistry(_manifest(spec), Features(), str(tmp_path))
        registry.load()
        registry.load()  # idempotent: a second call changes nothing
        assert registry.get("echo") is spec
        assert registry.resolve_handler("echo")("x") == "x"
        assert [tool.name for tool in registry.enabled_tools()] == ["echo"]
        assert registry.disabled_reasons() == {}
    finally:
        sys.path[:] = orig
        _purge_pkg_modules()


def test_disabled_feature_is_filtered(tmp_path):
    from core.security.registry import ToolRegistry

    _write_pkg(tmp_path)
    spec = _spec("webby", feature="web_search")
    orig = list(sys.path)
    _purge_pkg_modules()
    try:
        sys.path.append(str(tmp_path))
        registry = ToolRegistry(
            _manifest(spec), Features(web_search=False), str(tmp_path)
        )
        registry.load()
        assert registry.get("webby") is None
        assert registry.disabled_reasons() == {"webby": "feature_disabled"}
        assert registry.enabled_tools() == []
        # The feature gate runs before resolution: the module is never imported.
        assert "pkg.toolmod" not in sys.modules
    finally:
        sys.path[:] = orig
        _purge_pkg_modules()


def test_missing_optional_dependency_is_filtered(tmp_path):
    from core.security.registry import ToolRegistry

    _write_pkg(tmp_path)
    spec = _spec("dep_tool", optional_dependency="definitely_not_a_real_module_xyz")
    orig = list(sys.path)
    _purge_pkg_modules()
    try:
        sys.path.append(str(tmp_path))
        registry = ToolRegistry(_manifest(spec), Features(), str(tmp_path))
        registry.load()
        assert registry.get("dep_tool") is None
        assert (
            registry.disabled_reasons()
            == {"dep_tool": "missing_optional_dependency"}
        )
        assert registry.enabled_tools() == []
        # The dependency gate runs before resolution: the module is never imported.
        assert "pkg.toolmod" not in sys.modules
    finally:
        sys.path[:] = orig
        _purge_pkg_modules()


def test_unknown_handler_is_filtered(tmp_path):
    from core.security.registry import RegistryError, ToolRegistry

    _write_pkg(tmp_path)
    spec = _spec("probe", handler="pkg.toolmod:missing")
    orig = list(sys.path)
    _purge_pkg_modules()
    try:
        sys.path.append(str(tmp_path))
        registry = ToolRegistry(_manifest(spec), Features(), str(tmp_path))
        registry.load()
        assert registry.get("probe") is None
        assert registry.disabled_reasons() == {"probe": "unknown_handler"}
        with pytest.raises(RegistryError):
            registry.resolve_handler("probe")
    finally:
        sys.path[:] = orig
        _purge_pkg_modules()


def test_handler_outside_code_root_is_rejected(tmp_path):
    from core.security.registry import RegistryError, ToolRegistry

    # A lightweight real repo callable; its module lives outside this
    # test's code_root (no second writable dir is added to sys.path:
    # sanitization would strip it and conflate the two failure classes).
    assert "core.security.canonical" in sys.modules
    spec = _spec("outside", handler="core.security.canonical:canonical_json")
    orig = list(sys.path)
    try:
        registry = ToolRegistry(_manifest(spec), Features(), str(tmp_path))
        registry.load()
        assert registry.disabled_reasons() == {"outside": "handler_outside_code_root"}
        assert registry.get("outside") is None
        with pytest.raises(RegistryError) as excinfo:
            registry.resolve_handler("outside")
        assert str(excinfo.value) == "handler_outside_code_root:outside"
    finally:
        sys.path[:] = orig


def test_openai_schema_shape(tmp_path):
    from core.security.registry import ToolRegistry

    _write_pkg(tmp_path)
    zeta_params = {"type": "object", "properties": {"value": {"type": "string"}}}
    alpha_params = {"type": "object", "properties": {}}
    # Names deliberately out of order: output must be sorted by name.
    zeta = _spec("zeta", description="Zeta echo.", parameters=zeta_params)
    alpha = _spec("alpha", description="Alpha echo.", parameters=alpha_params)
    orig = list(sys.path)
    _purge_pkg_modules()
    try:
        sys.path.append(str(tmp_path))
        registry = ToolRegistry(_manifest(zeta, alpha), Features(), str(tmp_path))
        registry.load()
        schemas = registry.openai_schemas()
        assert schemas == [
            {
                "type": "function",
                "function": {
                    "name": "alpha",
                    "description": "Alpha echo.",
                    "parameters": alpha_params,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "zeta",
                    "description": "Zeta echo.",
                    "parameters": zeta_params,
                },
            },
        ]
        assert [schema["function"]["name"] for schema in schemas] == ["alpha", "zeta"]
        # parameters passed through unchanged (same object, not a copy).
        assert schemas[0]["function"]["parameters"] is alpha.parameters
        assert schemas[1]["function"]["parameters"] is zeta.parameters
        assert schemas[0]["function"]["parameters"] is alpha_params
    finally:
        sys.path[:] = orig
        _purge_pkg_modules()


def test_duplicate_names_are_rejected(tmp_path):
    from core.security.registry import RegistryError, ToolRegistry

    duplicate = _tool_dict("echo")
    data = {"schema_version": 1, "tools": [duplicate, dict(duplicate)]}
    with pytest.raises(ManifestError):
        manifest_from_dict(data)

    # A hand-built manifest whose mapping keys disagree with the spec names
    # is a programmer error the registry refuses at construction time.
    broken = Manifest(
        schema_version=1, tools={"mismatch": _spec("echo")}, verified=False
    )
    with pytest.raises(RegistryError):
        ToolRegistry(broken, Features(), str(tmp_path))


def test_writable_sys_path_entry_is_removed(tmp_path):
    from core.security.registry import ToolRegistry

    # The shadow dir must sit outside code_root (tmp_path) or the keep-prefix
    # rule would keep it; it is created as a sibling in pytest's temp area
    # and removed again in finally, so nothing is left behind.
    shadow_dir = tmp_path.parent / f"{tmp_path.name}_shadow"
    (shadow_dir / "core").mkdir(parents=True)
    (shadow_dir / "core" / "__init__.py").write_text("", encoding="utf-8")

    orig = list(sys.path)
    try:
        sys.path.append(str(tmp_path))
        sys.path.append(str(shadow_dir))
        registry = ToolRegistry(_manifest(), Features(), str(tmp_path))
        registry.load()
        assert not _present(str(shadow_dir)), "writable shadow dir must be dropped"
        assert _present(str(tmp_path)), "code_root must survive sanitization"
    finally:
        sys.path[:] = orig
        shutil.rmtree(shadow_dir, ignore_errors=True)
