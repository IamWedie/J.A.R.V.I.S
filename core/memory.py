import json
import os
import sqlite3
import threading
from datetime import datetime

from core.voiceid import data_dir

DB_PATH = os.path.join(data_dir(), "memory.db")
_lock = threading.Lock()


def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init():
    conn = _connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            fact TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS devices (
            ip TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_conv_ts ON conversations(ts);
        """
    )
    conn.commit()
    conn.close()


_init()


def save_devices(devices):
    now = _now()
    with _lock:
        conn = _connect()
        for d in devices:
            conn.execute(
                "INSERT INTO devices (ip, data, last_seen) VALUES (?, ?, ?) "
                "ON CONFLICT(ip) DO UPDATE SET data=excluded.data, last_seen=excluded.last_seen",
                (d["ip"], json.dumps(d), now),
            )
        conn.commit()
        conn.close()


def known_devices():
    with _lock:
        conn = _connect()
        rows = conn.execute("SELECT data, last_seen FROM devices ORDER BY ip").fetchall()
        conn.close()
    out = []
    for r in rows:
        try:
            d = json.loads(r["data"])
            d["last_seen"] = r["last_seen"]
            out.append(d)
        except Exception:
            continue
    return out


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(role, text):
    text = str(text).strip()
    if not text:
        return
    with _lock:
        conn = _connect()
        conn.execute("INSERT INTO conversations (ts, role, text) VALUES (?, ?, ?)", (_now(), role, text))
        conn.commit()
        conn.close()


def recent_conversations(limit=14):
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT ts, role, text FROM conversations ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
    return [{"ts": r["ts"], "role": r["role"], "text": r["text"]} for r in reversed(rows)]


def search_conversations(query, limit=6):
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT ts, role, text FROM conversations WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        conn.close()
    return [{"ts": r["ts"], "role": r["role"], "text": r["text"]} for r in rows]


def add_fact(fact):
    fact = str(fact).strip()
    if not fact:
        return False
    with _lock:
        conn = _connect()
        try:
            conn.execute("INSERT INTO facts (ts, fact) VALUES (?, ?)", (_now(), fact))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return False
        conn.close()
    return True


def remove_fact(substring):
    with _lock:
        conn = _connect()
        cur = conn.execute("DELETE FROM facts WHERE fact LIKE ?", (f"%{substring}%",))
        conn.commit()
        n = cur.rowcount
        conn.close()
    return n


def list_facts():
    with _lock:
        conn = _connect()
        rows = conn.execute("SELECT fact FROM facts ORDER BY id DESC").fetchall()
        conn.close()
    return [r["fact"] for r in rows]


def _tokens(text):
    return set(w for w in str(text).lower().split() if len(w) > 3)


def relevant_facts(user_text, limit=8):
    facts = list_facts()
    if len(facts) <= limit:
        return facts
    q = _tokens(user_text)
    scored = []
    for f in facts:
        overlap = len(q & _tokens(f))
        scored.append((overlap, f))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:limit]]


def wipe_memory():
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM conversations")
        conn.execute("DELETE FROM facts")
        conn.commit()
        conn.close()


def stats():
    with _lock:
        conn = _connect()
        c = conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]
        f = conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"]
        conn.close()
    return {"conversations": c, "facts": f}
