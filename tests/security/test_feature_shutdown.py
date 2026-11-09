"""Task 18: disabled risky features stay off at runtime."""

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

DISABLED_MODULES = (
    "core.approval",
    "core.net.netmsg",
    "core.updater",
    "core.license",
    "core.net.telegram_bot",
    "core.net.adb_controller",
    "core.net.cast_controller",
    "core.net.agent",
    "core.netdiscovery",
)

ORIGIN = {"origin": "http://127.0.0.1:8741"}


def test_disabled_modules_are_not_imported():
    repo_root = Path(__file__).parents[2]
    script = (
        "import sys\n"
        "import server\n"
        "from fastapi.testclient import TestClient\n"
        "with TestClient(server.app):\n"
        "    pass\n"
        "bad = [m for m in %r if m in sys.modules]\n"
        "if bad:\n"
        "    print('imported-disabled:', bad, file=sys.stderr)\n"
        "    sys.exit(1)\n"
        % (list(DISABLED_MODULES),)
    )
    with tempfile.TemporaryDirectory(prefix="jarvis-shutdown-") as data_dir:
        env = dict(os.environ)
        env["JARVIS_TEST_MODE"] = "1"
        env["JARVIS_DEV"] = "1"
        env["JARVIS_DATA_DIR"] = data_dir
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    assert result.returncode == 0, (
        "returncode=%s stdout=%s stderr=%s" % (result.returncode, result.stdout, result.stderr)
    )


def test_port_9998_is_not_open(client):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        assert sock.connect_ex(("127.0.0.1", 9998)) != 0
    finally:
        sock.close()


def test_update_command_does_not_exist(client, token):
    sys.modules.pop("core.updater", None)
    with client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"cmd": "auth", "token": token})
        for _ in range(12):
            message = ws.receive_json()
            if message.get("type") == "auth_ok":
                break
        else:
            raise AssertionError("auth_ok not received")
        ws.send_json({"cmd": "update_download"})
        error = None
        for _ in range(12):
            message = ws.receive_json()
            if message.get("type") == "error":
                error = message
                break
        assert error is not None, "update_download must produce an error frame"
    assert "core.updater" not in sys.modules


def test_license_endpoints_are_disabled(client, auth_headers):
    status = client.get("/api/license_status", headers=auth_headers)
    assert status.status_code == 404
    activate = client.post(
        "/api/license_activate", json={"key": "ABCD-EFGH-IJKL"}, headers=auth_headers
    )
    assert activate.status_code == 404


def test_auth_events_are_audited(client, token, auth_headers):
    import server

    with client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"cmd": "auth", "token": token})
        message = ws.receive_json()
        assert message["type"] == "auth_ok"

    with client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"cmd": "auth", "token": "wrong-token"})
        message = ws.receive_json()
        assert message["type"] == "auth_error"

    assert server.SECURITY is not None
    assert server.SECURITY.audit is not None
    rows = server.SECURITY.audit.recent(50)
    event_types = [row.get("event_type") for row in rows]
    assert "auth.success" in event_types
    assert "session.open" in event_types
    failures = [row for row in rows if row.get("event_type") == "auth.failure"]
    assert failures, "expected an auth.failure audit row"
    assert any(row.get("error_code") == "credentials" for row in failures)

    # The supplied tokens must never appear in the audit trail.
    blob = json.dumps(rows, default=str)
    assert token not in blob
    assert "wrong-token" not in blob
