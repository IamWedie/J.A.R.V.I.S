"""Verify a signed tool manifest against the trusted tool keyring.

Usage:
    python scripts/verify_tool_manifest.py --manifest manifest.json \\
        --signature manifest.sig --code-root C:/path/to/code \\
        [--executable frozen.exe] [--dev]

Exit codes: 0 only when the manifest is fully verified (signature valid and,
in source mode, every code-file hash matches), 1 on any rejection.  The
trusted keyring lives in ``core.security.tool_pubkeys`` (added by Task 8);
until it exists the keyring is treated as empty and verification fails with
a clear message.  ``--dev`` tolerates a missing/invalid signature but still
exits 1, because only ``verified=True`` counts as success.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.security.manifest import ManifestError, verify_manifest


def _load_trusted_keys() -> dict:
    """Load core.security.tool_pubkeys.TRUSTED_TOOL_KEYS, or {} if absent."""
    try:
        from core.security import tool_pubkeys
    except ImportError:
        return {}
    keys = getattr(tool_pubkeys, "TRUSTED_TOOL_KEYS", None)
    if not isinstance(keys, dict):
        return {}
    return keys


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="manifest JSON to verify")
    parser.add_argument("--signature", required=True, help="signature JSON to verify")
    parser.add_argument("--code-root", required=True, help="root the code_files paths resolve against")
    parser.add_argument("--executable", default=None, help="executable to Authenticode-check in frozen mode")
    parser.add_argument("--dev", action="store_true", help="allow an unsigned manifest (still exits 1 unless verified)")
    args = parser.parse_args(argv)

    trusted_keys = _load_trusted_keys()
    if not trusted_keys:
        print(
            "ERROR: no trusted tool keys (core.security.tool_pubkeys is missing or empty)",
            file=sys.stderr,
        )
        return 1

    try:
        manifest = verify_manifest(
            args.manifest,
            args.signature,
            args.code_root,
            trusted_keys,
            dev_mode=args.dev,
            executable_path=args.executable,
        )
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not manifest.verified:
        print("ERROR: manifest is not verified", file=sys.stderr)
        return 1
    print(f"OK: manifest verified ({len(manifest.tools)} tool(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
