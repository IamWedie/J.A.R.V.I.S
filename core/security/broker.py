"""Tool broker pipeline: schema, policy, approval, audit, then lane dispatch.

Audit ordering and terminal-failure rationale:

* The pre-execution ``tool.request`` row is written **after** approval
  resolves ALLOW/ALLOW_ALWAYS and **before** ``lanes.dispatch``. Dispatch
  never happens before that audit commit, so every executed tool call has
  a durable request row.
* Terminal rows (``tool.result`` / ``tool.error`` / ``tool.timeout``)
  share the ``tool.request`` ``request_id``. A terminal audit failure
  after dispatch is swallowed by design: it must not turn a successful
  tool call into a reported failure and must not trigger a retry.
  ``AuditLog.reconcile_incomplete()`` closes the dangling
  ``tool.request`` as ``unknown`` on the next startup, preserving the
  audit chain without misreporting the tool outcome.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

import jsonschema

from core.security.approvals import ApprovalDecision, ApprovalError
from core.security.audit import redact_args
from core.security.features import DEFAULT_FEATURES
from core.security.lanes import LaneTimeout
from core.security.policy import Decision, PolicyDecision
from core.security.voice_approval import VoiceResult

__all__ = ["ToolBroker", "ToolInvocation", "ToolResult"]


@dataclass(frozen=True)
class ToolInvocation:
    tool: str
    args: dict
    session_id: str
    source: str


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str
    error_code: str | None = None


_SCOPE_DENIED = frozenset(
    {
        "scoped_outside",
        "scope_violation",
        "scope_denied",
        "no_roots",
        "url_not_allowlisted",
        "unresolved_app",
        "domain_not_allowlisted",
        "app_not_allowlisted",
    }
)


class ToolBroker:
    """Brokers every tool call through schema, policy, approval, and audit."""

    def __init__(
        self,
        registry,
        policy,
        approvals,
        audit,
        lanes,
        *,
        notify,
        show_window,
        sessions,
        voice_provider=None,
        session_wait_seconds=10.0,
    ):
        self._registry = registry
        self._policy = policy
        self._approvals = approvals
        self._audit = audit
        self._lanes = lanes
        self._notify = notify
        self._show_window = show_window
        self._sessions = sessions
        self._voice_provider = voice_provider
        self._session_wait_seconds = session_wait_seconds

    # -- helpers -----------------------------------------------------------

    def _get_active(self):
        try:
            getter = getattr(self._sessions, "get_active", None)
            if not callable(getter):
                return None
            return getter()
        except Exception:
            return None

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        # 1. Invocation validation (no registry/policy/approval/audit/lane work).
        tool = getattr(invocation, "tool", None)
        args = getattr(invocation, "args", None)
        session_id = getattr(invocation, "session_id", None)
        source = getattr(invocation, "source", None)
        if (
            not isinstance(tool, str)
            or not tool
            or not isinstance(args, dict)
            or not isinstance(session_id, str)
            or not session_id
            or source not in ("ui", "voice")
        ):
            return ToolResult(False, "Invalid tool invocation.", "invalid_invocation")

        # 2. Registry lookup.
        try:
            spec = self._registry.get(tool)
        except Exception:
            spec = None
        if spec is None:
            return ToolResult(False, "Unknown or disabled tool.", "unknown_tool")

        spec_name = getattr(spec, "name", None) or tool
        spec_capabilities = getattr(spec, "capabilities", ())
        if spec_capabilities is None:
            spec_capabilities = ()

        # 3. Argument validation.
        parameters = getattr(spec, "parameters", None)
        if not isinstance(parameters, Mapping):
            return ToolResult(
                False, "Invalid arguments for this tool.", "invalid_arguments"
            )
        try:
            jsonschema.Draft202012Validator(parameters).validate(dict(args))
        except Exception:
            return ToolResult(
                False, "Invalid arguments for this tool.", "invalid_arguments"
            )

        # 4. Policy evaluation.
        try:
            decision_obj = self._policy.evaluate(spec, dict(args), DEFAULT_FEATURES)
        except Exception:
            decision_obj = PolicyDecision(
                Decision.DENY, "policy_error", getattr(spec, "risk", None) or ""
            )

        effective_risk = getattr(decision_obj, "effective_risk", None)
        risk = (
            effective_risk
            if isinstance(effective_risk, str) and effective_risk
            else getattr(spec, "risk", None)
        )
        reason = getattr(decision_obj, "reason", "policy_error")
        if not isinstance(reason, str) or not reason:
            reason = "policy_error"
        decision_value = getattr(decision_obj, "decision", None)

        is_deny = decision_value == Decision.DENY or decision_value == "deny"
        is_ask = decision_value == Decision.ASK or decision_value == "ask"
        is_allow = decision_value == Decision.ALLOW or decision_value == "allow"

        if is_deny or not (is_ask or is_allow):
            # Unknown verdicts fail closed as policy_error denies.
            deny_reason = reason if is_deny else "policy_error"
            try:
                self._audit.append(
                    "policy.decision",
                    decision="deny",
                    error_code=deny_reason,
                    tool=spec_name,
                    session_id=session_id,
                    source=source,
                    capabilities=spec_capabilities,
                    risk=risk,
                )
            except Exception:
                return ToolResult(
                    False,
                    "Tool execution was blocked because the audit log is unavailable.",
                    "audit_unavailable",
                )
            if deny_reason in _SCOPE_DENIED or "scope" in deny_reason:
                return ToolResult(
                    False,
                    "Requested scope is outside the allowed range.",
                    "scope_denied",
                )
            return ToolResult(False, "Denied by policy.", "policy_denied")

        if is_ask:
            # 5. Approval flow.
            active = self._get_active()
            if active is None:
                try:
                    show_result = self._show_window()
                    if inspect.isawaitable(show_result):
                        await show_result
                except Exception:
                    pass
                try:
                    wait_seconds = float(self._session_wait_seconds)
                except Exception:
                    wait_seconds = 0.0
                if wait_seconds < 0:
                    wait_seconds = 0.0
                deadline = time.monotonic() + wait_seconds
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(0.01, remaining))
                    active = self._get_active()
                    if active is not None:
                        break
                if active is None:
                    try:
                        self._audit.append(
                            "policy.decision",
                            decision="ask",
                            error_code="no_session",
                            tool=spec_name,
                            session_id=session_id,
                            source=source,
                            capabilities=spec_capabilities,
                            risk=risk,
                        )
                    except Exception:
                        pass
                    return ToolResult(
                        False, "Approval was not granted.", "approval_denied"
                    )

            if getattr(active, "session_id", None) != session_id or not getattr(
                active, "active", True
            ):
                try:
                    self._audit.append(
                        "policy.decision",
                        decision="ask",
                        error_code="session_mismatch",
                        tool=spec_name,
                        session_id=session_id,
                        source=source,
                        capabilities=spec_capabilities,
                        risk=risk,
                    )
                except Exception:
                    pass
                return ToolResult(
                    False, "Approval was not granted.", "approval_denied"
                )

            description = (
                getattr(spec, "description", None)
                or getattr(spec, "title", None)
                or getattr(spec, "name", None)
                or tool
            )
            can_grant = getattr(decision_obj, "can_grant", False)
            grant_scope = getattr(decision_obj, "grant_scope", None)

            try:
                request = self._approvals.create(
                    session_id=session_id,
                    source=source,
                    tool=spec_name,
                    capabilities=spec_capabilities,
                    risk=risk,
                    description=description,
                    args=args,
                    can_grant=can_grant,
                    grant_scope=grant_scope,
                )
            except ApprovalError as exc:
                if str(exc) == "audit_unavailable" or (
                    exc.args and exc.args[0] == "audit_unavailable"
                ):
                    return ToolResult(
                        False,
                        "Tool execution was blocked because the audit log is unavailable.",
                        "audit_unavailable",
                    )
                return ToolResult(
                    False, "Approval was not granted.", "approval_denied"
                )
            except Exception:
                return ToolResult(
                    False, "Approval was not granted.", "approval_denied"
                )

            # Exactly one flat approval_request notification.
            try:
                req_caps = getattr(request, "capabilities", spec_capabilities)
                capabilities_list = list(req_caps) if req_caps else []
            except Exception:
                capabilities_list = []
            args_redacted = getattr(request, "args_redacted", None)
            if not args_redacted:
                try:
                    args_redacted = redact_args(args)
                except Exception:
                    args_redacted = None
            event = {
                "type": "approval_request",
                "request_id": getattr(request, "request_id", None),
                "session_id": getattr(request, "session_id", session_id),
                "source": getattr(request, "source", source),
                "tool": getattr(request, "tool", spec_name),
                "capabilities": capabilities_list,
                "risk": getattr(request, "risk", None) or risk,
                "description": getattr(request, "description", None) or description,
                "args_redacted": args_redacted,
                "expires_at": getattr(request, "expires_at", None),
                "can_grant": getattr(request, "can_grant", can_grant),
                "grant_scope": getattr(request, "grant_scope", grant_scope),
            }
            try:
                notify_result = self._notify(event)
                if inspect.isawaitable(notify_result):
                    await notify_result
            except Exception:
                pass

            # Voice task: only for guarded voice invocations.
            voice_task = None
            if source == "voice" and risk == "guarded" and self._voice_provider is not None:
                voice_provider = self._voice_provider
                approvals_ref = self._approvals

                async def _run_voice():
                    try:
                        decided = voice_provider.decide(request)
                        if inspect.isawaitable(decided):
                            decided = await decided
                    except Exception:
                        try:
                            approvals_ref.resolve(
                                getattr(request, "session_id", session_id),
                                getattr(request, "request_id", None),
                                "deny",
                            )
                        except Exception:
                            pass
                        return
                    try:
                        if decided == VoiceResult.ALLOW or decided == "allow":
                            approvals_ref.resolve(
                                getattr(request, "session_id", session_id),
                                getattr(request, "request_id", None),
                                "allow",
                            )
                        elif decided == VoiceResult.DENY or decided == "deny":
                            approvals_ref.resolve(
                                getattr(request, "session_id", session_id),
                                getattr(request, "request_id", None),
                                "deny",
                            )
                        # DEFER: no resolution; UI request stays open.
                    except Exception:
                        pass

                voice_task = asyncio.create_task(_run_voice())

            approval_decision = None
            wait_failed = False
            try:
                approval_decision = await self._approvals.wait(request)
            except asyncio.CancelledError:
                raise
            except Exception:
                wait_failed = True
            finally:
                if voice_task is not None:
                    voice_task.cancel()
                    try:
                        await voice_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass

            if wait_failed:
                return ToolResult(
                    False, "Approval was not granted.", "approval_denied"
                )

            if approval_decision == ApprovalDecision.DENY or approval_decision == "deny":
                return ToolResult(
                    False, "Approval was not granted.", "approval_denied"
                )
            if (
                approval_decision == ApprovalDecision.ALLOW_ALWAYS
                or approval_decision == "allow_always"
            ):
                req_can_grant = getattr(request, "can_grant", False)
                req_grant_scope = getattr(request, "grant_scope", None)
                if req_can_grant is True and isinstance(req_grant_scope, str) and req_grant_scope:
                    try:
                        self._policy.add_grant(
                            spec,
                            req_grant_scope,
                            source=getattr(request, "source", source),
                        )
                    except Exception:
                        try:
                            self._audit.append(
                                "policy.decision",
                                decision="deny",
                                error_code="grant_failed",
                                tool=spec_name,
                                session_id=session_id,
                                source=source,
                                capabilities=spec_capabilities,
                                risk=risk,
                            )
                        except Exception:
                            pass
                        return ToolResult(False, "Denied by policy.", "policy_denied")
                # Non-grantable ALLOW_ALWAYS is treated as single-use ALLOW.
            elif approval_decision == ApprovalDecision.ALLOW or approval_decision == "allow":
                pass
            else:
                return ToolResult(
                    False, "Approval was not granted.", "approval_denied"
                )

        # 6. Pre-execution tool.request audit (before dispatch, always).
        request_id = uuid.uuid4().hex
        try:
            self._audit.append(
                "tool.request",
                session_id=session_id,
                source=source,
                tool=spec_name,
                capabilities=spec_capabilities,
                risk=risk,
                request_id=request_id,
                args=args,
            )
        except Exception:
            return ToolResult(
                False,
                "Tool execution was blocked because the audit log is unavailable.",
                "audit_unavailable",
            )

        # 7. Lane dispatch.
        try:
            value = await self._lanes.dispatch(spec, dict(args))
        except LaneTimeout:
            try:
                self._audit.append(
                    "tool.timeout",
                    session_id=session_id,
                    source=source,
                    tool=spec_name,
                    capabilities=spec_capabilities,
                    risk=risk,
                    request_id=request_id,
                    result_status="timeout",
                    error_code="tool_timeout",
                )
            except Exception:
                pass
            return ToolResult(False, "Tool timed out.", "tool_timeout")
        except Exception:
            try:
                self._audit.append(
                    "tool.error",
                    session_id=session_id,
                    source=source,
                    tool=spec_name,
                    capabilities=spec_capabilities,
                    risk=risk,
                    request_id=request_id,
                    result_status="error",
                    error_code="tool_error",
                )
            except Exception:
                pass
            return ToolResult(False, "Tool failed.", "tool_error")

        # 8. Terminal success audit (failure swallowed by design).
        try:
            self._audit.append(
                "tool.result",
                session_id=session_id,
                source=source,
                tool=spec_name,
                capabilities=spec_capabilities,
                risk=risk,
                request_id=request_id,
                result_status="ok",
            )
        except Exception:
            pass

        # 9. Result conversion.
        if isinstance(value, str):
            text = value
        elif value is None:
            text = "Done."
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except Exception:
                text = "Done."
        if len(text) > 8000:
            text = text[:8000] + "\n...[truncated]"
        return ToolResult(True, text, None)
