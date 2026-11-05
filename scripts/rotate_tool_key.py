"""Generate an Ed25519 keypair for signing tool manifests.

The private key is written to a local file and MUST NEVER be committed; the
raw public key (base64) and its key ID are safe to publish in the trusted
keyring (``core.security.tool_pubkeys``, Task 8).

Usage:
    python scripts/rotate_tool_key.py --private-out scripts/keys/tool_priv.pem
    python scripts/rotate_tool_key.py --private-out tools/key.pem --key-id my-key --force

Exit codes: 0 on success, 2 when refusing to overwrite an existing key.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--private-out", required=True, help="where to write the PKCS8 PEM private key"
    )
    parser.add_argument(
        "--key-id",
        default=None,
        help="key id (default: sha256 prefix of the raw public key)",
    )
    parser.add_argument(
        "--force", action="store_true", help="overwrite an existing private key"
    )
    parser.add_argument(
        "--public-out",
        default=None,
        help="optionally write the base64 raw public key to this file",
    )
    args = parser.parse_args(argv)

    if os.path.exists(args.private_out) and not args.force:
        print(f"ERROR: private key already exists at {args.private_out}", file=sys.stderr)
        print("Refusing to overwrite an existing private key without --force.", file=sys.stderr)
        return 2

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    parent = os.path.dirname(args.private_out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.private_out, "wb") as handle:
        handle.write(private_pem)  # PEM already ends with a newline
    try:
        os.chmod(args.private_out, 0o600)  # best effort; ignored on some FS
    except OSError:
        pass

    raw_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_b64 = base64.b64encode(raw_public).decode("ascii")
    key_id = args.key_id or hashlib.sha256(raw_public).hexdigest()[:16]

    if args.public_out:
        public_parent = os.path.dirname(args.public_out)
        if public_parent:
            os.makedirs(public_parent, exist_ok=True)
        with open(args.public_out, "w", encoding="ascii", newline="\n") as handle:
            handle.write(public_b64 + "\n")

    print(f"Private key written: {args.private_out}")
    print(f"Public key (raw b64): {public_b64}")
    print(f"Key ID: {key_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
