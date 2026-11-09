"""Policy-gated router for non-tool WebSocket control commands.

Every authenticated control message (``{"cmd": ...}``) is validated here
before it can reach server-side behaviour:

* a revoked session cannot issue any command;
* removed commands (``update_download``) are always rejected;
* unknown commands are rejected;
* destructive commands require an explicit boolean ``"confirm": true``;
* commands whose policy sets ``requires_audit`` are blocked when the
  ``command.request`` write fails, so no unaudited configuration change
  or deletion can happen.

``command.request`` is audited before dispatch and ``command.result``
after.  Commands outside the policy table (unknown, removed) carry no
capability to attribute and are therefore not audited.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.security.audit import AuditLog
from core.security.auth import Session


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one routed command."""

    ok: bool
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True)
class CommandPolicy:
    """Capability grant plus the gates a command must pass before dispatch."""

    capability: str
    requires_confirm: bool
    requires_audit: bool


COMMAND_POLICY: dict[str, CommandPolicy] = {
    "chat": CommandPolicy("assistant.control", False, False),
    "listen": CommandPolicy("assistant.control", False, False),
    "abort": CommandPolicy("assistant.control", False, False),
    "approval_response": CommandPolicy("approval.respond", False, True),
    "model": CommandPolicy("assistant.configure", False, True),
    "tts_settings": CommandPolicy("assistant.configure", False, True),
    "tts_test": CommandPolicy("assistant.configure", False, True),
    "wake_toggle": CommandPolicy("assistant.configure", False, True),
    "set_language": CommandPolicy("assistant.configure", False, True),
    "enroll": CommandPolicy("voice.enroll", False, True),
    "voice_reset": CommandPolicy("voice.delete", True, True),
    "wipe_memory": CommandPolicy("memory.delete", True, True),
    "reset": CommandPolicy("conversation.reset", True, True),
}

#: Commands whose server-side implementation was deleted; always rejected.
REMOVED_COMMANDS: frozenset[str] = frozenset({"update_download"})

#: Policy command -> ``CommandActions`` method name.  ``approval_response``
#: is deliberately absent: it is resolved through the ``ApprovalPort``.
_DISPATCH: dict[str, str] = {
    "listen": "start_listen",
    "abort": "abort",
    "chat": "chat",
    "model": "set_model",
    "reset": "reset",
    "wake_toggle": "wake_toggle",
    "tts_settings": "tts_settings",
    "tts_test": "tts_test",
    "enroll": "enroll",
    "voice_reset": "voice_reset",
    "wipe_memory": "wipe_memory",
    "set_language": "set_language",
}

_DENYING = frozenset({"confirm_required", "approval_rejected"})


class ApprovalPort(Protocol):
    """Resolves a request-bound approval decision; ``True`` grants it."""

    def resolve(self, session_id: str, request_id: str, decision: str) -> bool: ...


class CommandActions(Protocol):
    """Server-side command implementations.

    Every action receives the raw command message dict; extraction of
    message fields (``text``, ``model``, ``enabled``, ``voice``, ``rate``,
    ``lang`` ...) stays with the implementation, not the router.
    """

    async def start_listen(self, msg: dict) -> None: ...
    async def abort(self, msg: dict) -> None: ...
    async def chat(self, msg: dict) -> None: ...
    async def set_model(self, msg: dict) -> None: ...
    async def reset(self, msg: dict) -> None: ...
    async def wake_toggle(self, msg: dict) -> None: ...
    async def tts_settings(self, msg: dict) -> None: ...
    async def tts_test(self, msg: dict) -> None: ...
    async def enroll(self, msg: dict) -> None: ...
    async def voice_reset(self, msg: dict) -> None: ...
    async def wipe_memory(self, msg: dict) -> None: ...
    async def set_language(self, msg: dict) -> None: ...
    async def unknown(self, msg: dict) -> None: ...


class CommandRouter:
    """Validates, audits, and dispatches one command per session message.

    ``audit`` may be ``None`` before the runtime builds one: commands with
    ``requires_audit`` then fail closed with ``audit_unavailable`` while
    ``chat``/``listen``/``abort`` still dispatch.
    """

    def __init__(
        self,
        audit: AuditLog | None,
        approval_port: ApprovalPort,
        actions: CommandActions,
    ):
        self._audit = audit
        self._approval_port = approval_port
        self._actions = actions

    async def handle(self, session: Session, msg: dict) -> CommandResult:
        """Gate *msg* against the policy table and dispatch it exactly once."""
        cmd = msg.get("cmd")

        if session.revoked_at is not None:
            return CommandResult(False, "session_revoked", "session has been revoked")

        if not isinstance(cmd, str) or not cmd:
            await self._actions.unknown(msg)
            return CommandResult(False, "unknown_command", "unknown command")

        if cmd in REMOVED_COMMANDS:
            return CommandResult(False, "removed", f"{cmd} is no longer supported")

        policy = COMMAND_POLICY.get(cmd)
        if policy is None:
            await self._actions.unknown(msg)
            return CommandResult(False, "unknown_command", "unknown command")

        # Audit the request first: a command the log cannot record must not
        # run when the policy demands an audit trail, and refusals below are
        # recorded as command.result rows.
        if not self._audit_request(session, cmd, msg, policy) and policy.requires_audit:
            return CommandResult(
                False, "audit_unavailable", "audit log unavailable; command not executed"
            )

        if policy.requires_confirm and msg.get("confirm") is not True:
            result = CommandResult(
                False, "confirm_required", f"{cmd} requires confirm: true"
            )
        else:
            try:
                result = await self._dispatch(session, cmd, msg, policy)
            except Exception:
                self._audit_result(
                    session,
                    cmd,
                    msg,
                    policy,
                    CommandResult(False, "action_error", "action raised"),
                )
                raise
        self._audit_result(session, cmd, msg, policy, result)
        return result

    # -- dispatch ----------------------------------------------------------

    async def _dispatch(
        self, session: Session, cmd: str, msg: dict, policy: CommandPolicy
    ) -> CommandResult:
        if cmd == "approval_response":
            return self._resolve_approval(session, msg)
        action = getattr(self._actions, _DISPATCH[cmd])
        await action(msg)
        return CommandResult(True)

    def _resolve_approval(self, session: Session, msg: dict) -> CommandResult:
        request_id = msg.get("request_id")
        decision = msg.get("decision")
        if (
            not isinstance(request_id, str)
            or not request_id
            or not isinstance(decision, str)
            or not decision
        ):
            return CommandResult(
                False,
                "approval_rejected",
                "approval_response requires request_id and decision",
            )
        try:
            granted = bool(
                self._approval_port.resolve(session.session_id, request_id, decision)
            )
        except Exception:
            return CommandResult(
                False, "approval_rejected", "approval could not be resolved"
            )
        if granted:
            return CommandResult(True)
        return CommandResult(False, "approval_rejected", "approval was not granted")

    # -- audit -------------------------------------------------------------

    def _audit_request(
        self, session: Session, cmd: str, msg: dict, policy: CommandPolicy
    ) -> bool:
        """Write ``command.request``; ``False`` when no row could be written."""
        if self._audit is None:
            return False
        try:
            extra: dict = {}
            if cmd == "approval_response":
                request_id = msg.get("request_id")
                decision = msg.get("decision")
                extra["request_id"] = request_id if isinstance(request_id, str) else None
                extra["decision"] = decision if isinstance(decision, str) else None
            self._audit.append(
                "command.request",
                session_id=session.session_id,
                source="ws",
                capabilities=(policy.capability,),
                # Only the command name is recorded: message payloads such as
                # chat text do not belong in the audit trail.
                args={"cmd": cmd},
                **extra,
            )
            return True
        except Exception:
            return False

    def _audit_result(
        self,
        session: Session,
        cmd: str,
        msg: dict,
        policy: CommandPolicy,
        result: CommandResult,
    ) -> None:
        """Write ``command.result``; best effort, never raises."""
        if self._audit is None:
            return
        if result.ok:
            status = "ok"
        elif result.error_code in _DENYING:
            status = "denied"
        else:
            status = "error"
        extra: dict = {}
        if cmd == "approval_response":
            request_id = msg.get("request_id")
            decision = msg.get("decision")
            extra["request_id"] = request_id if isinstance(request_id, str) else None
            extra["decision"] = decision if isinstance(decision, str) else None
        try:
            self._audit.append(
                "command.result",
                session_id=session.session_id,
                source="ws",
                capabilities=(policy.capability,),
                result_status=status,
                error_code=result.error_code,
                **extra,
            )
        except Exception:
            pass
