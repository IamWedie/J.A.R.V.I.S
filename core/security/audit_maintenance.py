"""Shared archive-and-reset helper for the tamper-evident audit chain.

Both ``POST /api/security/audit_reset`` (server.py) and
``scripts/audit_reset.py`` call :func:`reset_audit`, so the operational
rules live in exactly one place:

- The old audit DB, the old DPAPI audit key, and (when it exists) the
  HMAC-signed ``security_policy.json`` are *moved* (``os.rename``, same
  volume) into ``archive_dir`` under UTC-microsecond names.  Archives are
  never deleted, overwritten, or followed back: a reset never rewrites the
  old material.
- The policy file must be archived even though a reset normally keeps
  policy state: it is HMAC-bound to the *old* audit key, so a new audit key
  can never verify the old envelope.  The rebuilt runtime therefore starts
  with the default-deny policy and every old grant must be re-approved.
- A fresh key and DB are created and exactly one ``audit.reset`` event is
  appended, whose ``args`` record the archived file *basenames*.
- A JSON recovery marker (``audit_recovery.json`` by default) is written
  atomically so the archived paths can be found again after the fact.

This module never touches the running runtime; callers rebuild and
reinstall it themselves.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from core.security.audit import AuditLog
from core.security.keystore import load_or_create_key

__all__ = ["reset_audit"]


def _utc_stamp(now=None) -> str:
    """``YYYYMMDDTHHMMSSffffffZ`` in UTC (microsecond-unique-per-second)."""
    ts = time.time() if now is None else now
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )


def _archive_into(
    source: Path, archive_dir: Path, prefix: str, stamp: str, suffix: str
):
    """Move *source* into ``archive_dir``; None when the source is absent.

    Uses ``os.rename`` (same volume, atomic) and refuses to overwrite any
    existing archive target, so an archive file is never destroyed.
    """
    if not source.exists():
        return None
    target = archive_dir / f"{prefix}-{stamp}{suffix}"
    if target.exists():
        raise FileExistsError(f"archive target already exists: {target}")
    os.rename(os.fspath(source), os.fspath(target))
    return target


def _write_marker(path: Path, marker: dict) -> None:
    """Atomic write (temp + fsync + replace); mode 600 best-effort."""
    payload = json.dumps(marker, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".audit_recovery-", suffix=".tmp", dir=os.fspath(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, 0o600)  # best-effort on Windows
        except OSError:
            pass
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def reset_audit(
    *,
    db_path,
    key_path,
    archive_dir,
    policy_path=None,
    marker_path=None,
    now=None,
) -> dict:
    """Archive the old chain material and start a fresh, verified chain.

    Moves ``db_path``/``key_path`` (and ``policy_path`` when it exists) into
    ``archive_dir`` under ``<prefix>-<utc-stamp>.<ext>`` names, creates a new
    audit key and DB, appends exactly one ``audit.reset`` event, and writes
    the recovery marker (default ``key_path.parent / "audit_recovery.json"``).

    Returns a JSON-safe dict of absolute paths (or ``None`` per key when the
    corresponding file did not exist) plus the reset event id and timestamp:
    ``archived_db``, ``archived_key``, ``archived_policy``,
    ``recovery_marker``, ``new_db``, ``new_key``, ``reset_event_id``,
    ``timestamp``.

    Never deletes or overwrites an archive.  If the ``audit.reset`` append
    fails, the log is closed and the exception propagates: the new key/DB
    remain and the old material stays archived.
    """
    stamp = _utc_stamp(now)
    db_path = Path(os.fspath(db_path))
    key_path = Path(os.fspath(key_path))
    archive = Path(os.fspath(archive_dir))
    archive.mkdir(parents=True, exist_ok=True)

    archived_db = _archive_into(db_path, archive, "audit", stamp, ".db")
    archived_key = _archive_into(key_path, archive, "audit", stamp, ".key")
    archived_policy = None
    if policy_path is not None:
        archived_policy = _archive_into(
            Path(os.fspath(policy_path)), archive, "policy", stamp, ".json"
        )

    # A brand-new key: load_or_create_key creates it because nothing is left
    # at key_path (the old key was archived above).
    new_key = load_or_create_key(key_path)
    log = AuditLog(db_path, new_key)
    try:
        reset_event_id = log.append(
            "audit.reset",
            source="reset",
            tool="audit",
            result_status="ok",
            args={
                "archived_db": (
                    archived_db.name if archived_db is not None else None
                ),
                "archived_key": (
                    archived_key.name if archived_key is not None else None
                ),
                "archived_policy": (
                    archived_policy.name if archived_policy is not None else None
                ),
                "note": (
                    "policy file archived: HMAC-bound to the retired audit "
                    "key; the rebuilt runtime starts with the default deny "
                    "policy and old grants must be re-approved"
                ),
            },
        )
    finally:
        log.close()

    timestamp = datetime.fromtimestamp(time.time() if now is None else now,
                                       tz=timezone.utc).isoformat()
    marker = {
        "schema_version": 1,
        "timestamp": timestamp,
        "archived": {
            "db": str(archived_db) if archived_db is not None else None,
            "key": str(archived_key) if archived_key is not None else None,
            "policy": str(archived_policy) if archived_policy is not None else None,
        },
        "new": {"db": str(db_path), "key": str(key_path)},
        "reset_event_id": reset_event_id,
    }
    resolved_marker = (
        Path(os.fspath(marker_path))
        if marker_path is not None
        else key_path.parent / "audit_recovery.json"
    )
    resolved_marker.parent.mkdir(parents=True, exist_ok=True)
    _write_marker(resolved_marker, marker)

    return {
        "archived_db": str(archived_db) if archived_db is not None else None,
        "archived_key": str(archived_key) if archived_key is not None else None,
        "archived_policy": (
            str(archived_policy) if archived_policy is not None else None
        ),
        "recovery_marker": str(resolved_marker),
        "new_db": str(db_path),
        "new_key": str(key_path),
        "reset_event_id": reset_event_id,
        "timestamp": timestamp,
    }
