"""Task 6: policy-gated router for non-tool WebSocket commands.

Every test drives a real ``AuditLog`` in ``tmp_path`` (except the audit-failure
test, which uses a stand-in whose writes always raise) so the
``command.request`` / ``command.result`` pairing is exercised for real.
"""
import time

import pytest

from core.security.audit import AuditLog
from core.security.auth import Session
from core.security.commands import (
    COMMAND_POLICY,
    REMOVED_COMMANDS,
    CommandRouter,
)


class FakeActions:
    """Records every dispatched action instead of running server logic."""

    def __init__(self):
        self.calls = []

    def _record(self, name, msg):
        self.calls.append((name, msg))

    async def start_listen(self, msg):
        self._record("start_listen", msg)

    async def abort(self, msg):
        self._record("abort", msg)

    async def chat(self, msg):
        self._record("chat", msg)

    async def set_model(self, msg):
        self._record("set_model", msg)

    async def reset(self, msg):
        self._record("reset", msg)

    async def wake_toggle(self, msg):
        self._record("wake_toggle", msg)

    async def tts_settings(self, msg):
        self._record("tts_settings", msg)

    async def tts_test(self, msg):
        self._record("tts_test", msg)

    async def enroll(self, msg):
        self._record("enroll", msg)

    async def voice_reset(self, msg):
        self._record("voice_reset", msg)

    async def wipe_memory(self, msg):
        self._record("wipe_memory", msg)

    async def set_language(self, msg):
        self._record("set_language", msg)

    async def unknown(self, msg):
        self._record("unknown", msg)

    @property
    def names(self):
        return [name for name, _ in self.calls]


class FakeApprovalPort:
    """Approval port stub; records (session_id, request_id, decision)."""

    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def resolve(self, session_id, request_id, decision):
        self.calls.append((session_id, request_id, decision))
        return self.result


class BrokenAudit:
    """Audit stand-in whose writes always fail (locked/absent store)."""

    def append(self, *args, **kwargs):
        raise RuntimeError("audit write failed")


@pytest.fixture
def audit(tmp_path):
    log = AuditLog(tmp_path / "audit.db", hmac_key=b"0" * 32)
    yield log
    log.close()


@pytest.fixture
def session():
    return Session(session_id="s1", websocket=None, created_at=time.time())


@pytest.fixture
def actions():
    return FakeActions()


def make_router(audit, actions=None, approval_port=None):
    return CommandRouter(
        audit,
        approval_port if approval_port is not None else FakeApprovalPort(),
        actions if actions is not None else FakeActions(),
    )


def event_types(log):
    return [row["event_type"] for row in log.recent(10)]


@pytest.mark.asyncio
async def test_chat_dispatches(audit, session, actions):
    router = make_router(audit, actions)
    msg = {"cmd": "chat", "text": "hello"}
    result = await router.handle(session, msg)
    assert result.ok is True
    assert result.error_code is None
    assert actions.calls == [("chat", msg)]
    assert event_types(audit) == ["command.request", "command.result"]


@pytest.mark.asyncio
async def test_destructive_command_requires_confirm(audit, session, actions):
    router = make_router(audit, actions)
    result = await router.handle(session, {"cmd": "wipe_memory"})
    assert result.ok is False
    assert result.error_code == "confirm_required"
    assert actions.names == []
    # The refusal is visible in the audit log.
    last = audit.recent(5)[-1]
    assert last["event_type"] == "command.result"
    assert last["result_status"] == "denied"
    assert last["error_code"] == "confirm_required"

    # An explicit, boolean True confirmation dispatches the command.
    result = await router.handle(session, {"cmd": "wipe_memory", "confirm": True})
    assert result.ok is True
    assert actions.names == ["wipe_memory"]


@pytest.mark.asyncio
async def test_update_download_is_rejected(audit, session, actions):
    assert "update_download" in REMOVED_COMMANDS
    router = make_router(audit, actions)
    for msg in ({"cmd": "update_download"}, {"cmd": "update_download", "confirm": True}):
        result = await router.handle(session, msg)
        assert result.ok is False
        assert result.error_code == "removed"
    assert actions.names == []


@pytest.mark.asyncio
async def test_unknown_command_is_rejected(audit, session, actions):
    router = make_router(audit, actions)
    result = await router.handle(session, {"cmd": "nope"})
    assert result.ok is False
    assert result.error_code == "unknown_command"
    # No *known* action may run for an unlisted command.
    assert set(actions.names) <= {"unknown"}
    assert "nope" not in COMMAND_POLICY


@pytest.mark.asyncio
async def test_approval_response_uses_request_and_session(audit, session, actions):
    granted = FakeApprovalPort(result=True)
    router = make_router(audit, actions, approval_port=granted)
    msg = {"cmd": "approval_response", "request_id": "r1", "decision": "allow"}
    result = await router.handle(session, msg)
    assert result.ok is True
    assert granted.calls == [(session.session_id, "r1", "allow")]
    assert actions.names == []

    refused = FakeApprovalPort(result=False)
    router = make_router(audit, actions, approval_port=refused)
    msg = {"cmd": "approval_response", "request_id": "r2", "decision": "deny"}
    result = await router.handle(session, msg)
    assert result.ok is False
    assert result.error_code == "approval_rejected"
    assert refused.calls == [(session.session_id, "r2", "deny")]
    assert actions.names == []


@pytest.mark.asyncio
async def test_revoked_session_cannot_command(audit, session, actions):
    router = make_router(audit, actions)
    session.revoked_at = time.time()
    result = await router.handle(session, {"cmd": "chat", "text": "hello"})
    assert result.ok is False
    assert result.error_code == "session_revoked"
    assert actions.names == []


@pytest.mark.asyncio
async def test_audit_failure_blocks_config_command(session, actions):
    router = make_router(BrokenAudit(), actions)
    blocked = await router.handle(session, {"cmd": "model", "model": "some-model"})
    assert blocked.ok is False
    assert blocked.error_code == "audit_unavailable"
    assert "set_model" not in actions.names

    # chat/listen/abort are explicitly allowed to proceed without an audit trail.
    ok = await router.handle(session, {"cmd": "chat", "text": "hello"})
    assert ok.ok is True
    assert actions.names.count("chat") == 1
