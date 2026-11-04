import pytest
from starlette.websockets import WebSocketDisconnect

ORIGIN = {"origin": "http://127.0.0.1:8741"}

def receive_until(ws, message_type, limit=12):
    for _ in range(limit):
        message = ws.receive_json()
        if message.get("type") == message_type:
            return message
    raise AssertionError(f"did not receive {message_type}")

def test_ws_rejects_missing_origin(client, token):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"cmd": "auth", "token": token})
            ws.receive_json()
    assert exc.value.code == 1008

def test_ws_rejects_cross_origin(client, token):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers={"origin": "http://evil.test"}) as ws:
            ws.send_json({"cmd": "auth", "token": token})
            ws.receive_json()
    assert exc.value.code == 1008

def test_ws_rejects_wrong_token(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            for _ in range(3):
                ws.send_json({"cmd": "auth", "token": "wrong"})
            for _ in range(3):
                assert ws.receive_json()["type"] == "auth_error"
            ws.receive_json()
    assert exc.value.code == 1008

def test_ws_accepts_valid_token(client, token):
    with client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"cmd": "auth", "token": token})
        message = ws.receive_json()
        assert message["type"] == "auth_ok"
        assert message["protocol"] == 1
        assert message["session_id"]

def test_ws_rejects_command_before_auth(client, token):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            for _ in range(3):
                ws.send_json({"cmd": "chat", "text": "hello"})
            for _ in range(3):
                assert ws.receive_json()["type"] == "auth_error"
            ws.receive_json()
    assert exc.value.code == 1008

def test_second_session_revokes_first(client, token):
    with client.websocket_connect("/ws", headers=ORIGIN) as first:
        first.send_json({"cmd": "auth", "token": token})
        assert first.receive_json()["type"] == "auth_ok"
        with client.websocket_connect("/ws", headers=ORIGIN) as second:
            second.send_json({"cmd": "auth", "token": token})
            assert second.receive_json()["type"] == "auth_ok"
            revoked = receive_until(first, "session_revoked")
            assert revoked["type"] == "session_revoked"

def test_ui_authenticates_before_commands():
    from pathlib import Path
    js = (Path(__file__).parents[2] / "ui" / "app.js").read_text(encoding="utf-8")
    assert '"cmd": "auth"' in js or "'cmd': 'auth'" in js or "cmd: 'auth'" in js
    assert "auth_ok" in js
