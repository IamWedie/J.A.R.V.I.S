"""Launch token, origin validation, and single control-session management."""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

ALLOWED_ORIGINS: frozenset[str] = frozenset({
    "http://127.0.0.1:8741",
    "http://localhost:8741",
})


@dataclass(frozen=True)
class LaunchToken:
    """One-time token embedded in the launch URL."""

    value: str

    @classmethod
    def generate(cls) -> "LaunchToken":
        return cls(secrets.token_urlsafe(32))

    def matches(self, candidate: str | None) -> bool:
        if not candidate:
            return False
        return secrets.compare_digest(self.value, candidate)


def origin_allowed(
    origin: str | None, allowed: frozenset[str] = ALLOWED_ORIGINS
) -> bool:
    """Exact membership check; the Origin header is never parsed or normalized."""
    return origin is not None and origin in allowed


@dataclass
class Session:
    session_id: str
    websocket: Any
    created_at: float
    revoked_at: float | None = None
    pending_approval_ids: set[str] = field(default_factory=set)

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class SessionManager:
    """Tracks created sessions and permits exactly one active control session."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._active: Session | None = None

    def create(self, websocket: Any) -> Session:
        session = Session(
            session_id=uuid.uuid4().hex,
            websocket=websocket,
            created_at=self._clock(),
        )
        self._sessions[session.session_id] = session
        return session

    def activate(self, session: Session) -> Session:
        known = self._sessions.get(session.session_id)
        if known is None:
            raise LookupError(f"unknown session: {session.session_id}")
        previous = self._active
        if previous is not None and previous is not known:
            previous.revoked_at = self._clock()
        known.revoked_at = None
        self._active = known
        return known

    def get_active(self) -> Session | None:
        return self._active

    def is_active(self, session_id: str) -> bool:
        return (
            self._active is not None
            and self._active.session_id == session_id
            and self._active.active
        )

    def revoke(self, session_id: str, reason: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        if session.revoked_at is None:
            session.revoked_at = self._clock()
        if self._active is session:
            self._active = None

    def close(self, session_id: str) -> None:
        self.revoke(session_id, "closed")
