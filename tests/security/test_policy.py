"""Tests for the default-deny policy engine, scoped grants, HMAC (Task 11)."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from core.security.audit import AuditLog
from core.security.canonical import canonical_json
from core.security.features import DEFAULT_FEATURES
from core.security.manifest import ToolSpec
from core.security.policy import Decision, PolicyEngine, PolicyIntegrityError

KEY = b"0123456789abcdef0123456789abcdef"


def _spec(name, *, risk="safe", caps=(), roots=(), egress=(), apps=(), arg_scopes=None):
    """Build a ToolSpec directly with the given name/caps/risk/scopes."""
    return ToolSpec(
        name=name,
        title=name.replace("_", " ").title(),
        description="Test tool.",
        handler="tests.test_policy:handler",
        parameters={"type": "object", "properties": {}},
        capabilities=tuple(caps),
        risk=risk,
        lane="core",
        timeout_seconds=5,
        enabled_by_default=True,
        version="1.0.0",
        code_files=(),
        filesystem_roots=tuple(roots),
        egress_allowlist=tuple(egress),
        app_allowlist=tuple(apps),
        argument_scopes={} if arg_scopes is None else arg_scopes,
    )


def write_policy(path, key, payload):
    """Write an HMAC envelope for *payload* signed with *key* (fixture data)."""
    mac = hmac.new(key, canonical_json(payload), hashlib.sha256).hexdigest()
    envelope = {"version": 1, "payload": payload, "hmac": mac}
    path.write_text(
        json.dumps(envelope, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _engine(tmp_path, *, overrides=None, grants=None, **kwargs):
    """Write a valid policy file and load it into a PolicyEngine."""
    path = tmp_path / "policy.json"
    write_policy(
        path,
        KEY,
        {"tool_overrides": overrides or {}, "grants": grants or []},
    )
    return PolicyEngine(path, KEY, **kwargs)


def test_safe_tool_is_allowed(tmp_path):
    engine = PolicyEngine(tmp_path / "policy.json", KEY)
    decision = engine.evaluate(_spec("system_info"), {}, DEFAULT_FEATURES)
    assert decision.decision is Decision.ALLOW
    assert decision.reason == "safe"
    assert decision.effective_risk == "safe"


def test_default_guarded_tools_are_allowed(tmp_path):
    engine = PolicyEngine(tmp_path / "policy.json", KEY)
    for name in ("set_volume", "media_key", "set_reminder", "remember_fact"):
        decision = engine.evaluate(_spec(name, risk="guarded"), {}, DEFAULT_FEATURES)
        assert decision.decision is Decision.ALLOW, name
        assert decision.reason == "guarded_allowed", name
    web = _spec("web_search", risk="guarded", egress=("example.com",))
    decision = engine.evaluate(web, {}, DEFAULT_FEATURES)
    assert decision.decision is Decision.ALLOW
    assert decision.reason == "fixed_endpoint"


def test_scoped_guarded_tool_is_allowed_inside_scope(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "notes.txt").write_text("hi", encoding="utf-8")
    spec = _spec("search_files", risk="guarded", roots=(str(docs),))
    engine = PolicyEngine(tmp_path / "policy.json", KEY)
    decision = engine.evaluate(spec, {"location": str(docs / "notes.txt")}, DEFAULT_FEATURES)
    assert decision.decision is Decision.ALLOW
    assert decision.reason == "scoped_allow"


def test_scoped_guarded_tool_asks_outside_scope(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    spec = _spec("search_files", risk="guarded", roots=(str(docs),))
    engine = PolicyEngine(tmp_path / "policy.json", KEY)
    decision = engine.evaluate(spec, {"location": str(tmp_path / "elsewhere")}, DEFAULT_FEATURES)
    assert decision.decision is Decision.ASK
    assert decision.can_grant is False


def test_sensitive_tool_asks(tmp_path):
    spec = _spec("get_clipboard", risk="sensitive", caps=("clipboard.read",))
    engine = PolicyEngine(tmp_path / "policy.json", KEY)
    decision = engine.evaluate(spec, {}, DEFAULT_FEATURES)
    assert decision.decision is Decision.ASK
    assert decision.reason == "sensitive"
    assert decision.can_grant is False


def test_critical_tool_is_denied(tmp_path):
    spec = _spec("fake_security_admin", risk="critical", caps=("security.admin",))
    engine = PolicyEngine(tmp_path / "policy.json", KEY)
    decision = engine.evaluate(spec, {}, DEFAULT_FEATURES)
    assert decision.decision is Decision.DENY
    assert decision.reason == "critical"


def test_user_deny_override_narrows(tmp_path):
    engine = _engine(tmp_path, overrides={"set_volume": "deny"})
    decision = engine.evaluate(_spec("set_volume", risk="guarded"), {}, DEFAULT_FEATURES)
    assert decision.decision is Decision.DENY
    assert decision.reason == "user_deny"


def test_user_allow_cannot_widen_manifest_scope(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    engine = _engine(tmp_path, overrides={"search_files": "allow", "open_url": "allow"})
    search = _spec("search_files", risk="guarded", roots=(str(docs),))
    decision = engine.evaluate(search, {"location": str(tmp_path / "x")}, DEFAULT_FEATURES)
    assert decision.decision is Decision.ASK
    url = _spec("open_url", risk="guarded", egress=("example.com",))
    decision = engine.evaluate(url, {"url": "http://127.0.0.1/"}, DEFAULT_FEATURES)
    assert decision.decision is Decision.ASK
    assert decision.can_grant is False


def test_sensitive_tool_cannot_be_granted(tmp_path):
    spec = _spec("get_clipboard", risk="sensitive", caps=("clipboard.read",))
    engine = PolicyEngine(tmp_path / "policy.json", KEY)
    with pytest.raises((ValueError, PolicyIntegrityError)):
        engine.add_grant(spec, "anything", "ui")
    assert engine.list_grants() == []
    decision = engine.evaluate(spec, {}, DEFAULT_FEATURES)
    assert decision.decision is Decision.ASK


def test_grant_expiry_and_revocation(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    target = docs / "target.txt"
    target.write_text("x", encoding="utf-8")
    spec = _spec(
        "read_file",
        risk="guarded",
        caps=("filesystem.read",),
        roots=(str(docs),),
        arg_scopes={"filesystem": ["path"]},
    )
    now = [1000.0]
    engine = PolicyEngine(tmp_path / "policy.json", KEY, clock=lambda: now[0])
    args = {"path": str(target)}
    # Built-in decision for this non-allowlisted guarded tool is ASK.
    decision = engine.evaluate(spec, args, DEFAULT_FEATURES)
    assert decision.decision is Decision.ASK
    assert decision.can_grant is True
    assert decision.grant_scope is not None
    # Grant lifts ASK to ALLOW while unexpired.
    grant_id = engine.add_grant(spec, str(target), "ui", ttl_days=1)
    decision = engine.evaluate(spec, args, DEFAULT_FEATURES)
    assert decision.decision is Decision.ALLOW
    assert decision.reason == "grant"
    # Advance two days: the one-day grant expires.
    now[0] += 2 * 86400
    decision = engine.evaluate(spec, args, DEFAULT_FEATURES)
    assert decision.decision is Decision.ASK
    # A fresh grant works until revoked.
    grant2 = engine.add_grant(spec, str(target), "ui", ttl_days=1)
    decision = engine.evaluate(spec, args, DEFAULT_FEATURES)
    assert decision.decision is Decision.ALLOW
    assert engine.revoke_grant(grant2) is True
    decision = engine.evaluate(spec, args, DEFAULT_FEATURES)
    assert decision.decision is Decision.ASK
    assert engine.revoke_grant("no-such-grant") is False


def test_corrupt_hmac_denies_all(tmp_path):
    path = tmp_path / "policy.json"
    write_policy(
        path,
        b"0123456789abcdefXXXX",  # wrong key -> bad HMAC
        {"tool_overrides": {}, "grants": []},
    )
    before = path.read_bytes()
    engine = PolicyEngine(path, KEY)
    assert engine.status() == "corrupt"
    decision = engine.evaluate(_spec("system_info"), {}, DEFAULT_FEATURES)
    assert decision.decision is Decision.DENY
    assert decision.reason == "policy_corrupt"
    guarded = _spec("set_volume", risk="guarded")
    with pytest.raises(PolicyIntegrityError):
        engine.add_grant(guarded, "x", "ui")
    with pytest.raises(PolicyIntegrityError):
        engine.revoke_grant("any")
    # The corrupt file is never repaired or overwritten.
    assert path.read_bytes() == before


def test_policy_change_is_audited(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    target = docs / "target.txt"
    target.write_text("x", encoding="utf-8")
    spec = _spec(
        "read_file",
        risk="guarded",
        caps=("filesystem.read",),
        roots=(str(docs),),
        arg_scopes={"filesystem": ["path"]},
    )
    audit = AuditLog(tmp_path / "audit.db", KEY)
    engine = PolicyEngine(tmp_path / "policy.json", KEY, audit=audit)
    grant_id = engine.add_grant(spec, str(target), "ui", ttl_days=1)
    assert engine.revoke_grant(grant_id) is True
    events = [e for e in audit.recent(50) if e["event_type"] == "policy.change"]
    actions = [json.loads(e["args_redacted"])["action"] for e in events]
    assert actions == ["grant", "revoke"]
    audit.close()


def test_unknown_policy_entry_is_ignored_and_denies(tmp_path):
    engine = _engine(
        tmp_path,
        overrides={
            "ghost": "allow",
            "set_volume": "bogus",
            "get_clipboard": "allow",
            "fake_security_admin": "allow",
        },
    )
    decision = engine.evaluate(_spec("set_volume", risk="guarded"), {}, DEFAULT_FEATURES)
    assert decision.decision is Decision.ALLOW  # bogus value ignored
    decision = engine.evaluate(
        _spec("get_clipboard", risk="sensitive", caps=("clipboard.read",)),
        {},
        DEFAULT_FEATURES,
    )
    assert decision.decision is Decision.ASK  # allow cannot widen sensitive
    critical = _spec("fake_security_admin", risk="critical", caps=("security.admin",))
    decision = engine.evaluate(critical, {}, DEFAULT_FEATURES)
    assert decision.decision is Decision.DENY  # allow cannot widen critical


def test_unresolved_app_is_asked_not_allowed(tmp_path):
    spec = _spec("launch_app", risk="guarded", apps=("Notepad",))
    engine = PolicyEngine(tmp_path / "policy.json", KEY)
    decision = engine.evaluate(
        spec, {"query": "zzzz-no-such-app-xyz"}, DEFAULT_FEATURES
    )
    assert decision.decision is Decision.ASK
    assert decision.reason == "unresolved_app"
    assert decision.can_grant is False
