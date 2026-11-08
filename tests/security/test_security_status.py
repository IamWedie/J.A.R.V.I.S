import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from core.security import keystore
from core.security.audit import AuditLog
from core.security.runtime import build_runtime

REPO_ROOT = Path(__file__).parents[2]


def _locked_runtime(tmp_path, monkeypatch):
    """Build a real audit-locked runtime in tmp_path and install it in server."""
    import server

    (tmp_path / "audit.key").write_text("not a dpapi blob", encoding="utf-8")
    args = dict(server.SECURITY_BUILD_ARGS)
    args.update({"data_dir": str(tmp_path), "frozen": False, "dev_mode": True})
    runtime = build_runtime(**args)
    assert runtime.status == "audit_locked"
    assert runtime.audit is None
    monkeypatch.setattr(server, "SECURITY", runtime)
    monkeypatch.setattr(server, "SECURITY_BUILD_ARGS", args)
    monkeypatch.setattr(server, "COMMAND_ROUTER", server.COMMAND_ROUTER)
    monkeypatch.setattr(server.brain, "registry", server.brain.registry)
    monkeypatch.setattr(server.brain, "broker", server.brain.broker)
    return runtime, args


def _run_script(name, *args):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / name), *map(str, args)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_status_requires_token(client):
    assert client.get("/api/security/status").status_code == 401


def test_status_returns_runtime_state_and_recent_audit(client, auth_headers):
    response = client.get("/api/security/status", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in {"ok", "audit_locked", "manifest_error", "runtime_error"}
    assert isinstance(data["tools"], list)
    assert isinstance(data["recent_audit"], list)
    assert {
        "phone", "cast", "lan_messaging", "telegram", "license_server", "auto_update",
    } <= set(data["disabled_features"])


def test_audit_reset_requires_confirm(client, auth_headers, tmp_path, monkeypatch):
    runtime, _ = _locked_runtime(tmp_path, monkeypatch)
    response = client.post(
        "/api/security/audit_reset", headers=auth_headers, json={"confirm": False}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "confirmation_required"
    assert not (tmp_path / "audit_archive").exists()
    assert runtime.status == "audit_locked"


def test_audit_reset_archives_old_db(client, auth_headers, tmp_path, monkeypatch):
    import server

    (tmp_path / "audit.db").write_bytes(b"old-chain")
    locked, _ = _locked_runtime(tmp_path, monkeypatch)
    response = client.post(
        "/api/security/audit_reset", headers=auth_headers, json={"confirm": True}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["status"] in {"ok", "manifest_error"}

    archive = tmp_path / "audit_archive"
    archived_db = list(archive.glob("audit-*.db"))
    archived_key = list(archive.glob("audit-*.key"))
    assert len(archived_db) == 1
    assert archived_db[0].read_bytes() == b"old-chain"
    assert len(archived_key) == 1
    assert (tmp_path / "audit.key").exists()
    assert (tmp_path / "audit.db").exists()

    marker = json.loads((tmp_path / "audit_recovery.json").read_text(encoding="utf-8"))
    assert marker["reset_event_id"] >= 1

    new_runtime = server.SECURITY
    try:
        assert new_runtime is not locked
        events = new_runtime.audit.recent(20)
        assert any(event["event_type"] == "audit.reset" for event in events)
    finally:
        audit = getattr(new_runtime, "audit", None)
        if audit is not None:
            audit.close()


def test_grants_list_requires_token(client):
    assert client.get("/api/security/grants").status_code == 401


def test_grant_can_be_revoked(client, auth_headers):
    import server

    runtime = server.SECURITY
    assert runtime is not None and runtime.status == "ok"
    spec = runtime.registry.get("search_files")
    assert spec is not None
    scope = str(Path.home() / "Documents")
    grant_id = runtime.policy.add_grant(spec, scope, "test")
    try:
        response = client.get("/api/security/grants", headers=auth_headers)
        assert response.status_code == 200
        grants = response.json()["grants"]
        mine = [grant for grant in grants if grant["grant_id"] == grant_id]
        assert len(mine) == 1
        assert set(mine[0]) == {
            "grant_id", "tool", "scope", "created_at", "expires_at", "source",
        }
        assert mine[0]["tool"] == "search_files"

        response = client.post(
            f"/api/security/grants/{grant_id}/revoke", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}

        grants = client.get("/api/security/grants", headers=auth_headers).json()["grants"]
        assert all(grant["grant_id"] != grant_id for grant in grants)
        changes = [
            event for event in runtime.audit.recent(50)
            if event["event_type"] == "policy.change"
        ]
        assert any(
            event["decision"] == "revoke" and event["tool"] == "search_files"
            for event in changes
        )
    finally:
        if any(grant.grant_id == grant_id for grant in runtime.policy.list_grants()):
            runtime.policy.revoke_grant(grant_id)


def test_audit_verify_script_detects_tampering(tmp_path):
    key = keystore.load_or_create_key(tmp_path / "audit.key")
    log = AuditLog(tmp_path / "audit.db", key)
    log.append("auth.success", source="test")
    log.close()

    good = _run_script(
        "audit_verify.py", "--db", tmp_path / "audit.db", "--key", tmp_path / "audit.key"
    )
    assert good.returncode == 0, good.stderr

    connection = sqlite3.connect(tmp_path / "audit.db")
    connection.execute("UPDATE audit SET event_type='auth.failure' WHERE id=1")
    connection.commit()
    connection.close()

    bad = _run_script(
        "audit_verify.py", "--db", tmp_path / "audit.db", "--key", tmp_path / "audit.key"
    )
    assert bad.returncode != 0
    assert "verification failed" in (bad.stdout + bad.stderr).lower()


def test_audit_export_script_writes_ndjson(tmp_path):
    key = keystore.load_or_create_key(tmp_path / "audit.key")
    log = AuditLog(tmp_path / "audit.db", key)
    log.append("auth.success", source="test")
    log.close()

    out = tmp_path / "export.ndjson"
    result = _run_script(
        "audit_export.py",
        "--db", tmp_path / "audit.db",
        "--key", tmp_path / "audit.key",
        "--out", out,
    )
    assert result.returncode == 0, result.stderr
    lines = [
        json.loads(line)
        for line in out.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(lines) == 1
    assert lines[0]["event_type"] == "auth.success"
