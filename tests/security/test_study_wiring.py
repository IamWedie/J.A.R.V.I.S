import json

import pytest

from core.security.auth import LaunchToken, SessionManager
import server


class FakeStudyService:
    def __init__(self):
        self.calls = []

    async def start_scan(self, session_id, msg):
        self.calls.append(("start_scan", session_id, msg))

    async def start_generate(self, session_id, msg):
        self.calls.append(("start_generate", session_id, msg))

    async def list_decks(self, session_id, msg):
        self.calls.append(("list_decks", session_id, msg))

    async def get_deck(self, session_id, msg):
        self.calls.append(("get_deck", session_id, msg))

    async def delete_deck(self, session_id, msg):
        self.calls.append(("delete_deck", session_id, msg))


@pytest.mark.asyncio
async def test_server_actions_delegate_to_the_study_service(monkeypatch):
    service = FakeStudyService()
    monkeypatch.setattr(server, "STUDY_SERVICE", service)
    monkeypatch.setattr(server, "_active_session_id", lambda: "s1")
    actions = server.ServerActions()
    messages = {
        "study_scan": {"cmd": "study_scan", "op_id": "scan-1"},
        "study_generate": {"cmd": "study_generate", "scan_id": "scan-1"},
        "study_list": {"cmd": "study_list"},
        "study_get": {"cmd": "study_get", "deck_id": "deck-1"},
        "study_delete": {"cmd": "study_delete", "deck_id": "deck-1", "confirm": True},
    }
    for name, msg in messages.items():
        await getattr(actions, name)(msg)
    assert service.calls == [
        ("start_scan", "s1", messages["study_scan"]),
        ("start_generate", "s1", messages["study_generate"]),
        ("list_decks", "s1", messages["study_list"]),
        ("get_deck", "s1", messages["study_get"]),
        ("delete_deck", "s1", messages["study_delete"]),
    ]


@pytest.mark.asyncio
async def test_server_actions_report_a_missing_study_service(monkeypatch):
    events = []

    async def fake_send(event):
        events.append(event)

    monkeypatch.setattr(server, "STUDY_SERVICE", None)
    monkeypatch.setattr(server, "send_event", fake_send)
    actions = server.ServerActions()
    await actions.study_scan({"op_id": "op-scan"})
    await actions.study_list({"op_id": "op-list"})
    assert events == [
        {
            "type": "study_error",
            "op_id": "op-scan",
            "stage": "scan",
            "code": "study_extract_failed",
            "message": "The study workspace is not available.",
        },
        {
            "type": "study_error",
            "op_id": "op-list",
            "stage": "decks",
            "code": "study_db_error",
            "message": "The study workspace is not available.",
        },
    ]


@pytest.mark.asyncio
async def test_study_cancel_is_best_effort(monkeypatch):
    cancelled = []

    class Service:
        async def cancel(self, session_id):
            cancelled.append(session_id)

    monkeypatch.setattr(server, "STUDY_SERVICE", Service())
    await server._study_cancel("s1")
    assert cancelled == ["s1"]

    class Broken:
        async def cancel(self, session_id):
            raise RuntimeError("boom")

    monkeypatch.setattr(server, "STUDY_SERVICE", Broken())
    await server._study_cancel("s2")

    monkeypatch.setattr(server, "STUDY_SERVICE", None)
    await server._study_cancel("s3")


@pytest.mark.asyncio
async def test_close_session_cancels_study_work(monkeypatch):
    sessions = SessionManager()
    monkeypatch.setattr(server, "SESSIONS", sessions)
    monkeypatch.setattr(server, "SECURITY", None)
    cancelled = []

    async def fake_cancel(session_id):
        cancelled.append(session_id)

    monkeypatch.setattr(server, "_study_cancel", fake_cancel)
    session = sessions.create(object())
    sessions.activate(session)

    await server._close_ws_session(session)

    assert cancelled == [session.session_id]
    assert sessions.is_active(session.session_id) is False


class FakeWS:
    def __init__(self, token):
        self.headers = {"origin": "http://127.0.0.1:8741"}
        self.messages = [json.dumps({"cmd": "auth", "token": token})]
        self.sent = []
        self.closed = []

    async def receive_text(self):
        return self.messages.pop(0)

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code=1000):
        self.closed.append(code)


@pytest.mark.asyncio
async def test_session_replacement_cancels_previous_study_work(monkeypatch):
    sessions = SessionManager()
    monkeypatch.setattr(server, "SESSIONS", sessions)
    monkeypatch.setattr(server, "SECURITY", None)
    monkeypatch.setattr(server, "LAUNCH_TOKEN", LaunchToken("test-token"))
    monkeypatch.setattr(server, "REPLACEMENT_GRACE_SECONDS", 0)
    cancelled = []

    async def fake_cancel(session_id):
        cancelled.append(session_id)

    monkeypatch.setattr(server, "_study_cancel", fake_cancel)

    first = await server._ws_handshake(FakeWS("test-token"))
    second = await server._ws_handshake(FakeWS("test-token"))

    assert first is not None and second is not None
    assert cancelled == [first.session_id]
    assert sessions.is_active(second.session_id) is True
    assert first.revoked_at is not None


@pytest.mark.asyncio
async def test_install_security_replaces_and_cancels_the_study_service(monkeypatch):
    class OldService:
        def __init__(self):
            self.cancelled = 0

        async def cancel_all(self):
            self.cancelled += 1

    old = OldService()
    monkeypatch.setattr(server, "STUDY_SERVICE", old)
    monkeypatch.setattr(server, "SECURITY", None)
    monkeypatch.setattr(server, "COMMAND_ROUTER", None)
    monkeypatch.setattr(server.brain, "registry", server.brain.registry)
    monkeypatch.setattr(server.brain, "broker", server.brain.broker)
    runtime = type(
        "Runtime",
        (),
        {"registry": None, "broker": None, "audit": None, "approvals": None},
    )()

    await server._install_security(runtime)

    assert old.cancelled == 1
    assert server.STUDY_SERVICE is not old
    assert server.STUDY_SERVICE is not None
    await server.STUDY_SERVICE.cancel_all()
