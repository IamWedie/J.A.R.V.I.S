"""Tamper-evident audit log: hash-chained, HMAC-bound SQLite entries.

Every row is linked to its predecessor via ``prev_hash`` and carries
``entry_hash = SHA-256(prev_hash || canonical_event_json)`` plus an
``HMAC-SHA256(key, entry_hash)`` tag, so edits to any hashed field
(every column except ``entry_hash``/``hmac`` themselves) are detectable
without the key.  Legitimate in-place mutations
(``reconcile_incomplete``) re-hash the mutated row and re-stamp every
later row inside the same transaction (see ``_restamp_from``).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

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

#: Fields authenticated by ``entry_hash`` (everything except the hash and MAC
#: columns themselves).  ``result_status`` is included: when
#: ``reconcile_incomplete`` updates it on an already-written row it re-hashes
#: that row and re-stamps every later row in the same transaction.
COLUMNS: tuple[str, ...] = tuple(c for c in ALL_COLUMNS if c not in {"entry_hash", "hmac"})

ZERO_ANCHOR = "0" * 64
_REDACTED = "[REDACTED]"
_VALUE_LIMIT = 120
#: Matched against *normalized* keys (casefolded, non-alphanumerics stripped),
#: so ``api_key``/``apikey``/``api-key`` and ``licenseKey`` are all caught.
_SECRET_KEY_RE = re.compile(r"apikey|token|secret|password|pin|licensekey")

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


def _normalize_key(key) -> str:
    """Casefold *key* and drop every character outside ``[0-9a-z]``."""
    return re.sub(r"[^0-9a-z]", "", str(key).casefold())


def _redact(value):
    """Recursively redact secret-looking keys and truncate strings."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if _SECRET_KEY_RE.search(_normalize_key(key)):
                cleaned[key] = _REDACTED
            else:
                cleaned[key] = _redact(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return value[:_VALUE_LIMIT]
    return value


def redact_args(args: dict) -> str:
    """Canonical JSON of *args* with secret-looking values replaced.

    Walks nested dicts and lists.  A key whose normalized form matches
    ``apikey|token|secret|password|pin|licensekey`` has its whole value
    replaced with ``[REDACTED]``; every other string value (nested ones
    included) is truncated to 120 characters.
    """
    return canonical_json(_redact(args)).decode("utf-8")


def _entry_hash(event: dict) -> str:
    """SHA-256 hex of ``prev_hash || canonical_event_json``."""
    payload = canonical_json(event)
    return hashlib.sha256(event["prev_hash"].encode("utf-8") + payload).hexdigest()


def _hmac_digest(key: bytes, entry_hash: str) -> str:
    return hmac.new(key, entry_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_rows(conn, key: bytes) -> bool:
    """Recompute every row from the stored anchor; False on first mismatch.

    Caller owns ``conn``'s row factory (must be ``sqlite3.Row``) and locking.
    A missing ``audit`` or ``audit_meta`` table raises ``sqlite3.Error`` here
    so the read-only path can treat it as tampering (never a re-creation).
    """
    row = conn.execute(
        "SELECT value FROM audit_meta WHERE key = 'chain_anchor'"
    ).fetchone()
    running = row["value"] if row is not None else ZERO_ANCHOR
    for row in conn.execute("SELECT * FROM audit ORDER BY id ASC"):
        event = {column: row[column] for column in COLUMNS}
        if event["prev_hash"] != running:
            return False
        expected = _entry_hash(event)
        if not hmac.compare_digest(expected, row["entry_hash"]):
            return False
        mac = _hmac_digest(key, row["entry_hash"])
        if not hmac.compare_digest(mac, row["hmac"]):
            return False
        running = row["entry_hash"]
    return True


def verify_chain_at(db_path, hmac_key) -> bool:
    """Verify a stored chain read-only; False on missing/tampered/corrupt DBs.

    Opens the database with ``mode=ro`` (never creates a writer) and never
    runs schema DDL: a dropped table counts as tampering and returns False.
    Used by the audit verification scripts, which must not touch the chain.
    """
    try:
        uri = Path(os.path.abspath(os.fspath(db_path))).as_uri() + "?mode=ro"
        key = bytes(hmac_key)
    except (TypeError, ValueError, OSError):
        return False
    try:
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.Error:
        return False
    try:
        conn.row_factory = sqlite3.Row
        return _verify_rows(conn, key)
    except (sqlite3.Error, TypeError, ValueError):
        return False
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


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
        if not event_type or event_type not in EVENT_TYPES:
            raise ValueError(f"unknown audit event type: {event_type!r}")
        # Hash the REDACTED form: raw args (e.g. a 4-digit PIN) are
        # brute-forceable from an unsalted SHA-256.
        redacted = _redact(args) if args is not None else None
        args_hash = hash_args(redacted) if redacted is not None else None
        if args_redacted is None and redacted is not None:
            args_redacted = canonical_json(redacted).decode("utf-8")
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
                if pending:
                    for row in pending:
                        conn.execute(
                            "UPDATE audit SET result_status = 'unknown' WHERE id = ?",
                            (row["id"],),
                        )
                    # ``result_status`` is authenticated: re-hash the mutated
                    # rows and re-stamp every later row so the chain stays
                    # verifiable.
                    self._restamp_from(min(row["id"] for row in pending))
                    for row in pending:
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
            rows = self._conn.execute(
                "SELECT * FROM audit ORDER BY id ASC"
            ).fetchall()
        # File I/O happens outside the lock; the snapshot is already
        # materialised in memory.
        events = [{column: row[column] for column in ALL_COLUMNS} for row in rows]
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(canonical_json(event).decode("utf-8"))
                handle.write("\n")
        count = len(events)
        with _LOCK:
            conn = self._conn
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
        ``audit_meta``; one ``audit.pruned`` event is appended with the
        surviving tip as its ``prev_hash`` (the stored anchor when nothing
        survives), so both a partial and a full prune leave the chain
        verifiable.  Returns the number of deleted rows.
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
                    # Survivors chain from the tip; after a full prune the
                    # table is empty and _tip_or_anchor() yields the anchor
                    # we just stored — both keep verify_chain() green.
                    self._insert(
                        "audit.pruned",
                        prev_hash=self._tip_or_anchor(),
                        ts=ts_now,
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
            return _verify_rows(self._conn, self._key)

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
            "result_status": result_status,
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

    def _restamp_from(self, start_id: int) -> None:
        """Re-hash row *start_id* and re-link every later row after an in-place
        mutation (e.g. ``result_status``).  Caller holds _LOCK + transaction."""
        conn = self._conn
        prev = conn.execute(
            "SELECT entry_hash FROM audit WHERE id < ? ORDER BY id DESC LIMIT 1",
            (start_id,),
        ).fetchone()
        running = (
            prev["entry_hash"]
            if prev is not None
            else (self._meta("chain_anchor") or ZERO_ANCHOR)
        )
        rows = conn.execute(
            "SELECT * FROM audit WHERE id >= ? ORDER BY id ASC", (start_id,)
        ).fetchall()
        for row in rows:
            event = {column: row[column] for column in COLUMNS}
            event["prev_hash"] = running
            entry_hash = _entry_hash(event)
            conn.execute(
                "UPDATE audit SET prev_hash = ?, entry_hash = ?, hmac = ?"
                " WHERE id = ?",
                (
                    running,
                    entry_hash,
                    _hmac_digest(self._key, entry_hash),
                    row["id"],
                ),
            )
            running = entry_hash

    def _meta(self, key: str):
        row = self._conn.execute(
            "SELECT value FROM audit_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None
