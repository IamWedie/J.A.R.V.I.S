"""Tool manifest model, structural validation, and Ed25519 verification.

A manifest file is ``{"schema_version": 1, "tools": [...]}``.  Its signature is
computed over :func:`core.security.canonical.canonical_json` of the *parsed*
manifest dictionary, so pretty-printing and key order do not affect trust.
Every structural or cryptographic rejection raises :class:`ManifestError`; a
partially trusted manifest is never returned.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import PureWindowsPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.security.authenticode import verify_authenticode
from core.security.canonical import canonical_json
from core.security.capabilities import CAPABILITIES, CAPABILITY_MIN_RISK, RISK_ORDER

__all__ = [
    "CAPABILITIES",
    "CAPABILITY_MIN_RISK",
    "CodeFile",
    "Manifest",
    "ManifestError",
    "ToolSpec",
    "manifest_from_dict",
    "resolve_code_path",
    "sha256_file",
    "validate_relative_path",
    "verify_manifest",
]


class ManifestError(ValueError):
    """Structural or cryptographic rejection of a manifest or its files."""


RISK_VALUES: frozenset[str] = frozenset(RISK_ORDER)
LANE_VALUES: frozenset[str] = frozenset(
    {"core", "restricted", "privileged", "egress", "disabled"}
)

_REQUIRED_TOOL_KEYS = (
    "name",
    "title",
    "description",
    "handler",
    "parameters",
    "capabilities",
    "risk",
    "lane",
    "timeout_seconds",
    "enabled_by_default",
    "version",
    "code_files",
)
_STR_COLLECTION_KEYS = ("filesystem_roots", "egress_allowlist", "app_allowlist")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# module.path.segments:function_name -- identifiers only, no separators,
# no "::", no "<locals>", no "<lambda>".
_HANDLER_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)
_PATH_SEPARATORS = re.compile(r"[\\/]+")


@dataclass(frozen=True)
class CodeFile:
    """One code file a tool loads, with its CRLF-normalized SHA-256."""

    path: str
    sha256: str


@dataclass(frozen=True)
class ToolSpec:
    """A single tool declaration parsed from the manifest."""

    name: str
    title: str
    description: str
    handler: str
    parameters: dict[str, Any]
    capabilities: tuple[str, ...]
    risk: str
    lane: str
    timeout_seconds: float
    enabled_by_default: bool
    version: str
    code_files: tuple[CodeFile, ...]
    filesystem_roots: tuple[str, ...] = ()
    egress_allowlist: tuple[str, ...] = ()
    app_allowlist: tuple[str, ...] = ()
    argument_scopes: Mapping[str, list[str]] = field(default_factory=dict)
    feature: str | None = None
    optional_dependency: str | None = None
    privacy_notes: str = ""


@dataclass(frozen=True)
class Manifest:
    """A parsed manifest plus its verification state."""

    schema_version: int
    tools: Mapping[str, ToolSpec]
    verified: bool


# ---------------------------------------------------------------------------
# Path safety (static checks run before any filesystem access)
# ---------------------------------------------------------------------------


def validate_relative_path(path: Any) -> str:
    """Reject a code-file path that is absolute, escaping, or malformed.

    Purely static: no filesystem access happens here, so unsafe paths are
    refused before anything is opened, hashed, or followed.
    """
    if not isinstance(path, str) or not path:
        raise ManifestError("code file path must be a non-empty string")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        raise ManifestError("code file path contains control characters")
    # Leading "/", "\\", "//", "\\\\", "\\?\\", "\\.\\" all start with a
    # separator; drive paths ("C:...", "C:\\...", UNC) carry a drive part.
    if path.startswith("/") or path.startswith("\\") or os.path.isabs(path):
        raise ManifestError("code file path must be relative")
    windows_path = PureWindowsPath(path)
    if windows_path.drive or windows_path.is_absolute():
        raise ManifestError("code file path must be relative")
    parts = _PATH_SEPARATORS.split(path)
    if ".." in parts:
        raise ManifestError("code file path must not escape the code root")
    return path


def resolve_code_path(code_root: Any, path: Any) -> str:
    """Validate ``path`` and resolve it inside ``code_root`` to a real file.

    Runs the static checks first, then ``realpath``s the candidate (following
    symlinks/junctions) and requires the resolved path to stay under the
    resolved root via ``os.path.commonpath`` -- never a bare ``startswith``.
    """
    validate_relative_path(path)
    root = os.path.normcase(os.path.realpath(os.fspath(code_root)))
    candidate = os.path.normcase(
        os.path.realpath(os.path.join(os.fspath(code_root), path))
    )
    try:
        common = os.path.commonpath([root, candidate])
    except ValueError:
        # Different drives (or one path relative) cannot share a prefix.
        raise ManifestError("code file path escapes the code root") from None
    if common != root:
        raise ManifestError("code file path escapes the code root")
    if not os.path.isfile(candidate):
        raise ManifestError("code file is missing or not a regular file")
    return candidate


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def sha256_file(path: str) -> str:
    """SHA-256 hex digest of a file with CRLF normalized to LF (one read)."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        raise ManifestError("code file is missing or unreadable") from None
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


def _expect_str(value: Any, message: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(message)
    return value


def _str_tuple(value: Any, message: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ManifestError(message)
    for item in value:
        if not isinstance(item, str):
            raise ManifestError(message)
    return tuple(value)


def _parse_capabilities(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ManifestError("tool capabilities must be a list")
    seen: dict[str, None] = {}
    for item in value:
        if not isinstance(item, str):
            raise ManifestError("tool capabilities must be strings")
        if item not in CAPABILITIES:
            raise ManifestError("tool declares an unknown capability")
        seen[item] = None  # de-duplicate, preserving order
    return tuple(seen)


def _parse_code_files(value: Any) -> tuple[CodeFile, ...]:
    if not isinstance(value, list):
        raise ManifestError("tool code_files must be a list")
    files: list[CodeFile] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ManifestError("code file entry must be an object")
        if "path" not in entry:
            raise ManifestError("code file entry is missing its path")
        path = _expect_str(entry["path"], "code file path must be a string")
        validate_relative_path(path)  # static: before any filesystem access
        stored = entry.get("sha256", "")
        if not isinstance(stored, str):
            raise ManifestError("code file sha256 must be a string")
        files.append(CodeFile(path=path, sha256=stored))
    return tuple(files)


def _parse_argument_scopes(value: Any) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ManifestError("tool argument_scopes must be an object")
    scopes: dict[str, list[str]] = {}
    for scope, args in value.items():
        if not isinstance(scope, str) or not isinstance(args, list):
            raise ManifestError("tool argument_scopes must map names to lists")
        for arg in args:
            if not isinstance(arg, str):
                raise ManifestError("tool argument_scopes must hold strings")
        scopes[scope] = list(args)
    return scopes


def _parse_tool(entry: Any) -> ToolSpec:
    if not isinstance(entry, dict):
        raise ManifestError("tool entry must be an object")
    for key in _REQUIRED_TOOL_KEYS:
        if key not in entry:
            raise ManifestError("tool is missing a required field")

    name = _expect_str(entry["name"], "tool name must be a string")
    if not _NAME_RE.match(name):
        raise ManifestError("tool name is not a valid identifier")
    title = _expect_str(entry["title"], "tool title must be a string")
    description = _expect_str(entry["description"], "tool description must be a string")
    handler = _expect_str(entry["handler"], "tool handler must be a string")
    if not _HANDLER_RE.match(handler):
        raise ManifestError("tool handler must be module.path:function_name")
    if not isinstance(entry["parameters"], dict):
        raise ManifestError("tool parameters must be an object")

    capabilities = _parse_capabilities(entry["capabilities"])

    risk = _expect_str(entry["risk"], "tool risk must be a string")
    if risk not in RISK_VALUES:
        raise ManifestError("tool risk is not a known value")
    lane = _expect_str(entry["lane"], "tool lane must be a string")
    if lane not in LANE_VALUES:
        raise ManifestError("tool lane is not a known value")

    timeout = entry["timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ManifestError("tool timeout_seconds must be a number")
    if not 0 < timeout <= 300:
        raise ManifestError("tool timeout_seconds is out of range")

    enabled = entry["enabled_by_default"]
    if not isinstance(enabled, bool):
        raise ManifestError("tool enabled_by_default must be a boolean")
    version = _expect_str(entry["version"], "tool version must be a string")

    # Risk floor: declared risk >= max minimum risk of requested capabilities.
    floor = max(
        (RISK_ORDER[CAPABILITY_MIN_RISK[c]] for c in capabilities), default=0
    )
    if RISK_ORDER[risk] < floor:
        raise ManifestError("tool risk is below its capability minimum")

    code_files = _parse_code_files(entry["code_files"])

    optional: dict[str, Any] = {}
    for key in ("feature", "optional_dependency"):
        value = entry.get(key)
        if value is not None and not isinstance(value, str):
            raise ManifestError(f"tool {key} must be a string or null")
        optional[key] = value
    privacy_notes = entry.get("privacy_notes", "")
    if not isinstance(privacy_notes, str):
        raise ManifestError("tool privacy_notes must be a string")

    collections = {
        key: _str_tuple(entry.get(key, ()), f"tool {key} must be a list of strings")
        for key in _STR_COLLECTION_KEYS
    }

    return ToolSpec(
        name=name,
        title=title,
        description=description,
        handler=handler,
        parameters=dict(entry["parameters"]),
        capabilities=capabilities,
        risk=risk,
        lane=lane,
        timeout_seconds=timeout,
        enabled_by_default=enabled,
        version=version,
        code_files=code_files,
        filesystem_roots=collections["filesystem_roots"],
        egress_allowlist=collections["egress_allowlist"],
        app_allowlist=collections["app_allowlist"],
        argument_scopes=_parse_argument_scopes(entry.get("argument_scopes")),
        feature=optional["feature"],
        optional_dependency=optional["optional_dependency"],
        privacy_notes=privacy_notes,
    )


def manifest_from_dict(data: Any) -> Manifest:
    """Validate manifest structure only (no signature, no file hashing)."""
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be an object")
    schema_version = data.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ManifestError("manifest schema_version must be an integer")
    if schema_version != 1:
        raise ManifestError("unsupported manifest schema version")
    tools_list = data.get("tools")
    if not isinstance(tools_list, list):
        raise ManifestError("manifest tools must be a list")

    tools: dict[str, ToolSpec] = {}
    for entry in tools_list:
        spec = _parse_tool(entry)
        if spec.name in tools:
            raise ManifestError("manifest declares a duplicate tool name")
        tools[spec.name] = spec
    return Manifest(schema_version=schema_version, tools=tools, verified=False)


# ---------------------------------------------------------------------------
# Signature and file verification
# ---------------------------------------------------------------------------


def _load_json_object(path: str, label: str) -> Any:
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        raise ManifestError(f"{label} file is missing") from None
    try:
        data = json.loads(raw)
    except ValueError:
        raise ManifestError(f"{label} file is not valid JSON") from None
    if not isinstance(data, dict):
        raise ManifestError(f"{label} file must be an object")
    return data


def _verify_signature(
    data: Mapping[str, Any], signature_path: str, trusted_keys: Mapping[str, bytes]
) -> None:
    payload = _load_json_object(signature_path, "signature")
    key_id = payload.get("key_id")
    signature_b64 = payload.get("signature")
    if not isinstance(key_id, str) or not isinstance(signature_b64, str):
        raise ManifestError("signature file is malformed")
    if not isinstance(trusted_keys, Mapping):
        raise ManifestError("trusted keyring is not a mapping")
    if key_id not in trusted_keys:
        raise ManifestError("signature uses an unknown key id")
    try:
        signature_bytes = base64.b64decode(signature_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ManifestError("signature is not valid base64") from None
    if len(signature_bytes) != 64:
        raise ManifestError("signature has the wrong length")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(trusted_keys[key_id])
    except (TypeError, ValueError):
        raise ManifestError("trusted key is not a valid Ed25519 key") from None
    try:
        message = canonical_json(dict(data))
    except (TypeError, ValueError, UnicodeError):
        raise ManifestError("manifest cannot be canonicalized") from None
    try:
        public_key.verify(signature_bytes, message)
    except InvalidSignature:
        raise ManifestError("manifest signature verification failed") from None


def verify_manifest(
    manifest_path: str,
    signature_path: str,
    code_root: str,
    trusted_keys: Mapping[str, bytes],
    *,
    frozen: bool = False,
    dev_mode: bool = False,
    executable_path: str | None = None,
) -> Manifest:
    """Verify a manifest's signature and (in source mode) its code files.

    Returns a manifest with ``verified=True`` only after the signature checks
    out and every code-file hash matches (source mode) or Authenticode passes
    (frozen mode).  An unsigned/invalid signature is tolerated only when
    ``dev_mode`` is set and ``frozen`` is not, and then the result carries
    ``verified=False`` with code-hash enforcement skipped.  Frozen builds
    reject unsigned manifests regardless of ``dev_mode``.
    """
    data = _load_json_object(manifest_path, "manifest")
    manifest = manifest_from_dict(data)  # structural validation, always applied

    try:
        _verify_signature(data, signature_path, trusted_keys)
    except ManifestError:
        if frozen or not dev_mode:
            raise
        return manifest  # dev fallback: verified=False, hashes not enforced

    if frozen:
        target = executable_path or sys.executable
        if verify_authenticode(target) is not True:
            raise ManifestError("executable Authenticode verification failed")
        return replace(manifest, verified=True)

    for spec in manifest.tools.values():
        for code_file in spec.code_files:
            resolved = resolve_code_path(code_root, code_file.path)
            if sha256_file(resolved) != code_file.sha256:
                raise ManifestError("code file hash does not match the manifest")
    return replace(manifest, verified=True)
