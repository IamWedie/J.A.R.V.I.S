"""Request-bound, single-use approval manager.

An approval is a frozen :class:`ApprovalRequest` identified by a unique
``request_id`` and bounded by a TTL.  A waiter (:meth:`wait`) and a
decider (:meth:`resolve`) meet on one ``asyncio.Future`` per request:

* ``create`` validates all caller input, then writes
  ``approval.requested`` **before** storing the request -- nothing
  pending is ever unaudited, and a failed audit write stores nothing;
* ``wait`` awaits the future under the request's remaining TTL; a
  timeout writes ``approval.expired`` (best effort), removes the
  request, and returns ``DENY``;
* ``resolve`` validates in a fixed order.  Successful allow / deny /
  allow_always writes ``approval.resolved`` **before** consuming the
  request (an audit failure leaves it pending -- fail closed), while
  invalid attempts (replay, cross-session, malformed decision,
  non-grantable allow_always) write ``approval.rejected`` and **never**
  consume the request or touch its future;
* ``cancel_session`` / ``cancel_all`` deny every affected request and
  write ``approval.cancelled`` -- always, even if the audit write fails,
  and never raising.

Raw arguments never reach storage or audit rows: ``args`` is reduced to
``args_redacted`` via :func:`core.security.audit.redact_args` at
creation time, and descriptions are never audited.

Single-event-loop only: ``create``, the synchronous ``resolve``, and
``wait`` all run on the loop that owns the futures, so no lock is used
and no thread touches the pending table.
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import dataclass
from enum import Enum

from core.security.audit import redact_args
from core.security.capabilities import RISK_ORDER

#: Wire-level decisions ``resolve`` accepts (case-sensitive, exact).
_VALID_DECISIONS = frozenset({"allow", "deny", "allow_always"})

_TOOL_LIMIT = 100
_DESCRIPTION_LIMIT = 500
_REASON_LIMIT = 100


class ApprovalDecision(str, Enum):
    """Outcome of one approval request."""

    ALLOW = "allow"
    DENY = "deny"
    ALLOW_ALWAYS = "allow_always"


@dataclass(frozen=True)
class ApprovalRequest:
    """Immutable snapshot of one approval, created exactly once."""

    request_id: str
    session_id: str
    source: str
    tool: str
    capabilities: tuple[str, ...]
    risk: str
    description: str
    args_redacted: str
    created_at: float
    expires_at: float
    can_grant: bool
    grant_scope: str | None


class ApprovalError(ValueError):
    """Invalid caller input, unavailable audit, or programmer error.

    Wire-level bad decisions never raise: ``resolve`` returns ``False``.
    """


@dataclass(frozen=True)
class _Pending:
    """One entry of the pending table: the request and its future."""

    request: ApprovalRequest
    future: asyncio.Future


class ApprovalManager:
    """Creates, resolves, expires, and cancels request-bound approvals."""

    def __init__(self, audit, clock=time.time, ttl=60.0):
        if audit is None or not callable(getattr(audit, "append", None)):
            raise ApprovalError("audit_unavailable")
        if not callable(clock):
            raise ApprovalError("invalid_clock")
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
            raise ApprovalError("invalid_ttl")
        try:
            ttl_value = float(ttl)
        except (OverflowError, ValueError):
            raise ApprovalError("invalid_ttl") from None
        if not math.isfinite(ttl_value) or ttl_value <= 0:
            raise ApprovalError("invalid_ttl")
        self._audit = audit
        self._clock = clock
        self._ttl = ttl_value
        self._pending: dict[str, _Pending] = {}

    # -- creation ----------------------------------------------------------

    def create(
        self,
        *,
        session_id,
        source,
        tool,
        capabilities,
        risk,
        description,
        args,
        can_grant=False,
        grant_scope=None,
    ) -> ApprovalRequest:
        """Validate, audit ``approval.requested``, and register a request."""
        # -- validation, all before any audit write or storage -------------
        if not isinstance(session_id, str) or not session_id.strip():
            raise ApprovalError("invalid_session_id")
        if not isinstance(tool, str) or not tool.strip():
            raise ApprovalError("invalid_tool")
        tool = tool.strip()[:_TOOL_LIMIT]
        if source not in ("ui", "voice"):
            raise ApprovalError("invalid_source")
        if not isinstance(description, str) or not description.strip():
            raise ApprovalError("invalid_description")
        description = description[:_DESCRIPTION_LIMIT]
        if not isinstance(risk, str) or risk not in RISK_ORDER:
            raise ApprovalError("invalid_risk")
        if isinstance(capabilities, (str, bytes)):
            raise ApprovalError("invalid_capabilities")
        try:
            cap_tuple = tuple(capabilities)
        except TypeError:
            raise ApprovalError("invalid_capabilities") from None
        for cap in cap_tuple:
            if not isinstance(cap, str) or not cap.strip():
                raise ApprovalError("invalid_capabilities")
        if not isinstance(args, dict):
            raise ApprovalError("invalid_args")
        try:
            args_redacted = redact_args(args)
        except (TypeError, ValueError):
            raise ApprovalError("invalid_args") from None
        if not isinstance(can_grant, bool):
            raise ApprovalError("invalid_can_grant")
        if can_grant:
            if not isinstance(grant_scope, str) or not grant_scope.strip():
                raise ApprovalError("invalid_grant_scope")
        else:
            grant_scope = None  # never carry a scope without a grant
        try:
            created_at = float(self._clock())
        except (TypeError, ValueError, OverflowError):
            raise ApprovalError("invalid_clock") from None
        if not math.isfinite(created_at):
            raise ApprovalError("invalid_clock")

        request = ApprovalRequest(
            request_id=uuid.uuid4().hex,
            session_id=session_id,
            source=source,
            tool=tool,
            capabilities=cap_tuple,
            risk=risk,
            description=description,
            args_redacted=args_redacted,
            created_at=created_at,
            expires_at=created_at + self._ttl,
            can_grant=can_grant,
            grant_scope=grant_scope,
        )

        # The future belongs to the loop that will await ``wait``.
        try:
            future = asyncio.get_running_loop().create_future()
        except RuntimeError:
            raise ApprovalError("no_running_loop") from None

        # Audit BEFORE storing: an unaudited request never becomes pending.
        try:
            self._audit.append(
                "approval.requested",
                session_id=request.session_id,
                source=request.source,
                tool=request.tool,
                capabilities=request.capabilities,
                risk=request.risk,
                request_id=request.request_id,
                args_redacted=request.args_redacted,
            )
        except Exception:
            raise ApprovalError("audit_unavailable") from None

        self._pending[request.request_id] = _Pending(request, future)
        return request

    # -- waiting -----------------------------------------------------------

    async def wait(self, request) -> ApprovalDecision:
        """Await the decision for *request*; deny when unknown or expired."""
        if not isinstance(request, ApprovalRequest):
            raise ApprovalError("invalid_request")
        entry = self._pending.get(request.request_id)
        if entry is None:
            # Unknown, already resolved, cancelled, or expired: no new row.
            return ApprovalDecision.DENY
        future = entry.future
        remaining = max(0.0, request.expires_at - self._clock())
        if remaining == 0.0:
            self._expire(request.request_id)
            return ApprovalDecision.DENY
        try:
            # shield: the timeout cancels only the wrapper, never the
            # shared future the decider is about to set.
            decision = await asyncio.wait_for(
                asyncio.shield(future), timeout=remaining
            )
        except asyncio.TimeoutError:
            if not self._expire(request.request_id) and future.done():
                # Already gone: a resolve/cancel raced the timeout and won.
                return future.result()
            return ApprovalDecision.DENY
        except asyncio.CancelledError:
            # Shutdown path: never leave a dangling approval behind.
            self._pending.pop(request.request_id, None)
            if not future.done():
                future.set_result(ApprovalDecision.DENY)
            self._audit_cancelled(request, "waiter_cancelled")
            raise
        return decision

    # -- resolution --------------------------------------------------------

    def resolve(self, session_id: str, request_id: str, decision: str) -> bool:
        """Validate and apply one wire-level decision; ``True`` grants it.

        Validation order is fixed; the first failing check wins.  Invalid
        attempts never consume the request and never touch its future.
        """
        # 1. Wire-level shape. A malformed call is never a decision.
        if (
            not isinstance(session_id, str)
            or not session_id
            or not isinstance(request_id, str)
            or not request_id
            or not isinstance(decision, str)
            or not decision
        ):
            self._audit_rejected(session_id, request_id, decision, "invalid_decision")
            return False

        entry = self._pending.get(request_id)
        # 2. Request must exist (replays land here: it was consumed).
        if entry is None:
            self._audit_rejected(session_id, request_id, decision, "unknown_request")
            return False
        request = entry.request

        # 3. Session binding: a foreign session never consumes the request.
        if request.session_id != session_id:
            self._audit_rejected(
                session_id, request_id, decision, "session_mismatch"
            )
            return False

        # 4. Expiry: an expired request denies and is removed.
        if not (self._clock() < request.expires_at):
            self._expire(request_id)
            return False

        # 5. Exact, case-sensitive wire decision.
        if decision not in _VALID_DECISIONS:
            self._audit_rejected(
                session_id, request_id, decision, "invalid_decision"
            )
            return False

        # 6. allow_always only when the request is grantable.
        if decision == "allow_always" and not request.can_grant:
            self._audit_rejected(session_id, request_id, decision, "not_grantable")
            return False

        # 7. Success: audit BEFORE consuming (fail closed on audit failure,
        # so the UI may retry while the request stays pending).
        try:
            self._audit.append(
                "approval.resolved",
                session_id=request.session_id,
                source=request.source,
                tool=request.tool,
                capabilities=request.capabilities,
                risk=request.risk,
                request_id=request.request_id,
                decision=decision,
                approver="ui",
            )
        except Exception:
            return False
        self._pending.pop(request_id, None)
        if not entry.future.done():
            entry.future.set_result(ApprovalDecision(decision))
        # An explicit "deny" is a successful resolution (resolved + DENY
        # future) that still reports "not granted" to the caller.
        return decision in ("allow", "allow_always")

    # -- cancellation and queries ------------------------------------------

    def cancel_session(self, session_id: str, reason: str = "session_closed") -> int:
        """Deny every pending request of *session_id*; return the count."""
        if not isinstance(reason, str) or not reason:
            reason = "session_closed"
        else:
            reason = reason[:_REASON_LIMIT]
        doomed = [
            entry
            for entry in self._pending.values()
            if entry.request.session_id == session_id
        ]
        for entry in doomed:
            self._cancel_entry(entry, reason)
        return len(doomed)

    def cancel_all(self) -> int:
        """Deny every pending request; return the count."""
        doomed = list(self._pending.values())
        for entry in doomed:
            self._cancel_entry(entry, "session_closed")
        return len(doomed)

    def pending_for(self, session_id: str) -> list[ApprovalRequest]:
        """Pending requests of *session_id* in creation order (no mutation)."""
        return [
            entry.request
            for entry in self._pending.values()
            if entry.request.session_id == session_id
        ]

    # -- internals ---------------------------------------------------------

    def _expire(self, request_id: str) -> bool:
        """Deny and remove a pending request; ``False`` if it was already gone.

        Never raises: the audit write is best effort.
        """
        entry = self._pending.pop(request_id, None)
        if entry is None:
            return False
        request = entry.request
        if not entry.future.done():
            entry.future.set_result(ApprovalDecision.DENY)
        try:
            self._audit.append(
                "approval.expired",
                session_id=request.session_id,
                source=request.source,
                tool=request.tool,
                capabilities=request.capabilities,
                risk=request.risk,
                request_id=request.request_id,
            )
        except Exception:
            pass
        return True

    def _cancel_entry(self, entry: _Pending, reason: str) -> None:
        """Deny, remove, and audit one entry; never raises."""
        self._pending.pop(entry.request.request_id, None)
        if not entry.future.done():
            entry.future.set_result(ApprovalDecision.DENY)
        self._audit_cancelled(entry.request, reason)

    def _audit_cancelled(self, request: ApprovalRequest, reason: str) -> None:
        """Best-effort ``approval.cancelled`` row; never raises."""
        try:
            self._audit.append(
                "approval.cancelled",
                session_id=request.session_id,
                source=request.source,
                tool=request.tool,
                capabilities=request.capabilities,
                risk=request.risk,
                request_id=request.request_id,
                error_code=reason,
            )
        except Exception:
            pass

    def _audit_rejected(
        self, session_id, request_id, decision, reason: str
    ) -> None:
        """Best-effort ``approval.rejected`` row; never raises.

        ``session_id``/``request_id``/``decision`` are the caller's own
        values (non-strings become NULL); tool/capabilities/risk appear
        only when the request is actually in the pending table.
        """
        entry = (
            self._pending.get(request_id) if isinstance(request_id, str) else None
        )
        request = entry.request if entry is not None else None
        fields: dict = {}
        if request is not None:
            fields = {
                "tool": request.tool,
                "capabilities": request.capabilities,
                "risk": request.risk,
            }
        try:
            self._audit.append(
                "approval.rejected",
                session_id=session_id,
                source="ui",
                request_id=request_id if isinstance(request_id, str) else None,
                decision=decision if isinstance(decision, str) else None,
                error_code=reason,
                **fields,
            )
        except Exception:
            pass
