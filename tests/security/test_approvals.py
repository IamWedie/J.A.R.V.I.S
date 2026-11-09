"""Task 12: request-bound, single-use approval manager.

Every test drives a real ``AuditLog`` in ``tmp_path`` (no fakes), so the
``approval.requested`` / ``approval.resolved`` / ``approval.rejected`` /
``approval.expired`` / ``approval.cancelled`` chain is exercised for real
and ``verify_chain()`` stays green.

Only the expiry test sleeps in wall time (``ttl=0.01`` /
``asyncio.sleep(0.02)``, exactly as the brief specifies); every other
``asyncio.sleep(0)`` is a cooperative yield that lets freshly created
waiter tasks reach their first suspension -- not a timed sleep.
"""
import asyncio
import json
import time
from dataclasses import FrozenInstanceError, fields, replace

import pytest
from starlette.websockets import WebSocketDisconnect

from core.security.approvals import (
    ApprovalDecision,
    ApprovalError,
    ApprovalManager,
    ApprovalRequest,
)
from core.security.audit import AuditLog
from core.security.auth import Session, SessionManager
from core.security.commands import CommandRouter

ORIGIN = {"origin": "http://127.0.0.1:8741"}


@pytest.fixture
def audit(tmp_path):
    log = AuditLog(tmp_path / "audit.db", hmac_key=b"0" * 32)
    yield log
    log.close()


@pytest.fixture
def manager(audit):
    return ApprovalManager(audit)


def make_request(manager, session_id="s1", **overrides):
    params = {
        "session_id": session_id,
        "source": "ui",
        "tool": "read_file",
        "capabilities": ("filesystem.read",),
        "risk": "guarded",
        "description": "read a file for the user",
        "args": {"path": "C:/tmp/a.txt"},
    }
    params.update(overrides)
    return manager.create(**params)


def audit_rows(log, event_type):
    return [row for row in log.recent(50) if row["event_type"] == event_type]


class FlakyAudit:
    """Delegates to a real AuditLog, optionally failing one event type."""

    def __init__(self, real):
        self.real = real
        self.fail_on = None

    def append(self, event_type, **kwargs):
        if event_type == self.fail_on:
            raise RuntimeError("audit down")
        return self.real.append(event_type, **kwargs)


@pytest.mark.asyncio
async def test_allow_resolves_only_matching_request(audit, manager):
    first = make_request(manager)
    second = make_request(manager)
    assert manager.pending_for("s1") == [first, second]

    first_waiter = asyncio.create_task(manager.wait(first))
    second_waiter = asyncio.create_task(manager.wait(second))
    await asyncio.sleep(0)  # both waiters are now suspended on their futures

    assert manager.resolve("s1", second.request_id, "allow") is True
    assert not first_waiter.done()  # approving r2 must not complete r1's waiter

    assert manager.resolve("s1", first.request_id, "allow") is True
    assert await asyncio.gather(first_waiter, second_waiter) == [
        ApprovalDecision.ALLOW,
        ApprovalDecision.ALLOW,
    ]
    assert manager.pending_for("s1") == []
    assert [row["event_type"] for row in audit.recent(10)] == [
        "approval.requested",
        "approval.requested",
        "approval.resolved",
        "approval.resolved",
    ]


@pytest.mark.asyncio
async def test_deny_resolves_false(audit, manager):
    request = make_request(manager)
    waiter = asyncio.create_task(manager.wait(request))
    await asyncio.sleep(0)

    assert manager.resolve("s1", request.request_id, "deny") is False
    assert await waiter == ApprovalDecision.DENY
    assert manager.pending_for("s1") == []

    resolved = audit_rows(audit, "approval.resolved")
    assert len(resolved) == 1
    assert resolved[0]["decision"] == "deny"
    # an explicit deny is a resolution, never a rejection
    assert audit_rows(audit, "approval.rejected") == []

    # cancelling an already-resolved request is a no-op (0), not an error
    assert manager.cancel_session("s1") == 0


@pytest.mark.asyncio
async def test_replay_is_rejected(audit, manager):
    request = make_request(manager)
    waiter = asyncio.create_task(manager.wait(request))
    await asyncio.sleep(0)

    assert manager.resolve("s1", request.request_id, "allow") is True
    assert await waiter == ApprovalDecision.ALLOW

    # replay: same id, same session, same decision -- must not resolve again
    assert manager.resolve("s1", request.request_id, "allow") is False
    rejected = audit_rows(audit, "approval.rejected")
    assert len(rejected) == 1
    assert rejected[0]["error_code"] == "unknown_request"
    assert rejected[0]["request_id"] == request.request_id
    assert rejected[0]["tool"] is None  # request not found -> no tool fields
    assert waiter.result() == ApprovalDecision.ALLOW  # first future stays ALLOW
    assert len(audit_rows(audit, "approval.resolved")) == 1
    assert manager.pending_for("s1") == []

    # a late wait on the consumed request denies without a new audit row
    assert await manager.wait(request) == ApprovalDecision.DENY
    assert len(audit_rows(audit, "approval.rejected")) == 1


@pytest.mark.asyncio
async def test_cross_session_is_rejected(audit, manager):
    request = make_request(manager, session_id="s1")
    waiter = asyncio.create_task(manager.wait(request))
    await asyncio.sleep(0)

    assert manager.resolve("s2", request.request_id, "allow") is False
    assert not waiter.done()
    assert manager.pending_for("s1") == [request]  # still pending for its owner

    rejected = audit_rows(audit, "approval.rejected")
    assert len(rejected) == 1
    assert rejected[0]["error_code"] == "session_mismatch"
    assert rejected[0]["session_id"] == "s2"  # the caller's session
    assert rejected[0]["tool"] == "read_file"  # request was found

    assert manager.resolve("s1", request.request_id, "allow") is True
    assert await waiter == ApprovalDecision.ALLOW
    assert manager.pending_for("s1") == []

    # -- stored-TTL guarantee -------------------------------------------
    # wait must read expires_at from the STORED request; a forged past
    # expires_at on the caller object must not expire the waiter.
    third = make_request(manager, session_id="s1")
    forged = replace(third, expires_at=0.0, tool="forged")
    forged_waiter = asyncio.create_task(manager.wait(forged))
    await asyncio.sleep(0)
    assert not forged_waiter.done()  # trusted forged 0.0 -> would be done
    assert manager.resolve("s1", third.request_id, "allow") is True
    assert await forged_waiter == ApprovalDecision.ALLOW


@pytest.mark.asyncio
async def test_expired_request_denies(audit):
    manager = ApprovalManager(audit, ttl=0.01)
    # constructor validation: ttl must be a finite, non-bool number > 0
    for bad_ttl in (0, -1, float("inf"), float("nan"), True, "60"):
        with pytest.raises(ApprovalError):
            ApprovalManager(audit, ttl=bad_ttl)
    with pytest.raises(ApprovalError):
        ApprovalManager(None)

    first = make_request(manager)
    first_future = manager._pending[first.request_id].future
    assert await manager.wait(first) == ApprovalDecision.DENY
    # asyncio.shield pins: wait_for's timeout must not cancel the shared
    # future the decider would still set.
    assert first_future.done()
    assert not first_future.cancelled()
    assert first_future.result() is ApprovalDecision.DENY
    expired = audit_rows(audit, "approval.expired")
    assert [row["request_id"] for row in expired] == [first.request_id]
    assert expired[0]["decision"] is None
    assert expired[0]["approver"] is None
    assert manager.pending_for("s1") == []

    second = make_request(manager)
    await asyncio.sleep(0.02)  # past ttl=0.01
    assert manager.resolve("s1", second.request_id, "allow") is False
    assert [row["request_id"] for row in audit_rows(audit, "approval.expired")] == [
        first.request_id,
        second.request_id,
    ]
    assert audit_rows(audit, "approval.rejected") == []
    assert manager.pending_for("s1") == []

    # waiting on an already-expired request denies without another audit row
    assert await manager.wait(second) == ApprovalDecision.DENY
    assert len(audit_rows(audit, "approval.expired")) == 2

    # a failing approval.expired write is best effort: wait still denies
    # and must not raise out of the timeout path.
    flaky = FlakyAudit(audit)
    flaky.fail_on = "approval.expired"
    flaky_manager = ApprovalManager(flaky, ttl=0.01)
    third = make_request(flaky_manager)
    assert await flaky_manager.wait(third) == ApprovalDecision.DENY
    assert flaky_manager.pending_for("s1") == []


@pytest.mark.asyncio
async def test_disconnect_cancels_pending(audit, manager):
    first = make_request(manager, session_id="s1")
    second = make_request(manager, session_id="s1")
    other = make_request(manager, session_id="s2")
    first_waiter = asyncio.create_task(manager.wait(first))
    second_waiter = asyncio.create_task(manager.wait(second))
    other_waiter = asyncio.create_task(manager.wait(other))
    await asyncio.sleep(0)

    assert manager.cancel_session("s1") == 2
    assert await asyncio.gather(first_waiter, second_waiter) == [
        ApprovalDecision.DENY,
        ApprovalDecision.DENY,
    ]
    assert manager.pending_for("s1") == []
    assert manager.pending_for("s2") == [other]
    assert not other_waiter.done()

    assert manager.cancel_all() == 1
    assert await other_waiter == ApprovalDecision.DENY
    assert manager.pending_for("s2") == []

    cancelled = audit_rows(audit, "approval.cancelled")
    assert len(cancelled) == 3
    assert {row["error_code"] for row in cancelled} == {"session_closed"}

    # unknown sessions and bad reasons are no-ops (0), never errors
    assert manager.cancel_session("s1") == 0
    assert manager.cancel_session("s9", 123) == 0
    assert manager.cancel_session("s9", "x" * 200) == 0
    assert manager.cancel_all() == 0
    assert len(audit_rows(audit, "approval.cancelled")) == 3

    # a failing approval.cancelled write never blocks the deny: the
    # request is still removed and cancel_session does not raise.
    flaky = FlakyAudit(audit)
    flaky.fail_on = "approval.cancelled"
    flaky_manager = ApprovalManager(flaky)
    doomed = make_request(flaky_manager, session_id="s3")
    assert flaky_manager.cancel_session("s3") == 1
    assert flaky_manager.pending_for("s3") == []

    # a forged request object with the same id must not leak into the
    # cancelled audit row: the STORED request's tool is audited.
    fifth = make_request(manager, session_id="s1")
    forged = replace(fifth, tool="forged")
    stale_waiter = asyncio.create_task(manager.wait(forged))
    await asyncio.sleep(0)
    stale_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stale_waiter
    cancelled = audit_rows(audit, "approval.cancelled")
    assert cancelled[-1]["tool"] == "read_file"  # stored, not "forged"
    assert cancelled[-1]["request_id"] == fifth.request_id
    assert cancelled[-1]["error_code"] == "waiter_cancelled"
    assert manager.pending_for("s1") == []


@pytest.mark.asyncio
async def test_malformed_decision_is_rejected(audit, manager):
    request = make_request(manager)
    waiter = asyncio.create_task(manager.wait(request))
    await asyncio.sleep(0)

    for bad in (None, "", "ALLOW", "allow ", "yes", 1):
        assert manager.resolve("s1", request.request_id, bad) is False

    assert not waiter.done()
    assert manager.pending_for("s1") == [request]  # never consumed

    rejected = audit_rows(audit, "approval.rejected")
    assert len(rejected) == 6
    assert {row["error_code"] for row in rejected} == {"invalid_decision"}

    assert manager.resolve("s1", request.request_id, "allow") is True
    assert await waiter == ApprovalDecision.ALLOW
    assert len(audit_rows(audit, "approval.resolved")) == 1
    assert manager.pending_for("s1") == []


@pytest.mark.asyncio
async def test_request_and_resolution_are_audited(audit, manager):
    # exact contract from the controller ruling
    assert [f.name for f in fields(ApprovalRequest)] == [
        "request_id",
        "session_id",
        "source",
        "tool",
        "capabilities",
        "risk",
        "description",
        "args_redacted",
        "created_at",
        "expires_at",
        "can_grant",
        "grant_scope",
    ]
    assert (
        ApprovalDecision.ALLOW.value,
        ApprovalDecision.DENY.value,
        ApprovalDecision.ALLOW_ALWAYS.value,
    ) == ("allow", "deny", "allow_always")

    # validation runs entirely before any audit write
    with pytest.raises(ApprovalError):
        make_request(manager, source="attacker")
    with pytest.raises(ApprovalError):
        make_request(manager, risk="apocalyptic")
    with pytest.raises(ApprovalError):
        make_request(manager, args=["not", "a", "dict"])
    assert audit_rows(audit, "approval.requested") == []
    assert manager.pending_for("s1") == []

    request = manager.create(
        session_id="s1",
        source="ui",
        tool="read_file",
        capabilities=("filesystem.read",),
        risk="guarded",
        description="read a file for the user",
        args={
            "path": "C:/tmp/a.txt",
            "token": "supersecret",
            "nested": {"password": "hunter2"},
        },
    )
    assert isinstance(request, ApprovalRequest)
    with pytest.raises(FrozenInstanceError):
        request.risk = "critical"
    assert "[REDACTED]" in request.args_redacted
    assert "supersecret" not in request.args_redacted
    assert "hunter2" not in request.args_redacted

    requested = audit_rows(audit, "approval.requested")
    assert len(requested) == 1
    row = requested[0]
    assert row["request_id"] == request.request_id
    assert row["args_redacted"] == request.args_redacted
    assert row["args_hash"] is None  # raw args were never even hashed
    blob = " ".join(str(value) for value in row.values())
    assert "supersecret" not in blob
    assert "hunter2" not in blob
    assert "read a file for the user" not in blob  # description never audited

    waiter = asyncio.create_task(manager.wait(request))
    await asyncio.sleep(0)
    assert manager.resolve("s1", request.request_id, "allow") is True
    assert await waiter == ApprovalDecision.ALLOW

    resolved = audit_rows(audit, "approval.resolved")
    assert len(resolved) == 1
    assert resolved[0]["approver"] == "ui"
    assert resolved[0]["decision"] == "allow"
    assert resolved[0]["request_id"] == request.request_id
    assert audit.verify_chain() is True

    # -- fail-closed audit guarantee ------------------------------------
    # an audit outage must never leave an unaudited pending request or
    # an unaudited grant behind; the caller retries after recovery.
    flaky = FlakyAudit(audit)
    flaky_manager = ApprovalManager(flaky)

    flaky.fail_on = "approval.requested"
    with pytest.raises(ApprovalError, match="audit_unavailable"):
        make_request(flaky_manager)
    assert flaky_manager.pending_for("s1") == []

    flaky.fail_on = None
    retry = make_request(flaky_manager)
    retry_waiter = asyncio.create_task(flaky_manager.wait(retry))
    await asyncio.sleep(0)

    flaky.fail_on = "approval.resolved"
    assert flaky_manager.resolve("s1", retry.request_id, "allow") is False
    assert flaky_manager.pending_for("s1") == [retry]  # still pending
    assert not retry_waiter.done()  # nothing granted unaudited

    flaky.fail_on = None
    assert flaky_manager.resolve("s1", retry.request_id, "allow") is True
    assert await retry_waiter == ApprovalDecision.ALLOW
    assert audit.verify_chain() is True


@pytest.mark.asyncio
async def test_allow_always_only_when_grantable(audit, manager):
    # a non-grantable request may supply grant_scope; it is forced to None
    plain = make_request(manager, grant_scope="C:/tmp")
    assert plain.can_grant is False
    assert plain.grant_scope is None

    waiter = asyncio.create_task(manager.wait(plain))
    await asyncio.sleep(0)

    assert manager.resolve("s1", plain.request_id, "allow_always") is False
    assert not waiter.done()
    assert manager.pending_for("s1") == [plain]  # still pending
    rejected = audit_rows(audit, "approval.rejected")
    assert len(rejected) == 1
    assert rejected[0]["error_code"] == "not_grantable"

    assert manager.resolve("s1", plain.request_id, "allow") is True
    assert await waiter == ApprovalDecision.ALLOW

    # grantable requests must carry a non-empty grant_scope, boolean can_grant
    with pytest.raises(ApprovalError):
        make_request(manager, can_grant=True)  # missing grant_scope
    with pytest.raises(ApprovalError):
        make_request(manager, can_grant="yes", grant_scope="C:/tmp")
    assert manager.pending_for("s1") == []

    grantable = make_request(manager, can_grant=True, grant_scope="C:/tmp")
    assert grantable.grant_scope == "C:/tmp"
    grant_waiter = asyncio.create_task(manager.wait(grantable))
    await asyncio.sleep(0)

    assert manager.resolve("s1", grantable.request_id, "allow_always") is True
    assert await grant_waiter == ApprovalDecision.ALLOW_ALWAYS

    assert [row["decision"] for row in audit_rows(audit, "approval.resolved")] == [
        "allow",
        "allow_always",
    ]
    assert len(audit_rows(audit, "approval.requested")) == 2


@pytest.mark.asyncio
async def test_command_router_resolves_manager_approval(audit):
    manager = ApprovalManager(audit)

    class NoActions:
        """The approval path must never dispatch an action."""

        def __getattr__(self, name):
            def _fail(*args, **kwargs):
                raise AssertionError(f"unexpected action dispatch: {name}")

            return _fail

    router = CommandRouter(audit, manager, NoActions())
    session = Session(session_id="s1", websocket=None, created_at=time.time())

    request = make_request(manager)
    waiter = asyncio.create_task(manager.wait(request))
    await asyncio.sleep(0)  # waiter must be pending before the command runs

    result = await router.handle(
        session,
        {
            "cmd": "approval_response",
            "request_id": request.request_id,
            "decision": "allow",
        },
    )
    assert result.ok is True
    assert await waiter == ApprovalDecision.ALLOW
    assert manager.pending_for("s1") == []
    assert [row["event_type"] for row in audit.recent(10)] == [
        "approval.requested",
        "command.request",
        "approval.resolved",
        "command.result",
    ]


def test_stale_session_command_is_closed(client, token):
    import server

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json({"cmd": "auth", "token": token})
            message = ws.receive_json()
            assert message["type"] == "auth_ok"
            session_id = message["session_id"]
            server.SESSIONS.revoke(session_id, "test_stale")
            ws.send_json({"cmd": "chat", "text": "hello"})
            for _ in range(200):  # drain the initial state/history messages
                if ws.receive_json().get("type") == "session_inactive":
                    break
            else:
                raise AssertionError("did not receive session_inactive")
            ws.receive_json()  # the socket is closed with 1008 right after
    assert exc.value.code == 1008


@pytest.mark.asyncio
async def test_send_event_targets_only_active_session(monkeypatch):
    import server

    class FakeSocket:
        def __init__(self):
            self.texts = []

        async def send_text(self, text):
            self.texts.append(text)

    active_socket = FakeSocket()
    inactive_socket = FakeSocket()
    sessions = SessionManager()
    active = sessions.create(active_socket)
    sessions.create(inactive_socket)  # never activated
    sessions.activate(active)
    monkeypatch.setattr(server, "SESSIONS", sessions)

    await server.send_event({"type": "state", "value": "thinking"})

    assert [json.loads(text) for text in active_socket.texts] == [
        {"type": "state", "value": "thinking"}
    ]
    assert inactive_socket.texts == []
