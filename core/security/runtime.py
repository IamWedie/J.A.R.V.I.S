"""Runtime composition for the secure core.

Builds the audit log, verified tool registry, policy engine, approval
manager, lanes, and tool broker in a fixed order, and fails closed when
the audit key or the tool manifest cannot be trusted.  Chat stays
available without tools; tool calls deny.

This module must not import ``core.brain``, ``server``, or ``core.tools``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Any

from core.security.approvals import ApprovalManager
from core.security.audit import AuditLog
from core.security.broker import ToolBroker, ToolResult
from core.security.features import DEFAULT_FEATURES, Features
from core.security.keystore import KeyStoreError, load_or_create_key
from core.security.lanes import LaneDispatcher, LaneError
from core.security.manifest import ManifestError, verify_manifest
from core.security.policy import PolicyEngine
from core.security.registry import ToolRegistry
from core.security.tool_pubkeys import TRUSTED_TOOL_KEYS


class _DenyBroker:
    """Broker that denies every tool call because no audit chain exists."""

    async def invoke(self, invocation) -> ToolResult:
        return ToolResult(
            False,
            "Tool execution was blocked because the audit log is unavailable.",
            "audit_unavailable",
        )


class _DenyApprovals:
    """Approval port that denies everything; satisfies the router contract."""

    def resolve(self, session_id, request_id, decision) -> bool:
        return False

    def cancel_session(self, session_id, reason="session_closed") -> int:
        return 0


class _EmptyRegistry:
    """Registry with no tools; every tool call denies as ``unknown_tool``."""

    def get(self, name):
        return None

    def openai_schemas(self):
        return []

    def enabled_tools(self):
        return []

    def disabled_reasons(self):
        return {}

    def resolve_handler(self, name):
        raise LaneError("unknown_tool")


@dataclass
class SecurityRuntime:
    """Assembled secure runtime; carries the server's token/sessions by identity."""

    token: Any
    sessions: Any
    audit: Any
    policy: Any
    approvals: Any
    registry: Any
    broker: Any
    lanes: Any
    status: str

    def security_status(self) -> dict:
        """Public status snapshot: tools on, features off, recent audit rows."""
        tools: list[str] = []
        if self.registry is not None:
            try:
                tools = sorted(spec.name for spec in self.registry.enabled_tools())
            except Exception:
                tools = []
        disabled_features = sorted(
            feature.name
            for feature in fields(Features)
            if not DEFAULT_FEATURES.enabled(feature.name)
        )
        recent_audit: list[dict] = []
        if self.audit is not None:
            try:
                recent_audit = list(self.audit.recent(20) or [])
            except Exception:
                recent_audit = []
        status: dict[str, Any] = {
            "status": self.status,
            "tools": tools,
            "disabled_features": disabled_features,
            "recent_audit": recent_audit,
        }
        if self.audit is None:
            status["recovery_hint"] = (
                "The audit key is unreadable: restore or remove the corrupted "
                "audit.key, then restart."
            )
        return status


def _safe_append(audit, event_type, **kwargs) -> None:
    """Best-effort audit write: a raising log must never break startup."""
    if audit is None:
        return
    try:
        audit.append(event_type, **kwargs)
    except Exception:
        pass


def build_runtime(
    *,
    token,
    sessions,
    data_dir,
    code_root,
    frozen,
    dev_mode,
    notify,
    show_window,
    voice_provider,
) -> SecurityRuntime:
    """Build the secure runtime; never raises.

    Statuses: ``ok`` (manifest verified, tools brokered), ``manifest_error``
    (empty registry, chat still on, every tool call denies), ``audit_locked``
    (no key, no chain, broker denies all), ``runtime_error`` (unexpected build
    failure, broker denies all).
    """
    data_dir = os.fspath(data_dir)
    code_root = os.fspath(code_root)
    audit_key = os.path.join(data_dir, "audit.key")
    audit_db = os.path.join(data_dir, "audit.db")
    policy_path = os.path.join(data_dir, "security_policy.json")
    sandbox_dir = os.path.join(data_dir, "sandbox")
    manifest_path = os.path.join(code_root, "core", "security", "tool_manifest.json")
    signature_path = os.path.join(code_root, "core", "security", "tool_manifest.sig")

    audit = None
    try:
        try:
            key = load_or_create_key(audit_key)
        except KeyStoreError:
            return SecurityRuntime(
                token=token,
                sessions=sessions,
                audit=None,
                policy=None,
                approvals=_DenyApprovals(),
                registry=None,
                broker=_DenyBroker(),
                lanes=None,
                status="audit_locked",
            )

        audit = AuditLog(audit_db, key)
        audit.reconcile_incomplete()

        try:
            manifest = verify_manifest(
                manifest_path,
                signature_path,
                code_root,
                TRUSTED_TOOL_KEYS,
                frozen=frozen,
                dev_mode=dev_mode,
            )
        except ManifestError:
            _safe_append(
                audit,
                "manifest.reject",
                source="runtime",
                result_status="error",
                error_code="manifest_reject",
            )
            registry = _EmptyRegistry()
            policy = PolicyEngine(policy_path, key, audit=audit)
            approvals = ApprovalManager(audit)
            lanes = LaneDispatcher(registry, sandbox_dir)
            broker = ToolBroker(
                registry,
                policy,
                approvals,
                audit,
                lanes,
                notify=notify,
                show_window=show_window,
                sessions=sessions,
                voice_provider=voice_provider,
            )
            return SecurityRuntime(
                token=token,
                sessions=sessions,
                audit=audit,
                policy=policy,
                approvals=approvals,
                registry=registry,
                broker=broker,
                lanes=lanes,
                status="manifest_error",
            )

        registry = ToolRegistry(manifest, DEFAULT_FEATURES, code_root, frozen)
        registry.load()
        policy = PolicyEngine(policy_path, key, audit=audit)
        approvals = ApprovalManager(audit)
        lanes = LaneDispatcher(registry, sandbox_dir)
        broker = ToolBroker(
            registry,
            policy,
            approvals,
            audit,
            lanes,
            notify=notify,
            show_window=show_window,
            sessions=sessions,
            voice_provider=voice_provider,
        )
        _safe_append(audit, "manifest.load", source="runtime", result_status="ok")
        return SecurityRuntime(
            token=token,
            sessions=sessions,
            audit=audit,
            policy=policy,
            approvals=approvals,
            registry=registry,
            broker=broker,
            lanes=lanes,
            status="ok",
        )
    except Exception:
        _safe_append(
            audit,
            "manifest.reject",
            source="runtime",
            result_status="error",
            error_code="runtime_error",
        )
        return SecurityRuntime(
            token=token,
            sessions=sessions,
            audit=audit,
            policy=None,
            approvals=_DenyApprovals(),
            registry=None,
            broker=_DenyBroker(),
            lanes=None,
            status="runtime_error",
        )
