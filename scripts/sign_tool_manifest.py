"""Sign a tool manifest: fill code-file hashes, rewrite JSON, emit signature.

Usage:
    python scripts/sign_tool_manifest.py --manifest manifest.json \\
        --signature manifest.sig --key tool_priv.pem --key-id my-key \\
        --code-root C:/path/to/code

The manifest is rewritten as canonical pretty JSON (indent=2, sorted keys,
UTF-8, trailing newline) with every ``code_files[].sha256`` recomputed using
the same CRLF-normalized ``sha256_file`` the verifier uses.  The Ed25519
signature is computed over ``canonical_json`` of the *parsed* manifest, not
the pretty bytes, so re-serialization never changes the signature.  The
signature file is deterministic: sorted keys, trailing newline.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.security.canonical import canonical_json
from core.security.manifest import (
    ManifestError,
    manifest_from_dict,
    resolve_code_path,
    sha256_file,
)


def _load_manifest(path: str) -> dict:
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        raise ManifestError("manifest file is missing") from None
    try:
        data = json.loads(raw)
    except ValueError:
        raise ManifestError("manifest is not valid JSON") from None
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be an object")
    return data


def _load_private_key(path: str) -> Ed25519PrivateKey:
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        raise ManifestError("private key file is missing") from None
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except (TypeError, ValueError):
        raise ManifestError("private key file is not a valid PEM key") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise ManifestError("private key must be an Ed25519 key")
    return key


def sign_manifest(
    manifest_path: str,
    signature_path: str,
    private_key_path: str,
    key_id: str,
    code_root: str,
) -> dict:
    """Fill code hashes, rewrite the manifest, and write its Ed25519 signature.

    Returns the signature payload ``{"key_id", "signature"}``.  Raises
    ``ManifestError`` for any structural, path, or key problem.
    """
    if not isinstance(key_id, str) or not key_id:
        raise ManifestError("key id must be a non-empty string")
    data = _load_manifest(manifest_path)
    # Structural + static path validation with the same rules as verification.
    manifest_from_dict(data)

    for tool in data["tools"]:
        for code_file in tool["code_files"]:
            resolved = resolve_code_path(code_root, code_file["path"])
            code_file["sha256"] = sha256_file(resolved)

    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except (OSError, UnicodeError):
        raise ManifestError("manifest could not be rewritten") from None

    # Sign the canonical form of the parsed (updated) manifest, never the
    # pretty-printed bytes.
    parsed = json.loads(text)
    signature = _load_private_key(private_key_path).sign(canonical_json(parsed))

    payload = {
        "key_id": key_id,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    try:
        with open(signature_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(
                    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                + "\n"
            )
    except (OSError, UnicodeError):
        raise ManifestError("signature file could not be written") from None
    return payload


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="manifest JSON to sign in place")
    parser.add_argument("--signature", required=True, help="where to write the signature JSON")
    parser.add_argument("--key", required=True, help="Ed25519 private key PEM")
    parser.add_argument("--key-id", required=True, help="key id recorded in the signature")
    parser.add_argument("--code-root", required=True, help="root the code_files paths resolve against")
    args = parser.parse_args(argv)

    try:
        payload = sign_manifest(
            args.manifest, args.signature, args.key, args.key_id, args.code_root
        )
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Manifest signed: {args.manifest}")
    print(f"Signature written: {args.signature} (key id: {payload['key_id']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
