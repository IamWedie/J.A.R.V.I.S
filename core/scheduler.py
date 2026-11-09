import asyncio
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from core import config
from core.logging_setup import get_logger

log = get_logger("scheduler")

_conn = None
_db_path = None
_lock = threading.Lock()
_main_loop = None
_fired_callback = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fire_at REAL NOT NULL,
    message TEXT NOT NULL,
    type TEXT NOT NULL,
    fired INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
)
"""


def _ensure_conn_locked():
    """Lazily open (and create the schema for) the SQLite reminder store.

    Must only be called while ``_lock`` is held.
    """
    global _conn
    if _conn is None:
        path = _db_path or os.path.join(config.data_dir(), "scheduler.db")
        conn = sqlite3.connect(
            path, timeout=10, check_same_thread=False, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        conn.execute(_SCHEMA)
        _conn = conn
    return _conn


def init(main_loop, callback, db_path=None):
    global _conn, _db_path, _main_loop, _fired_callback
    _main_loop = main_loop
    _fired_callback = callback
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
        _db_path = os.fspath(db_path) if db_path is not None else None
        # Open the new database immediately so a bad path fails at init.
        _ensure_conn_locked()


def set_reminder(seconds, message):
    now = time.time()
    fire_at = now + seconds
    with _lock:
        conn = _ensure_conn_locked()
        conn.execute(
            "INSERT INTO reminders (fire_at, message, type, fired, created_at)"
            " VALUES (?, ?, 'timer', 0, ?)",
            (fire_at, message, now),
        )
    return f"Reminder set: '{message}' in {seconds // 60}m {seconds % 60}s"


def set_alarm(time_str, message=""):
    try:
        now = datetime.now()
        for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
            try:
                t = datetime.strptime(time_str.strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return f"Could not understand time: {time_str}. Use HH:MM format."
        target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        seconds = (target - now).total_seconds()
        msg = message or f"Alarm at {time_str}"
        with _lock:
            conn = _ensure_conn_locked()
            conn.execute(
                "INSERT INTO reminders (fire_at, message, type, fired, created_at)"
                " VALUES (?, ?, 'alarm', 0, ?)",
                (time.time() + seconds, msg, time.time()),
            )
        return f"Alarm set for {time_str}: '{msg}'"
    except Exception as e:
        return f"Failed to set alarm: {e}"


def list_reminders():
    with _lock:
        conn = _ensure_conn_locked()
        now = time.time()
        rows = conn.execute(
            "SELECT fire_at, message FROM reminders"
            " WHERE fired = 0 AND fire_at > ? ORDER BY fire_at, id",
            (now,),
        ).fetchall()
    if not rows:
        return "No pending reminders or alarms."
    lines = []
    for r in rows:
        remaining = r["fire_at"] - now
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        lines.append(f"- \"{r['message']}\" in {mins}m {secs}s")
    return "Pending:\n" + "\n".join(lines)


def cancel_reminder(message_fragment=""):
    with _lock:
        conn = _ensure_conn_locked()
        if message_fragment:
            cur = conn.execute(
                "DELETE FROM reminders WHERE fired = 0"
                " AND instr(lower(message), lower(?)) > 0",
                (message_fragment,),
            )
        else:
            cur = conn.execute("DELETE FROM reminders WHERE fired = 0")
        removed = cur.rowcount
    return f"Cancelled {removed} reminder(s)." if removed else "Nothing to cancel."


def pending_snapshot():
    with _lock:
        conn = _ensure_conn_locked()
        rows = conn.execute(
            "SELECT id, fire_at, message, type FROM reminders"
            " WHERE fired = 0 ORDER BY fire_at, id"
        ).fetchall()
    return [
        {
            "id": r["id"],
            "fire_at": r["fire_at"],
            "message": r["message"],
            "type": r["type"],
        }
        for r in rows
    ]


def _check_reminders():
    with _lock:
        conn = _ensure_conn_locked()
        now = time.time()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id, fire_at, message, type FROM reminders"
                " WHERE fired = 0 AND fire_at <= ? ORDER BY fire_at, id",
                (now,),
            ).fetchall()
            conn.executemany(
                "UPDATE reminders SET fired = 1 WHERE id = ?",
                [(r["id"],) for r in rows],
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        due = [dict(r) for r in rows]
    for r in due:
        _fire(r)


def _fire(reminder):
    msg = reminder["message"]
    log.info("firing: %s", msg)
    callback = _fired_callback
    if callback is None:
        return
    loop = _main_loop
    if loop is not None:
        try:
            loop.call_soon_threadsafe(
                lambda m=msg: asyncio.ensure_future(callback(m))
            )
        except Exception:
            pass
    else:
        try:
            callback(msg)
        except Exception:
            pass


def start_checker():
    def loop():
        while True:
            time.sleep(1)
            _check_reminders()
    t = threading.Thread(target=loop, daemon=True)
    t.start()
