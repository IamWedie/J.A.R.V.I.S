"""Tamper-evident audit log: hash-chained, HMAC-bound SQLite entries.

Every row is linked to its predecessor via ``prev_hash`` and carries
``entry_hash = SHA-256(prev_hash || canonical_event_json)`` plus an
``HMAC-SHA256(key, entry_hash)`` tag, so edits to hashed fields are
detectable without the key (``result_status`` is mutable by design; see
``COLUMNS``).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
import threading
import time

from core.security.canonical import canonical_json

EVENT_TYPES: tuple[str, ...] = (
    "auth.success",
    "auth.failure",
    "session.open",
    "session.close",
    "manifest.load",
    "manifest.reject",
    "policy.decision",
    "policy.change",
    "approval.requested",
    "approval.resolved",
    "approval.rejected",
    "approval.expired",
    "approval.cancelled",
    "tool.request",
    "tool.result",
    "tool.error",
    "tool.timeout",
    "command.request",
    "command.result",
    "feature.toggle",
    "secret.migrated",
    "data.migrated",
    "audit.export",
    "audit.locked",
    "audit.reset",
    "audit.pruned",
    "voice.rejected",
)

#: Every column, in schema order.
ALL_COLUMNS: tuple[str, ...] = (
    "id",
    "ts",
    "event_type",
    "session_id",
    "source",
    "tool",
    "capabilities",
    "risk",
    "request_id",
    "args_hash",
    "args_redacted",
    "decision",
    "approver",
    "result_status",
    "error_code",
    "prev_hash",
    "entry_hash",
    "hmac",
)

#: Fields authenticated by ``entry_hash``.  ``result_status`` is deliberately
#: excluded: ``reconcile_incomplete`` updates it on an already-written row, and
#: re-hashing that row would invalidate every link after it.
COLUMNS: tuple[str, ...] = tuple(
    c for c in ALL_COLUMNS if c not in {"result_status", "entry_hash", "hmac"}
)

ZERO_ANCHOR = "0" * 64
_REDACTED = "[REDACTED]"
_VALUE_LIMIT = 120
_SECRET_KEY_RE = re.compile(
    r"api_key|token|secret|password|pin|license_key", re.IGNORECASE
)

# Process-wide writer lock: serializes writes (and reads of the shared
# connection) across every AuditLog instance in this process.
_LOCK = threading.Lock()

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        event_type TEXT NOT NULL,
        session_id TEXT,
        source TEXT,
        tool TEXT,
        capabilities TEXT,
        risk TEXT,
        request_id TEXT,
        args_hash TEXT,
        args_redacted TEXT,
        decision TEXT,
        approver TEXT,
        result_status TEXT,
        error_code TEXT,
        prev_hash TEXT NOT NULL,
        entry_hash TEXT NOT NULL,
        hmac TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_audit_request_id ON audit(request_id)",
    """CREATE TABLE IF NOT EXISTS audit_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
)


def hash_args(args: dict) -> str:
    """SHA-256 (hex) of the canonical JSON encoding of *args*."""
    return hashlib.sha256(canonical_json(args)).hexdigest()


def redact_args(args: dict) -> str:
    """Canonical JSON of *args* with secret-looking values replaced.

    Keys matching ``api_key|token|secret|password|pin|license_key``
    (case-insensitive) become ``[REDACTED]``; every other string value is
    truncated to 120 characters.
    """
    cleaned = {}
    for key, value in args.items():
        if _SECRET_KEY_RE.search(str(key)):
            cleaned[key] = _REDACTED
        elif isinstance(value, str):
            cleaned[key] = value[:_VALUE_LIMIT]
        else:
            cleaned[key] = value
    return canonical_json(cleaned).decode("utf-8")


def _entry_hash(event: dict) -> str:
    """SHA-256 hex of ``prev_hash || canonical_event_json``."""
    payload = canonical_json(event)
    return hashlib.sha256(event["prev_hash"].encode("utf-8") + payload).hexdigest()


def _hmac_digest(key: bytes, entry_hash: str) -> str:
    return hmac.new(key, entry_hash.encode("utf-8"), hashlib.sha256).hexdigest()


class AuditLog:
    """Append-only, hash-chained audit store backed by SQLite."""

    def __init__(self, db_path, hmac_key: bytes, retention_days: int = 90):
        self._key = bytes(hmac_key)
        self._retention_days = retention_days
        # check_same_thread=False: every access goes through _LOCK, so the
        # shared connection may be used from worker threads.
        self._conn = sqlite3.connect(
            os.fspath(db_path), isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with _LOCK:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _SCHEMA:
                    self._conn.execute(statement)
                self._conn.execute(
                    "INSERT OR IGNORE INTO audit_meta (key, value) VALUES (?, ?)",
                    ("chain_anchor", ZERO_ANCHOR),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    # -- writing -----------------------------------------------------------

    def append(
        self,
        event_type: str,
        *,
        session_id=None,
        source=None,
        tool=None,
        capabilities=(),
        risk=None,
        request_id=None,
        args=None,
        args_redacted=None,
        decision=None,
        approver=None,
        result_status=None,
        error_code=None,
    ) -> int:
        """Append one event inside a ``BEGIN IMMEDIATE`` transaction; return its id."""
        args_hash = hash_args(args) if args is not None else None
        if args_redacted is None and args is not None:
            args_redacted = redact_args(args)
        caps = (
            canonical_json(list(capabilities)).decode("utf-8") if capabilities else None
        )
        with _LOCK:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                entry_id = self._insert(
                    event_type,
                    prev_hash=self._tip_or_anchor(),
                    session_id=session_id,
                    source=source,
                    tool=tool,
                    capabilities=caps,
                    risk=risk,
                    request_id=request_id,
                    args_hash=args_hash,
                    args_redacted=args_redacted,
                    decision=decision,
                    approver=approver,
                    result_status=result_status,
                    error_code=error_code,
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return entry_id

    def reconcile_incomplete(self) -> int:
        """Mark dangling ``tool.request`` rows unknown and append a ``tool.error``.

        A request is dangling when no ``tool.result``/``tool.error`` with the
        same ``request_id`` was appended after it.  Returns the number of
        requests reconciled; a second call is a no-op.
        """
        with _LOCK:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                # ``IS`` is SQLite's NULL-safe equality, so requests without a
                # request_id are also closed out (and stay idempotent).
                pending = conn.execute(
                    "SELECT r.id AS id, r.tool AS tool, r.request_id AS request_id"
                    " FROM audit AS r WHERE r.event_type = 'tool.request'"
                    " AND NOT EXISTS ("
                    " SELECT 1 FROM audit AS x"
                    " WHERE x.request_id IS r.request_id"
                    " AND x.event_type IN ('tool.result', 'tool.error')"
                    " AND x.id > r.id)"
                ).fetchall()
                count = 0
                for row in pending:
                    conn.execute(
                        "UPDATE audit SET result_status = 'unknown' WHERE id = ?",
                        (row["id"],),
                    )
                    self._insert(
                        "tool.error",
                        prev_hash=self._tip_or_anchor(),
                        tool=row["tool"],
                        request_id=row["request_id"],
                        error_code="unknown_result",
                    )
                    count += 1
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return count

    def export(self, path) -> int:
        """Write one canonical JSON object per line in id order, then log the export.

        Returns the number of rows written (the ``audit.export`` event itself
        is appended after the file is written and is therefore not in it).
        """
        with _LOCK:
            conn = self._conn
            rows = conn.execute("SELECT * FROM audit ORDER BY id ASC").fetchall()
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                for row in rows:
                    event = {column: row[column] for column in ALL_COLUMNS}
                    handle.write(canonical_json(event).decode("utf-8"))
                    handle.write("\n")
            count = len(rows)
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._insert("audit.export", prev_hash=self._tip_or_anchor())
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return count

    def prune(self, now: float | None = None) -> int:
        """Delete rows older than the retention cutoff and log the new anchor.

        The last deleted row's ``entry_hash`` becomes the ``chain_anchor`` in
        ``audit_meta``; one ``audit.pruned`` event is appended with that anchor
        as its ``prev_hash``.  Returns the number of deleted rows.
        """
        ts_now = time.time() if now is None else now
        cutoff = ts_now - self._retention_days * 86400.0
        with _LOCK:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                doomed = conn.execute(
                    "SELECT id, entry_hash FROM audit WHERE ts < ? ORDER BY id DESC",
                    (cutoff,),
                ).fetchall()
                if doomed:
                    new_anchor = doomed[0]["entry_hash"]  # highest deleted id
                    conn.execute("DELETE FROM audit WHERE ts < ?", (cutoff,))
                    conn.execute(
                        "UPDATE audit_meta SET value = ? WHERE key = 'chain_anchor'",
                        (new_anchor,),
                    )
                    self._insert(
                        "audit.pruned", prev_hash=new_anchor, ts=ts_now
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return len(doomed)

    # -- reading -----------------------------------------------------------

    def verify_chain(self) -> bool:
        """Recompute every row from the stored anchor; False on first mismatch."""
        with _LOCK:
            running = self._meta("chain_anchor") or ZERO_ANCHOR
            for row in self._conn.execute("SELECT * FROM audit ORDER BY id ASC"):
                event = {column: row[column] for column in COLUMNS}
                if event["prev_hash"] != running:
                    return False
                expected = _entry_hash(event)
                if not hmac.compare_digest(expected, row["entry_hash"]):
                    return False
                mac = _hmac_digest(self._key, row["entry_hash"])
                if not hmac.compare_digest(mac, row["hmac"]):
                    return False
                running = row["entry_hash"]
        return True

    def recent(self, limit: int = 20) -> list[dict]:
        """Newest *limit* rows in chronological order as dicts."""
        with _LOCK:
            rows = self._conn.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {column: row[column] for column in ALL_COLUMNS} for row in reversed(rows)
        ]

    def close(self) -> None:
        with _LOCK:
            self._conn.close()

    # -- internals ---------------------------------------------------------

    def _insert(
        self,
        event_type: str,
        *,
        prev_hash: str,
        session_id=None,
        source=None,
        tool=None,
        capabilities=None,
        risk=None,
        request_id=None,
        args_hash=None,
        args_redacted=None,
        decision=None,
        approver=None,
        result_status=None,
        error_code=None,
        ts: float | None = None,
    ) -> int:
        """Hash, MAC, and insert one row.  Caller holds _LOCK and a transaction."""
        conn = self._conn
        next_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM audit"
        ).fetchone()["next_id"]
        event = {
            "id": next_id,
            # Coerce so an int ``now`` (e.g. prune) hashes the same way SQLite
            # reads the REAL column back.
            "ts": float(time.time() if ts is None else ts),
            "event_type": event_type,
            "session_id": session_id,
            "source": source,
            "tool": tool,
            "capabilities": capabilities,
            "risk": risk,
            "request_id": request_id,
            "args_hash": args_hash,
            "args_redacted": args_redacted,
            "decision": decision,
            "approver": approver,
            # result_status is stored but not authenticated: see COLUMNS.
            "error_code": error_code,
            "prev_hash": prev_hash,
        }
        entry_hash = _entry_hash(event)
        mac = _hmac_digest(self._key, entry_hash)
        conn.execute(
            "INSERT INTO audit (id, ts, event_type, session_id, source, tool,"
            " capabilities, risk, request_id, args_hash, args_redacted, decision,"
            " approver, result_status, error_code, prev_hash, entry_hash, hmac)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event["id"],
                event["ts"],
                event_type,
                session_id,
                source,
                tool,
                capabilities,
                risk,
                request_id,
                args_hash,
                args_redacted,
                decision,
                approver,
                result_status,
                error_code,
                prev_hash,
                entry_hash,
                mac,
            ),
        )
        return next_id

    def _tip_or_anchor(self) -> str:
        """Hash linking the next row: current tip, or the anchor on an empty log."""
        row = self._conn.execute(
            "SELECT entry_hash FROM audit ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            return row["entry_hash"]
        return self._meta("chain_anchor") or ZERO_ANCHOR

    def _meta(self, key: str):
        row = self._conn.execute(
            "SELECT value FROM audit_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None
