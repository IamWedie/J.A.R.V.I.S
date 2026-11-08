"""Verify an audit hash chain read-only from the command line.

Usage:
    python scripts/audit_verify.py --db audit.db --key audit.key

Opens the database in read-only mode (never creates a writer, never
creates a missing DB or key, never runs schema DDL) and recomputes the
whole chain.  Exit codes: 0 when the chain verifies, 1 on any missing
file, corrupt key, tampered row, or dropped table.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.security.audit import verify_chain_at
from core.security.keystore import KeyStoreError, load_or_create_key


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="audit DB to verify")
    parser.add_argument("--key", required=True, help="DPAPI audit key file")
    args = parser.parse_args(argv)

    for label, path in (("--db", args.db), ("--key", args.key)):
        if not os.path.isfile(path):
            print(f"ERROR: {label} file does not exist: {path}", file=sys.stderr)
            return 1

    try:
        key = load_or_create_key(args.key)
    except KeyStoreError as exc:
        print(f"ERROR: could not load audit key: {exc}", file=sys.stderr)
        return 1

    if verify_chain_at(args.db, key):
        print("OK: audit chain verified")
        return 0
    print("ERROR: audit chain verification failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
