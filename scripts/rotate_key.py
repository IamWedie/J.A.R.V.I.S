"""Generate / rotate the JARVIS license signing keypair.

Creates or appends to the embedded public keyring (``core/pubkeys.json``),
which ships inside the packaged app and is used to verify keys offline. The
private key is written to a local, git-ignored file and NEVER ships in the app.

Usage:
    python scripts/rotate_key.py [--priv scripts/keys/license_priv.pem]

Exit code 0 on success.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import license_keys as lk


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--priv", default=os.path.join("scripts", "keys", "license_priv.pem"))
    parser.add_argument("--out-json", default=lk.KEYRING_PATH)
    args = parser.parse_args(argv)

    priv_dir = os.path.dirname(args.priv)
    os.makedirs(priv_dir, exist_ok=True)

    if os.path.exists(args.priv):
        print(f"ERROR: private key already exists at {args.priv}", file=sys.stderr)
        print("Refusing to overwrite an existing private key.", file=sys.stderr)
        return 2

    priv_pem, pub_b64 = lk.generate_keypair()
    with open(args.priv, "w", encoding="utf-8") as f:
        f.write(priv_pem + "\n")

    lk.add_to_keyring(pub_b64, keyring_path=args.out_json)

    print(f"Private key written: {args.priv}  (keep this SECRET, never commit)")
    print(f"Public key added to keyring: {args.out_json}")
    print(f"Public key (raw b64): {pub_b64}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
