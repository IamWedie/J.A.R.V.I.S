"""Tests for the signed tool manifest format, Ed25519 verification, and signing."""
from __future__ import annotations

import json
import os

import pytest
from cryptography.hazmat.primitives import serialization

from core.security.canonical import canonical_json
from core.security.manifest import (
    ManifestError,
    manifest_from_dict,
    sha256_file,
    verify_manifest,
)
from core.security.authenticode import verify_authenticode
from scripts import rotate_tool_key
from scripts.sign_tool_manifest import sign_manifest


def _generate_key(tmp_path):
    """Create an Ed25519 keypair via rotate_tool_key; return (pem path, raw public key)."""
    key_path = tmp_path / "tool_priv.pem"
    assert rotate_tool_key.main(["--private-out", str(key_path)]) == 0
    private = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    raw_public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    assert len(raw_public) == 32
    return key_path, raw_public


def _manifest_data(code_path="tools/get_volume.py"):
    """A small but complete manifest: one tool, one code file."""
    return {
        "schema_version": 1,
        "tools": [
            {
                "name": "get_volume",
                "title": "Get volume",
                "description": "Read the current output volume.",
                "handler": "core.tools.pc_tools:get_volume",
                "parameters": {"type": "object", "properties": {}},
                "capabilities": ["system.read"],
                "risk": "safe",
                "lane": "restricted",
                "timeout_seconds": 10,
                "enabled_by_default": True,
                "version": "1.0.0",
                "code_files": [{"path": code_path, "sha256": ""}],
            }
        ],
    }


def _write_manifest(tmp_path, data, name="manifest.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _write_code_file(tmp_path, code_path="tools/get_volume.py", content="print('volume')\n"):
    path = tmp_path / code_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_canonical_json_is_order_independent():
    first = {"b": 2, "a": {"y": [1, 2], "x": 1}}
    second = {"a": {"x": 1, "y": [1, 2]}, "b": 2}
    assert canonical_json(first) == canonical_json(second)
    assert isinstance(canonical_json(first), bytes)


def test_signed_manifest_verifies(tmp_path):
    key_path, raw_public = _generate_key(tmp_path)
    code_file = _write_code_file(tmp_path)
    manifest_path = _write_manifest(tmp_path, _manifest_data())
    signature_path = tmp_path / "manifest.sig"

    sign_manifest(
        str(manifest_path), str(signature_path), str(key_path), "key-main", str(tmp_path)
    )

    manifest = verify_manifest(
        str(manifest_path), str(signature_path), str(tmp_path), {"key-main": raw_public}
    )
    assert manifest.verified is True
    assert len(manifest.tools) == 1
    tool = manifest.tools["get_volume"]
    assert tool.handler == "core.tools.pc_tools:get_volume"
    assert tool.capabilities == ("system.read",)
    assert tool.risk == "safe"
    assert tool.lane == "restricted"
    # The signer filled the stored hash with the CRLF-normalized digest.
    stored = json.loads(manifest_path.read_text(encoding="utf-8"))["tools"][0]["code_files"][0]
    assert stored["sha256"] == sha256_file(str(code_file))


def test_tampered_manifest_is_rejected(tmp_path):
    key_path, raw_public = _generate_key(tmp_path)
    _write_code_file(tmp_path)
    manifest_path = _write_manifest(tmp_path, _manifest_data())
    signature_path = tmp_path / "manifest.sig"
    sign_manifest(
        str(manifest_path), str(signature_path), str(key_path), "key-main", str(tmp_path)
    )

    # Semantic change, re-serialized without re-signing.
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["tools"][0]["title"] = "Tampered volume reader"
    manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(ManifestError):
        verify_manifest(
            str(manifest_path), str(signature_path), str(tmp_path), {"key-main": raw_public}
        )


def test_tampered_code_file_is_rejected(tmp_path):
    key_path, raw_public = _generate_key(tmp_path)
    _write_code_file(tmp_path)
    manifest_path = _write_manifest(tmp_path, _manifest_data())
    signature_path = tmp_path / "manifest.sig"
    sign_manifest(
        str(manifest_path), str(signature_path), str(key_path), "key-main", str(tmp_path)
    )

    # Signature is still valid; only the code file changed after signing.
    _write_code_file(tmp_path, content="print('tampered')\n")

    with pytest.raises(ManifestError):
        verify_manifest(
            str(manifest_path), str(signature_path), str(tmp_path), {"key-main": raw_public}
        )


def test_code_path_escape_is_rejected(tmp_path):
    # Relative escape: rejected at load, before any filesystem access.
    escaping = _manifest_data(code_path="../outside.py")
    with pytest.raises(ManifestError):
        manifest_from_dict(escaping)

    # Absolute path: rejected the same way.
    absolute = _manifest_data(code_path=str(tmp_path / "outside.py"))
    with pytest.raises(ManifestError):
        manifest_from_dict(absolute)

    # Load-time rejection happens even in dev mode (no signature/hash work first).
    manifest_path = _write_manifest(tmp_path, escaping, name="escape.json")
    with pytest.raises(ManifestError):
        verify_manifest(
            str(manifest_path),
            str(tmp_path / "absent.sig"),
            str(tmp_path),
            {},
            dev_mode=True,
        )


def test_unsigned_release_is_rejected(tmp_path):
    _write_code_file(tmp_path)
    manifest_path = _write_manifest(tmp_path, _manifest_data())
    trusted = {"key-main": os.urandom(32)}

    # Missing signature file.
    with pytest.raises(ManifestError):
        verify_manifest(
            str(manifest_path),
            str(tmp_path / "absent.sig"),
            str(tmp_path),
            trusted,
            dev_mode=False,
            frozen=False,
        )

    # Malformed signature JSON.
    signature_path = tmp_path / "manifest.sig"
    signature_path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(ManifestError):
        verify_manifest(str(manifest_path), str(signature_path), str(tmp_path), trusted)

    # Non-base64 signature.
    signature_path.write_text(
        json.dumps({"key_id": "key-main", "signature": "!!not-base64!!"}), encoding="utf-8"
    )
    with pytest.raises(ManifestError):
        verify_manifest(str(manifest_path), str(signature_path), str(tmp_path), trusted)


def test_unsigned_dev_is_allowed_only_with_dev_mode(tmp_path):
    _write_code_file(tmp_path)
    manifest_path = _write_manifest(tmp_path, _manifest_data())
    signature_path = tmp_path / "manifest.sig"  # never written

    manifest = verify_manifest(
        str(manifest_path),
        str(signature_path),
        str(tmp_path),
        {},
        dev_mode=True,
        frozen=False,
    )
    assert manifest.verified is False
    assert len(manifest.tools) == 1
    assert manifest.tools["get_volume"].handler == "core.tools.pc_tools:get_volume"

    # Frozen builds reject unsigned manifests regardless of dev_mode.
    with pytest.raises(ManifestError):
        verify_manifest(
            str(manifest_path),
            str(signature_path),
            str(tmp_path),
            {},
            dev_mode=True,
            frozen=True,
        )


def test_unknown_key_id_is_rejected(tmp_path):
    key_path, raw_public = _generate_key(tmp_path)
    _write_code_file(tmp_path)
    manifest_path = _write_manifest(tmp_path, _manifest_data())
    signature_path = tmp_path / "manifest.sig"
    # Signed under a key id the verifier does not trust.
    sign_manifest(
        str(manifest_path), str(signature_path), str(key_path), "other", str(tmp_path)
    )

    with pytest.raises(ManifestError):
        verify_manifest(
            str(manifest_path), str(signature_path), str(tmp_path), {"key-main": raw_public}
        )


def test_authenticode_rejects_unsigned_file(tmp_path):
    path = tmp_path / "unsigned.txt"
    path.write_text("this file is not signed", encoding="utf-8")
    assert verify_authenticode(str(path)) is False
