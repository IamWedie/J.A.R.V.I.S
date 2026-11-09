"""Archive and reset the audit chain from the command line.

Usage:
    python scripts/audit_reset.py --db audit.db --key audit.key \\
        --archive-dir audit_archive --confirm

The application must be stopped before running this script (the helper
moves the DB and key files; a running writer would race the rename).
Moves the old audit DB, DPAPI audit key, and (when present) the
HMAC-signed ``security_policy.json`` into ``--archive-dir`` under UTC
microsecond names, creates a fresh key and DB, and appends one
``audit.reset`` event.  Archives are never deleted or overwritten; the
new runtime starts with the default-deny policy (old grants must be
re-approved because the policy envelope is bound to the retired key).
Never deletes anything.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.security.audit_maintenance import reset_audit


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="current audit DB path")
    parser.add_argument("--key", required=True, help="current DPAPI audit key path")
    parser.add_argument("--archive-dir", required=True,
                        help="directory to move the old chain material into")
    parser.add_argument("--confirm", action="store_true",
                        help="required to actually archive and reset")
    args = parser.parse_args(argv)

    if not args.confirm:
        print("ERROR: pass --confirm to archive and reset the audit chain",
              file=sys.stderr)
        return 2

    if not (os.path.isfile(args.db) or os.path.isfile(args.key)):
        print("ERROR: neither --db nor --key exists; nothing to archive",
              file=sys.stderr)
        return 1

    try:
        result = reset_audit(
            db_path=Path(os.path.abspath(args.db)),
            key_path=Path(os.path.abspath(args.key)),
            archive_dir=Path(os.path.abspath(args.archive_dir)),
            policy_path=Path(os.path.abspath(args.db)).with_name(
                "security_policy.json"
            ),
        )
    except Exception as exc:
        print(f"ERROR: reset failed: {exc}", file=sys.stderr)
        return 1

    print(f"OK: archived DB:        {result['archived_db']}")
    print(f"OK: archived key:       {result['archived_key']}")
    print(f"OK: archived policy:    {result['archived_policy']}")
    print(f"OK: new DB:             {result['new_db']}")
    print(f"OK: new key:            {result['new_key']}")
    print(f"OK: reset event id:     {result['reset_event_id']}")
    print(f"OK: recovery marker:    {result['recovery_marker']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
