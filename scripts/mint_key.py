"""Mint JARVIS license keys signed with the private key.

The private key must exist (see scripts/rotate_key.py). Keys are self-contained
and verified offline by the app using the embedded public keyring.

Usage:
    python scripts/mint_key.py                       # one lifetime key
    python scripts/mint_key.py 5                     # five lifetime keys
    python scripts/mint_key.py --days 365            # one 365-day key
    python scripts/mint_key.py --flags 1             # subscription flag
    python scripts/mint_key.py VERIFY JARV-...       # verify an existing key
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import license as lic
from core import license_keys as lk

DEFAULT_PRIV = os.path.join("scripts", "keys", "license_priv.pem")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--priv", default=DEFAULT_PRIV)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--days", type=int, default=0,
                        help="expiry in days (0 = lifetime)")
    parser.add_argument("--flags", type=int, default=0)
    parser.add_argument("verify", nargs="?", help="existing key to verify")
    args = parser.parse_args(argv)

    if not os.path.exists(args.priv):
        print(
            f"ERROR: private key not found at {args.priv}\n"
            f"Generate it first: python scripts/rotate_key.py --priv {args.priv}",
            file=sys.stderr,
        )
        return 2

    with open(args.priv, "r", encoding="utf-8") as f:
        priv_pem = f.read()

    if args.verify:
        ok, reason, info = lk.verify_key(args.verify, lk.load_keyring())
        print(f"{args.verify} -> {'VALID' if ok else 'INVALID'}")
        if ok:
            print(f"  info: {info}")
        else:
            print(f"  reason: {reason}")
        return 0 if ok else 1

    for _ in range(max(1, args.count)):
        print(lic.generate_license(priv_pem, flags=args.flags, expiry_days=args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
