"""JARVIS license server (local / self-hosted) — FastAPI.

Runs the online layer for the JARVIS license system:
  - Verifies Ed25519 signatures of license keys (using the app's public keyring).
  - Tracks issued keys and their active device instances in SQLite.
  - Enforces an activation limit per key (anti-sharing).
  - Lets you mint new keys via the private key (admin endpoints).

Run (from the repo root):
    python -m uvicorn license_server.main:app --port 8765
"""
import os
import sqlite3
import sys
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Allow running as a module or directly.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from core import license as lic            # noqa: E402
from core import license_keys as lk        # noqa: E402

app = FastAPI(title="JARVIS License Server")

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("LICENSE_DB", os.path.join(SERVER_DIR, "licenses.db"))
PRIV_PATH = os.environ.get(
    "LICENSE_PRIV",
    os.path.join(_REPO, "scripts", "keys", "license_priv.pem"),
)
ACTIVATION_LIMIT = int(os.environ.get("LICENSE_ACTIVATION_LIMIT", "3"))

# Public keys the server will accept (from the app's embedded keyring).
PUBKEYS = lk.load_keyring(os.path.join(_REPO, "core", "pubkeys.json"))


class LicenseBody(BaseModel):
    license_key: str
    instance_name: str = "default"


class DeactivateBody(BaseModel):
    license_key: str
    instance_id: str = ""


class MintBody(BaseModel):
    count: int = 1
    expiry_days: int = 0
    flags: int = 0


# ─────────────────────────── DB helpers ───────────────────────────

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS licenses (
            key_id TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL,
            expiry_days INTEGER NOT NULL DEFAULT 0,
            flags INTEGER NOT NULL DEFAULT 0,
            revoked INTEGER NOT NULL DEFAULT 0
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS activations (
            key_id TEXT NOT NULL,
            instance_id TEXT PRIMARY KEY,
            instance_name TEXT NOT NULL,
            activated_at INTEGER NOT NULL
        )"""
    )
    conn.commit()
    conn.close()


_init_db()


def _key_id_hex(key):
    try:
        _payload, _sig = lk.parse_key(key)
        info = lk.parse_payload(_payload)
        return info["key_id"].hex()
    except Exception:
        return None


def _load_private():
    if not os.path.exists(PRIV_PATH):
        raise RuntimeError(f"private key not found: {PRIV_PATH}")
    with open(PRIV_PATH, "r", encoding="utf-8") as f:
        return f.read()


# ─────────────────────────── endpoints ───────────────────────────

@app.get("/health")
def health():
    return {"ok": True}


def _validate_key(license_key):
    """Return (key_id_hex, info) if valid, else raise HTTPException."""
    ok, reason, info = lk.verify_key(license_key, PUBKEYS)
    if not ok:
        raise HTTPException(status_code=400, detail=reason or "invalid license")
    key_id = info["key_id"].hex()
    conn = _db()
    row = conn.execute(
        "SELECT * FROM licenses WHERE key_id = ?", (key_id,)
    ).fetchone()
    conn.close()
    if row is not None and row["revoked"]:
        raise HTTPException(status_code=400, detail="license revoked")
    return key_id, info


@app.post("/license/validate")
def validate(body: LicenseBody):
    key_id, info = _validate_key(body.license_key)
    conn = _db()
    activation_count = conn.execute(
        "SELECT COUNT(*) AS c FROM activations WHERE key_id = ?", (key_id,)
    ).fetchone()["c"]
    conn.close()
    return {
        "valid": True,
        "key_id": key_id,
        "expiry_days": info["expiry_days"],
        "flags": info["flags"],
        "activation_usage": activation_count,
        "activation_limit": ACTIVATION_LIMIT,
    }


@app.post("/license/activate")
def activate(body: LicenseBody):
    key_id, info = _validate_key(body.license_key)
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO licenses (key_id, created_at, expiry_days, flags) "
        "VALUES (?, ?, ?, ?)",
        (key_id, int(time.time()), info["expiry_days"], info["flags"]),
    )
    activation_count = conn.execute(
        "SELECT COUNT(*) AS c FROM activations WHERE key_id = ?", (key_id,)
    ).fetchone()["c"]
    if activation_count >= ACTIVATION_LIMIT:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=f"activation limit reached ({ACTIVATION_LIMIT})",
        )
    instance_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO activations (key_id, instance_id, instance_name, activated_at) "
        "VALUES (?, ?, ?, ?)",
        (key_id, instance_id, body.instance_name or "default", int(time.time())),
    )
    conn.commit()
    conn.close()
    return {"valid": True, "instance_id": instance_id}


@app.post("/license/deactivate")
def deactivate(body: DeactivateBody):
    key_id = _key_id_hex(body.license_key)
    if not key_id:
        raise HTTPException(status_code=400, detail="invalid license key")
    conn = _db()
    if body.instance_id:
        conn.execute(
            "DELETE FROM activations WHERE key_id = ? AND instance_id = ?",
            (key_id, body.instance_id),
        )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/admin/mint")
def admin_mint(body: MintBody):
    """Mint new keys on the server (requires the private key)."""
    try:
        priv = _load_private()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    keys = [
        lic.generate_license(priv, flags=body.flags, expiry_days=body.expiry_days)
        for _ in range(max(1, body.count))
    ]
    return {"keys": keys}
