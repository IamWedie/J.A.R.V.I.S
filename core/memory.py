import json
import os
import sqlite3
import threading
from datetime import datetime

from core.voiceid import data_dir

DB_PATH = os.path.join(data_dir(), "memory.db")
_lock = threading.Lock()
_local = threading.local()


def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _connect()
    return _local.conn


SCHEMA_VERSION = 2


def _init():
    conn = _connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            user TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            topic TEXT NOT NULL DEFAULT '',
            fact TEXT NOT NULL,
            user TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS devices (
            ip TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_conv_ts ON conversations(ts);
        CREATE INDEX IF NOT EXISTS idx_facts_topic ON facts(topic);
        CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user);
        """
    )
    conn.commit()
    row = conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
    current = int(row["value"]) if row else 1
    if current < SCHEMA_VERSION:
        _migrate(conn, current, SCHEMA_VERSION)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    conn.close()


def _migrate(conn, from_ver, to_ver):
    if from_ver < 2:
        for table, col in (("facts", "topic"), ("conversations", "user"), ("facts", "user")):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            except Exception:
                pass


_init()


def save_devices(devices):
    now = _now()
    with _lock:
        conn = _get_conn()
        for d in devices:
            conn.execute(
                "INSERT INTO devices (ip, data, last_seen) VALUES (?, ?, ?) "
                "ON CONFLICT(ip) DO UPDATE SET data=excluded.data, last_seen=excluded.last_seen",
                (d["ip"], json.dumps(d), now),
            )
        conn.commit()


def known_devices():
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT data, last_seen FROM devices ORDER BY ip").fetchall()
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


def log(role, text, user=""):
    text = str(text).strip()
    if not text:
        return
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO conversations (ts, role, text, user) VALUES (?, ?, ?, ?)",
            (_now(), role, text, str(user or "")),
        )
        conn.commit()


def recent_conversations(limit=14):
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT ts, role, text, user FROM conversations ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{"ts": r["ts"], "role": r["role"], "text": r["text"], "user": r["user"] or ""} for r in reversed(rows)]


def search_conversations(query, limit=6):
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT ts, role, text FROM conversations WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
    return [{"ts": r["ts"], "role": r["role"], "text": r["text"]} for r in rows]


def add_fact(fact, user=""):
    fact = str(fact).strip()
    if not fact:
        return False
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO facts (ts, topic, fact, user) VALUES (?, '', ?, ?)",
                (_now(), fact, str(user or "")),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return False
    return True


def upsert_fact(topic, fact, user=""):
    topic = str(topic).strip().lower()
    fact = str(fact).strip()
    user = str(user or "").strip().lower()
    if not fact:
        return False
    with _lock:
        conn = _get_conn()
        existing = conn.execute(
            "SELECT id FROM facts WHERE topic = ? AND user = ?", (topic, user)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE facts SET fact = ?, ts = ? WHERE id = ?",
                (fact, _now(), existing["id"]),
            )
        else:
            try:
                conn.execute(
                    "INSERT INTO facts (ts, topic, fact, user) VALUES (?, ?, ?, ?)",
                    (_now(), topic, fact, user),
                )
            except sqlite3.IntegrityError:
                return False
        conn.commit()
    return True


def find_facts_by_topic(topic, user=""):
    topic = str(topic).strip().lower()
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT fact FROM facts WHERE topic = ? AND user = ? ORDER BY id DESC",
            (topic, str(user or "")),
        ).fetchall()
    return [r["fact"] for r in rows]


def remove_fact(substring):
    with _lock:
        conn = _get_conn()
        cur = conn.execute("DELETE FROM facts WHERE fact LIKE ?", (f"%{substring}%",))
        conn.commit()
        n = cur.rowcount
    return n


def list_facts():
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT fact FROM facts ORDER BY id DESC").fetchall()
    return [r["fact"] for r in rows]


def _tokens(text):
    return set(w for w in str(text).lower().split() if len(w) > 3)


def relevant_facts(user_text, limit=8, user=None):
    facts = list_facts()
    if not facts:
        return []
    if len(facts) <= limit:
        return facts
    q = _tokens(user_text)
    uname = str(user or "").strip().lower()
    scored = []
    for f in list_facts_with_users():
        overlap = len(q & _tokens(f["fact"]))
        bonus = 0
        if uname and f["user"] == uname:
            bonus = 10
        elif f["user"]:
            bonus = -5
        scored.append((overlap + bonus, f["fact"]))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:limit]]


def list_facts_with_users():
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT fact, user FROM facts ORDER BY id DESC").fetchall()
    return [{"fact": r["fact"], "user": r["user"] or ""} for r in rows]


def wipe_memory():
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM conversations")
        conn.execute("DELETE FROM facts")
        conn.execute("VACUUM")
        conn.commit()


def stats():
    with _lock:
        conn = _get_conn()
        c = conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]
        f = conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"]
    return {"conversations": c, "facts": f}


import re as _re

_PATTERNS = [
    (_re.compile(r"\bmy\s+(name|nickname)\s+(?:is|'s)\s+(.+?)[\.\!\?\,]", _re.I), "name"),
    (_re.compile(r"\b(?:i(?:'m| am)|this is)\s+(.+?)[\.\!\?\,]", _re.I), "name"),
    (_re.compile(r"\bmy\s+(phone|number|cell)\s+(?:is|'s)\s+(.+?)[\.\!\?\,]", _re.I), "phone"),
    (_re.compile(r"\bmy\s+(email|mail)\s+(?:is|'s)\s+(.+?)[\.\!\?\,]", _re.I), "email"),
    (_re.compile(r"\bmy\s+(address|location|live(?:s|ing)?)\s+(?:is|at|in)?\s*(.+?)[\.\!\?\,]", _re.I), "address"),
    (_re.compile(r"\bi\s+(?:work|work(?:s|ing))\s+(?:at|for|in)\s+(.+?)[\.\!\?\,]", _re.I), "work"),
    (_re.compile(r"\bmy\s+(?:job|work|profession)\s+(?:is|'s)\s+(.+?)[\.\!\?\,]", _re.I), "work"),
    (_re.compile(r"\bi\s+(?:study|study(?:s|ing))\s+(.+?)[\.\!\?\,]", _re.I), "study"),
    (_re.compile(r"\bmy\s+(?:fav(?:ou?rite)?|pref(?:er)?)\s+(\w+)\s+(?:is|'s)\s+(.+?)[\.\!\?\,]", _re.I), "preference"),
    (_re.compile(r"\bi\s+(?:like|love|enjoy|hate|prefer)\s+(.+?)[\.\!\?\,]", _re.I), "preference"),
    (_re.compile(r"\bmy\s+(?:birthday|born)\s+(?:is|on)?\s*(.+?)[\.\!\?\,]", _re.I), "birthday"),
    (_re.compile(r"\bi\s+(?:have|have got)\s+(?:a|an|the)?\s*(.+?)[\.\!\?\,]", _re.I), "possession"),
    (_re.compile(r"\bi\s+(?:drive|drive(?:s)?)\s+(?:a|an)?\s*(.+?)[\.\!\?\,]", _re.I), "vehicle"),
    (_re.compile(r"\bi\s+(?:speak|speak(?:s|ing)?|know)\s+(.+?[\.\!\?\,])", _re.I), "language"),
]


def extract_facts(text):
    text = str(text).strip()
    if not text or len(text) < 10:
        return []
    facts = []
    for pattern, default_topic in _PATTERNS:
        for m in pattern.finditer(text):
            groups = [g.strip() for g in m.groups() if g]
            if len(groups) >= 2:
                topic = groups[0].lower().replace(" ", "_")
                fact = groups[1].strip().rstrip(".!?")
            elif len(groups) == 1:
                topic = default_topic
                fact = groups[0].strip().rstrip(".!?")
            else:
                continue
            if len(fact) > 3 and len(fact) < 200:
                facts.append((topic, f"{topic}: {fact}"))
    return facts


def auto_learn(user_text, assistant_text, user=""):
    combined = f"{user_text} {assistant_text}"
    facts = extract_facts(combined)
    added = 0
    for topic, fact in facts:
        if upsert_fact(topic, fact, user=user):
            added += 1
    return added
