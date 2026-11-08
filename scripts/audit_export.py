"""Export verified audit events to NDJSON from the command line.

Usage:
    python scripts/audit_export.py --db audit.db --key audit.key --out events.ndjson

Verifies the chain (read-only) before writing anything; a tampered or
missing DB never produces an export file.  The DB is then opened with the
usual writer to stream one canonical JSON object per line (in id order).
Exit codes: 0 on a verified export, 1 on missing files, decode failures,
or tampering.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.security.audit import AuditLog, verify_chain_at
from core.security.keystore import KeyStoreError, load_or_create_key


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="audit DB to export")
    parser.add_argument("--key", required=True, help="DPAPI audit key file")
    parser.add_argument("--out", required=True, help="NDJSON output path")
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

    if not verify_chain_at(args.db, key):
        print("ERROR: audit chain verification failed; refusing to export",
              file=sys.stderr)
        return 1

    try:
        out_path = Path(os.path.abspath(os.fspath(args.out)))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        log = AuditLog(args.db, key)
        try:
            count = log.export(out_path)
        finally:
            log.close()
    except (OSError, sqlite3.Error) as exc:
        print(f"ERROR: export failed: {exc}", file=sys.stderr)
        return 1
    print(f"OK: exported {count} event(s) to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
