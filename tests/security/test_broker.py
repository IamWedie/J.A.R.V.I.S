"""Task 17: Tool broker pipeline (schema -> policy -> approval -> audit -> lane)."""

import time
import uuid
from types import SimpleNamespace

import pytest

from core.security.approvals import ApprovalDecision
from core.security.audit import AuditLog, redact_args
from core.security.broker import ToolBroker, ToolInvocation
from core.security.lanes import LaneTimeout
from core.security.policy import Decision, PolicyDecision


@pytest.fixture
def audit(tmp_path):
    log = AuditLog(tmp_path / "audit.db", hmac_key=b"0" * 32)
    yield log
    log.close()


def make_spec(**overrides):
    params = {
        "name": "read_file",
        "title": "Read File",
        "description": "Read a file for the user",
        "handler": "core.tools:read_file",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "token": {"type": "string"},
                "nested": {
                    "type": "object",
                    "properties": {"password": {"type": "string"}},
                },
            },
            "required": ["path"],
            "additionalProperties": True,
        },
        "capabilities": ("filesystem.read",),
        "risk": "guarded",
        "lane": "core",
        "timeout_seconds": 10.0,
        "enabled_by_default": True,
        "version": "1",
        "code_files": (),
        "filesystem_roots": (),
        "egress_allowlist": (),
        "app_allowlist": (),
        "argument_scopes": {},
        "feature": None,
        "optional_dependency": None,
        "privacy_notes": "",
    }
    params.update(overrides)
    return SimpleNamespace(**params)


class FakeRegistry:
    def __init__(self, spec=None):
        self.spec = spec
        self.calls = []

    def get(self, name):
        self.calls.append(name)
        if self.spec is None:
            return None
        return self.spec if name == self.spec.name else None


class FakePolicy:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []
        self.grants = []

    def evaluate(self, spec, args, features):
        self.calls.append((spec, args, features))
        return self.decision

    def add_grant(self, spec, scope, source, ttl_days=30):
        self.grants.append((spec, scope, source))
        return "grant-id"


class FakeApprovals:
    def __init__(self, wait_decision=ApprovalDecision.ALLOW):
        self.wait_decision = wait_decision
        self.create_calls = []
        self.wait_calls = []
        self.last_request = None

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        request = SimpleNamespace(
            request_id=uuid.uuid4().hex,
            session_id=kwargs["session_id"],
            source=kwargs["source"],
            tool=kwargs["tool"],
            capabilities=tuple(kwargs["capabilities"]),
            risk=kwargs["risk"],
            description=kwargs["description"],
            args_redacted=redact_args(kwargs["args"]),
            created_at=time.time(),
            expires_at=time.time() + 60.0,
            can_grant=kwargs.get("can_grant", False),
            grant_scope=kwargs.get("grant_scope"),
        )
        self.last_request = request
        return request

    async def wait(self, request):
        self.wait_calls.append(request)
        return self.wait_decision


class FakeLanes:
    def __init__(self, result="ok result", error=None, audit=None):
        self.result = result
        self.error = error
        self.audit = audit
        self.calls = []
        self.snapshots = []

    async def dispatch(self, spec, args):
        self.calls.append((spec, args))
        if self.audit is not None:
            self.snapshots.append(self.audit.recent(50))
        if self.error is not None:
            raise self.error
        return self.result


class FakeSessions:
    def __init__(self, active=None):
        self.active = active

    def get_active(self):
        return self.active


def make_notify():
    events = []

    async def notify(event):
        events.append(event)

    return notify, events


def make_show_window():
    calls = []

    def show_window():
        calls.append(1)

    return show_window, calls


_DEFAULT = object()


def make_broker(
    audit,
    *,
    spec=_DEFAULT,
    decision=None,
    wait_decision=ApprovalDecision.ALLOW,
    lane_result="ok result",
    lane_error=None,
    active_session=None,
    session_wait_seconds=10.0,
):
    if spec is _DEFAULT:
        spec = make_spec()
    if decision is None:
        decision = PolicyDecision(Decision.ALLOW, "safe", "guarded")
    registry = FakeRegistry(spec)
    policy = FakePolicy(decision)
    approvals = FakeApprovals(wait_decision)
    lanes = FakeLanes(result=lane_result, error=lane_error, audit=audit)
    notify, notify_events = make_notify()
    show_window, window_calls = make_show_window()
    sessions = FakeSessions(active_session)
    broker = ToolBroker(
        registry,
        policy,
        approvals,
        audit,
        lanes,
        notify=notify,
        show_window=show_window,
        sessions=sessions,
        session_wait_seconds=session_wait_seconds,
    )
    return broker, registry, policy, approvals, lanes, notify_events, window_calls


def audit_rows(audit, event_type):
    return [row for row in audit.recent(50) if row["event_type"] == event_type]


def active_s1():
    return SimpleNamespace(session_id="s1", active=True)


@pytest.mark.asyncio
async def test_unknown_tool_is_denied(audit):
    broker, registry, policy, approvals, lanes, notify_events, _ = make_broker(
        audit, spec=None
    )

    result = await broker.invoke(
        ToolInvocation(tool="missing_tool", args={"path": "C:/tmp/a.txt"}, session_id="s1", source="ui")
    )

    assert result.ok is False
    assert result.error_code == "unknown_tool"
    assert result.content == "Unknown or disabled tool."
    assert lanes.calls == []
    assert audit_rows(audit, "tool.request") == []


@pytest.mark.asyncio
async def test_invalid_arguments_are_denied(audit):
    broker, registry, policy, approvals, lanes, notify_events, _ = make_broker(audit)

    result = await broker.invoke(
        ToolInvocation(tool="read_file", args={"path": 123}, session_id="s1", source="ui")
    )

    assert result.ok is False
    assert result.error_code == "invalid_arguments"
    assert result.content == "Invalid arguments for this tool."
    assert lanes.calls == []


@pytest.mark.asyncio
async def test_scope_violation_is_denied(audit):
    decision = PolicyDecision(Decision.DENY, "scoped_outside", "guarded")
    broker, registry, policy, approvals, lanes, notify_events, _ = make_broker(
        audit, decision=decision
    )

    result = await broker.invoke(
        ToolInvocation(tool="read_file", args={"path": "C:/tmp/a.txt"}, session_id="s1", source="ui")
    )

    assert result.ok is False
    assert result.error_code == "scope_denied"
    assert result.content == "Requested scope is outside the allowed range."
    assert lanes.calls == []
    rows = audit_rows(audit, "policy.decision")
    assert len(rows) == 1
    assert rows[0]["decision"] == "deny"
    assert rows[0]["error_code"] == "scoped_outside"
    assert rows[0]["tool"] == "read_file"
    assert audit_rows(audit, "tool.request") == []


@pytest.mark.asyncio
async def test_policy_deny_does_not_dispatch(audit):
    decision = PolicyDecision(Decision.DENY, "user_deny", "guarded")
    broker, registry, policy, approvals, lanes, notify_events, _ = make_broker(
        audit, decision=decision
    )

    result = await broker.invoke(
        ToolInvocation(tool="read_file", args={"path": "C:/tmp/a.txt"}, session_id="s1", source="ui")
    )

    assert result.ok is False
    assert result.error_code == "policy_denied"
    assert result.content == "Denied by policy."
    assert lanes.calls == []
    assert audit_rows(audit, "tool.request") == []


@pytest.mark.asyncio
async def test_policy_ask_denied_does_not_dispatch(audit):
    decision = PolicyDecision(Decision.ASK, "sensitive", "sensitive")
    broker, registry, policy, approvals, lanes, notify_events, _ = make_broker(
        audit,
        decision=decision,
        wait_decision=ApprovalDecision.DENY,
        active_session=active_s1(),
    )

    result = await broker.invoke(
        ToolInvocation(tool="read_file", args={"path": "C:/tmp/a.txt"}, session_id="s1", source="ui")
    )

    assert result.ok is False
    assert result.error_code == "approval_denied"
    assert result.content == "Approval was not granted."
    assert lanes.calls == []
    assert len(notify_events) == 1
    assert notify_events[0]["type"] == "approval_request"
    assert notify_events[0]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_policy_ask_approved_dispatches(audit):
    decision = PolicyDecision(
        Decision.ASK, "guarded_ask", "guarded", grant_scope="C:/tmp", can_grant=True
    )
    broker, registry, policy, approvals, lanes, notify_events, _ = make_broker(
        audit,
        decision=decision,
        wait_decision=ApprovalDecision.ALLOW,
        lane_result="file contents",
        active_session=active_s1(),
    )

    args = {
        "path": "C:/tmp/a.txt",
        "token": "supersecret",
        "nested": {"password": "hunter2"},
    }
    result = await broker.invoke(
        ToolInvocation(tool="read_file", args=args, session_id="s1", source="ui")
    )

    assert result.ok is True
    assert result.content == "file contents"
    assert result.error_code is None
    assert len(lanes.calls) == 1
    # tool.request committed before dispatch
    assert len(lanes.snapshots) == 1
    assert any(
        row["event_type"] == "tool.request" for row in lanes.snapshots[0]
    )
    # notification carries created request_id
    assert len(notify_events) == 1
    assert notify_events[0]["type"] == "approval_request"
    assert notify_events[0]["request_id"] == approvals.last_request.request_id
    assert notify_events[0]["session_id"] == "s1"
    # create received expected fields
    assert len(approvals.create_calls) == 1
    create_kwargs = approvals.create_calls[0]
    assert create_kwargs["session_id"] == "s1"
    assert create_kwargs["source"] == "ui"
    assert create_kwargs["tool"] == "read_file"
    assert create_kwargs["risk"] == "guarded"
    assert create_kwargs["can_grant"] is True
    assert create_kwargs["grant_scope"] == "C:/tmp"
    # audit rows
    result_rows = audit_rows(audit, "tool.result")
    assert len(result_rows) == 1
    assert result_rows[0]["result_status"] == "ok"
    request_rows = audit_rows(audit, "tool.request")
    assert len(request_rows) == 1
    assert request_rows[0]["args_redacted"] is not None
    assert "supersecret" not in request_rows[0]["args_redacted"]
    assert "hunter2" not in request_rows[0]["args_redacted"]
    assert "[REDACTED]" in request_rows[0]["args_redacted"]
    assert audit.verify_chain() is True


@pytest.mark.asyncio
async def test_audit_failure_prevents_dispatch(audit):
    class FailingToolRequestAudit:
        def __init__(self, real):
            self.real = real

        def append(self, event_type, **kwargs):
            if event_type == "tool.request":
                raise RuntimeError("audit down")
            return self.real.append(event_type, **kwargs)

        def recent(self, limit=20):
            return self.real.recent(limit)

    failing = FailingToolRequestAudit(audit)
    spec = make_spec()
    registry = FakeRegistry(spec)
    policy = FakePolicy(PolicyDecision(Decision.ALLOW, "safe", "guarded"))
    approvals = FakeApprovals(ApprovalDecision.ALLOW)
    lanes = FakeLanes(result="ok result", audit=audit)
    notify, notify_events = make_notify()
    show_window, window_calls = make_show_window()
    sessions = FakeSessions(active_s1())
    broker = ToolBroker(
        registry,
        policy,
        approvals,
        failing,
        lanes,
        notify=notify,
        show_window=show_window,
        sessions=sessions,
    )

    result = await broker.invoke(
        ToolInvocation(tool="read_file", args={"path": "C:/tmp/a.txt"}, session_id="s1", source="ui")
    )

    assert result.ok is False
    assert result.error_code == "audit_unavailable"
    assert result.content == "Tool execution was blocked because the audit log is unavailable."
    assert lanes.calls == []
    assert audit_rows(audit, "tool.request") == []


@pytest.mark.asyncio
async def test_dispatch_error_is_audited(audit):
    broker, registry, policy, approvals, lanes, notify_events, _ = make_broker(
        audit, lane_error=RuntimeError("secret detail")
    )

    result = await broker.invoke(
        ToolInvocation(tool="read_file", args={"path": "C:/tmp/a.txt"}, session_id="s1", source="ui")
    )

    assert result.ok is False
    assert result.error_code == "tool_error"
    assert result.content == "Tool failed."
    assert "secret detail" not in result.content
    rows = audit_rows(audit, "tool.error")
    assert len(rows) == 1
    assert rows[0]["result_status"] == "error"
    assert rows[0]["error_code"] == "tool_error"
    blob = " ".join(str(v) for row in audit.recent(50) for v in row.values())
    assert "secret detail" not in blob
    assert audit.verify_chain() is True


@pytest.mark.asyncio
async def test_timeout_is_audited(audit):
    broker, registry, policy, approvals, lanes, notify_events, _ = make_broker(
        audit, lane_error=LaneTimeout("lane_timeout")
    )

    result = await broker.invoke(
        ToolInvocation(tool="read_file", args={"path": "C:/tmp/a.txt"}, session_id="s1", source="ui")
    )

    assert result.ok is False
    assert result.error_code == "tool_timeout"
    assert result.content == "Tool timed out."
    rows = audit_rows(audit, "tool.timeout")
    assert len(rows) == 1
    assert rows[0]["result_status"] == "timeout"
    assert rows[0]["error_code"] == "tool_timeout"


@pytest.mark.asyncio
async def test_sensitive_action_without_session_asks_for_window_and_denies(audit):
    decision = PolicyDecision(Decision.ASK, "sensitive", "sensitive")
    broker, registry, policy, approvals, lanes, notify_events, window_calls = make_broker(
        audit,
        decision=decision,
        wait_decision=ApprovalDecision.ALLOW,
        active_session=None,
        session_wait_seconds=0.05,
    )

    start = time.monotonic()
    result = await broker.invoke(
        ToolInvocation(tool="read_file", args={"path": "C:/tmp/a.txt"}, session_id="s1", source="ui")
    )
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert window_calls == [1]
    assert notify_events == []
    assert result.ok is False
    assert result.error_code == "approval_denied"
    assert result.content == "Approval was not granted."
    assert lanes.calls == []
