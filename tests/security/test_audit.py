import os
import sqlite3

import pytest

from core.security.audit import AuditLog, hash_args, redact_args
from core.security import keystore

KEY = os.urandom(32)

def make_log(tmp_path, key=KEY):
    return AuditLog(tmp_path / "audit.db", key)

def test_append_and_verify_chain(tmp_path):
    log = make_log(tmp_path)
    log.append("auth.success", session_id="s1", source="ui")
    log.append("tool.request", tool="get_volume", args={"x": 1})
    assert log.verify_chain() is True
    log.close()

def test_tampering_breaks_chain(tmp_path):
    log = make_log(tmp_path)
    log.append("auth.success", session_id="s1")
    log.close()
    conn = sqlite3.connect(tmp_path / "audit.db")
    conn.execute("UPDATE audit SET event_type='auth.failure' WHERE id=1")
    conn.commit()
    conn.close()
    log = make_log(tmp_path)
    assert log.verify_chain() is False
    log.close()

def test_wrong_key_breaks_verification(tmp_path):
    log = make_log(tmp_path)
    log.append("auth.success")
    log.close()
    log = AuditLog(tmp_path / "audit.db", os.urandom(32))
    assert log.verify_chain() is False
    log.close()

def test_secret_arguments_are_redacted():
    args = {"api_key": "sk-secret-value", "path": "C:/notes.txt", "text": "x" * 500}
    redacted = redact_args(args)
    assert "sk-secret-value" not in redacted
    assert "notes.txt" in redacted
    assert len(redacted) < 400
    assert hash_args(args) == hash_args({"text": "x" * 500, "path": "C:/notes.txt",
                                         "api_key": "sk-secret-value"})

def test_key_file_round_trip(tmp_path):
    path = tmp_path / "audit.key"
    first = keystore.load_or_create_key(path)
    second = keystore.load_or_create_key(path)
    assert first == second
    assert len(first) == 32

def test_corrupt_key_file_raises(tmp_path):
    path = tmp_path / "audit.key"
    path.write_text("not a dpapi blob", encoding="utf-8")
    with pytest.raises(keystore.KeyStoreError):
        keystore.load_or_create_key(path)

def test_prune_keeps_chain_verifiable(tmp_path):
    log = AuditLog(tmp_path / "audit.db", KEY, retention_days=0)
    log.append("auth.success")
    log.append("auth.success")
    removed = log.prune(now=10**12)
    assert removed >= 1
    assert log.verify_chain() is True
    log.close()

def test_reconcile_marks_incomplete_tool_request(tmp_path):
    log = make_log(tmp_path)
    log.append("tool.request", tool="get_volume", request_id="r1")
    assert log.reconcile_incomplete() == 1
    recent = log.recent(5)
    assert recent[-1]["event_type"] == "tool.error"
    assert recent[-1]["error_code"] == "unknown_result"
    assert log.verify_chain() is True
    log.close()

def test_export_is_newline_delimited_json(tmp_path):
    log = make_log(tmp_path)
    log.append("auth.success", session_id="s1")
    out = tmp_path / "audit-export.ndjson"
    assert log.export(out) == 1
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert '"event_type":"auth.success"' in lines[0]
    assert log.verify_chain() is True
    log.close()

def test_recent_returns_newest_last(tmp_path):
    log = make_log(tmp_path)
    log.append("auth.success", session_id="s1")
    log.append("auth.failure", session_id="s2")
    assert [row["event_type"] for row in log.recent(2)] == [
        "auth.success", "auth.failure",
    ]
    log.close()
