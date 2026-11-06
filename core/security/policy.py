"""Default-deny capability policy engine with scoped grants (Task 11).

Decision order in :meth:`PolicyEngine.evaluate` - first terminal rule wins:

1. corrupt policy             -> DENY  ``policy_corrupt``
2. unknown capability/risk    -> DENY  ``unknown_capability``
3. feature disabled           -> DENY  ``feature_disabled``
4. user override ``deny``     -> DENY  ``user_deny``
5. user override ``ask``      -> ASK   ``user_ask``
6. built-in decision          -> per effective risk and manifest scopes
7. matching unexpired grant   -> ALLOW ``grant``
8. user override ``allow``    -> only re-affirms step 7; never widens

The policy file is an HMAC-SHA256 envelope over the canonical JSON payload.
A bad HMAC or malformed envelope marks the engine ``corrupt``: evaluate
denies every tool and all mutating calls raise
:class:`PolicyIntegrityError`.  A corrupt file is never repaired or
overwritten in place.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.security.apps import ResolvedApp, app_allowed, resolve_app
from core.security.canonical import canonical_json
from core.security.capabilities import RISK_ORDER, ScopeError, capability_risk
from core.security.scopes import domain_allowed, inspect_url, resolve_within_roots

__all__ = [
    "Decision",
    "Grant",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyIntegrityError",
]


class Decision(str, Enum):
    """Terminal verdict of the policy engine."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PolicyIntegrityError(Exception):
    """Corrupt policy envelope, or a programmer contract violation."""


@dataclass(frozen=True)
class PolicyDecision:
    """Outcome of :meth:`PolicyEngine.evaluate`."""

    decision: Decision
    reason: str
    effective_risk: str
    grant_scope: str | None = None
    can_grant: bool = False


@dataclass(frozen=True)
class Grant:
    """A stored scoped grant for ``(tool, scope)``."""

    grant_id: str
    tool: str
    scope: str
    created_at: float
    expires_at: float
    source: str


#: Guarded tools allowed without any scope check.
_GUARDED_ALLOW = frozenset(
    {
        "set_volume",
        "get_volume",
        "media_key",
        "set_reminder",
        "set_alarm",
        "list_reminders",
        "cancel_reminder",
        "minimize_all_windows",
        "focus_window",
        "remember_fact",
    }
)

#: Guarded tools whose target is a fixed endpoint (args are never destinations).
_FIXED_ENDPOINT = frozenset({"web_search", "morning_briefing"})

#: Named filesystem locations understood by ``search_files``.
_LOCATION_ALIASES = frozenset(
    {"desktop", "documents", "downloads", "pictures", "videos", "music"}
)

#: Exact override values; anything else is ignored (never widens).
_OVERRIDE_VALUES = frozenset({"allow", "ask", "deny"})

_VERSION = 1


def _effective_risk(spec) -> tuple[str, str | None]:
    """Return ``(effective_risk, error)``; error set means unknown/invalid.

    ``effective_risk`` is the higher of the declared risk and the
    capability floor.  Never raises: malformed specs fail closed with
    ``error="unknown_capability"``.
    """
    declared = getattr(spec, "risk", None)
    declared_ok = isinstance(declared, str) and declared in RISK_ORDER
    try:
        cap = capability_risk(getattr(spec, "capabilities", None))
    except Exception:  # ScopeError, TypeError on malformed input
        cap = None
    # Both sides must be known risk keys; an unexpected capability_risk
    # result fails closed instead of raising KeyError in RISK_ORDER[...].
    cap_ok = isinstance(cap, str) and cap in RISK_ORDER
    if not cap_ok:
        return (declared if declared_ok else "", "unknown_capability")
    if not declared_ok:
        return (cap, "unknown_capability")
    risk = declared if RISK_ORDER[declared] >= RISK_ORDER[cap] else cap
    return (risk, None)


def _scope_kind(spec) -> tuple[str, str] | None:
    """``(kind, argument_name)`` supplying the tool's scope, or None.

    ``argument_scopes`` names the argument for ``filesystem``; ``domain``
    and ``app`` use their documented arguments.  When the mapping is
    missing, the documented per-tool defaults apply.
    """
    mapping = getattr(spec, "argument_scopes", None)
    if isinstance(mapping, Mapping):
        if "filesystem" in mapping:
            names = mapping["filesystem"]
            if (
                isinstance(names, Sequence)
                and not isinstance(names, (str, bytes))
                and names
                and isinstance(names[0], str)
                and names[0]
            ):
                return ("filesystem", names[0])
            return None
        if "domain" in mapping:
            return ("domain", "url")
        if "app" in mapping:
            return ("app", "query")
    defaults = {
        "search_files": ("filesystem", "location"),
        "open_url": ("domain", "url"),
        "launch_app": ("app", "query"),
    }
    name = getattr(spec, "name", None)
    return defaults.get(name) if isinstance(name, str) else None


def _norm_host(value) -> str:
    """Lowercase a host and strip exactly one trailing dot (never raises)."""
    if not isinstance(value, str):
        return ""
    text = value.lower()
    return text[:-1] if text.endswith(".") else text


def _requested_scope(spec, args) -> tuple[str, str] | None:
    """Canonical manifest-bounded ``(kind, scope)`` for *args*, or None.

    Only returns a scope that is inside the manifest maximum: a resolved
    path inside the filesystem roots, a host inside the egress
    allowlist, or a resolved+allowlisted app target.  Never raises.
    """
    kind_info = _scope_kind(spec)
    if kind_info is None:
        return None
    kind, arg = kind_info
    value = args.get(arg)
    if kind == "filesystem":
        if not isinstance(value, str) or not value:
            return None
        try:
            resolved = resolve_within_roots(value, spec.filesystem_roots)
        except (ScopeError, TypeError, ValueError):
            return None
        return ("filesystem", str(resolved))
    if kind == "domain":
        if not isinstance(value, str) or not value:
            return None
        check = inspect_url(value, spec.egress_allowlist)
        if not check.allowlisted or not check.host:
            return None
        return ("domain", check.host)
    # app
    if value is None:
        return None
    query = value if isinstance(value, str) else str(value)
    resolved = resolve_app(query)
    if resolved is None or not app_allowed(resolved, spec.app_allowlist):
        return None
    return ("app", os.path.normcase(resolved.target))


def _search_files(spec, args) -> tuple[Decision, str]:
    """Built-in decision for ``search_files`` (guarded, scoped)."""
    kind_info = _scope_kind(spec)
    arg = kind_info[1] if (kind_info and kind_info[0] == "filesystem") else "location"
    roots = getattr(spec, "filesystem_roots", None)
    if isinstance(roots, (str, bytes)) or not roots:
        roots = ()  # malformed or empty: there is no manifest root set
    value = args.get(arg)
    if value is None:
        # Missing -> "all manifest roots"; with no roots that is a search
        # over nothing, which must never become an unrestricted ALLOW.
        if not roots:
            return (Decision.ASK, "no_roots")
        return (Decision.ALLOW, "scoped_allow")
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text.lower() == "all":
            if not roots:
                return (Decision.ASK, "no_roots")
            return (Decision.ALLOW, "scoped_allow")
        alias = text.lower()
        if alias in _LOCATION_ALIASES:
            if not roots:
                return (Decision.ASK, "no_roots")
            for root in roots:
                base = os.path.basename(os.path.normpath(os.path.expanduser(root)))
                if base.casefold() == alias:
                    return (Decision.ALLOW, "scoped_allow")
            # Alias names a location but no manifest root matches it.
            return (Decision.ASK, "scoped_outside")
    try:
        resolve_within_roots(value, roots)
    except (ScopeError, TypeError, ValueError):
        return (Decision.ASK, "scoped_outside")
    return (Decision.ALLOW, "scoped_allow")


def _builtin(spec, args, risk: str) -> tuple[Decision, str]:
    """Built-in decision (step 6) for a validated spec."""
    if risk == "safe":
        return (Decision.ALLOW, "safe")
    if risk == "critical":
        return (Decision.DENY, "critical")
    if risk == "sensitive":
        return (Decision.ASK, "sensitive")
    # effective_risk == "guarded" from here on
    name = getattr(spec, "name", None)
    if not isinstance(name, str):
        return (Decision.ASK, "guarded_ask")
    if name in _GUARDED_ALLOW:
        return (Decision.ALLOW, "guarded_allowed")
    if name in _FIXED_ENDPOINT:
        if getattr(spec, "egress_allowlist", None):
            return (Decision.ALLOW, "fixed_endpoint")
        return (Decision.ASK, "guarded_ask")
    if name == "search_files":
        return _search_files(spec, args)
    if name == "open_url":
        value = args.get("url", "")
        check = inspect_url(value if isinstance(value, str) else "", spec.egress_allowlist)
        if check.allowlisted:
            return (Decision.ALLOW, "url_allowlisted")
        return (Decision.ASK, "url_not_allowlisted")
    if name == "launch_app":
        query = args.get("query", "")
        resolved = resolve_app(query if isinstance(query, str) else str(query))
        if resolved is not None and app_allowed(resolved, spec.app_allowlist):
            return (Decision.ALLOW, "app_allowlisted")
        return (Decision.ASK, "unresolved_app")
    return (Decision.ASK, "guarded_ask")


def _feature_enabled(features, feature) -> bool:
    """True only when *features* explicitly enables *feature* (fail closed)."""
    if features is None:
        return False
    fn = getattr(features, "enabled", None)
    if not callable(fn):
        return False
    try:
        return bool(fn(feature))
    except Exception:
        return False


def _parse_grant(entry) -> Grant | None:
    """Validate one stored grant entry; None when invalid (dropped)."""
    if not isinstance(entry, dict):
        return None
    gid = entry.get("id")
    tool = entry.get("tool")
    scope = entry.get("scope")
    source = entry.get("source")
    created = entry.get("created_at")
    expires = entry.get("expires_at")
    for value in (gid, tool, scope, source):
        if not isinstance(value, str) or not value:
            return None
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        return None
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        return None
    return Grant(gid, tool, scope, float(created), float(expires), source)


def _grant_entry(grant: Grant) -> dict:
    """Payload representation of *grant* (exact keys from the brief)."""
    return {
        "id": grant.grant_id,
        "tool": grant.tool,
        "scope": grant.scope,
        "created_at": grant.created_at,
        "expires_at": grant.expires_at,
        "source": grant.source,
    }


def _grant_in_manifest(spec, grant: Grant, kind: str) -> bool:
    """True when a stored grant scope still sits inside the spec maximum.

    An invalid or legacy grant (outside the current manifest roots, not on
    the egress allowlist, or no longer a resolvable allowlisted shortcut)
    is ignored rather than trusted.
    """
    try:
        if kind == "filesystem":
            resolve_within_roots(grant.scope, spec.filesystem_roots)
            return True
        if kind == "domain":
            return domain_allowed(_norm_host(grant.scope), spec.egress_allowlist)
        resolved = ResolvedApp(
            display_name=Path(grant.scope).stem, target=grant.scope
        )
        return app_allowed(resolved, spec.app_allowlist)
    except (ScopeError, TypeError, ValueError, OSError):
        return False


class PolicyEngine:
    """Default-deny capability policy with HMAC-protected scoped grants.

    The engine loads the signed policy file once at construction and never
    repairs it: a corrupt envelope makes :meth:`evaluate` deny everything
    and makes every mutation raise :class:`PolicyIntegrityError`.
    """

    def __init__(self, policy_path, hmac_key, audit=None, clock=time.time):
        if isinstance(hmac_key, (bytes, bytearray, memoryview)):
            key = bytes(hmac_key)
        else:
            key = b""
        if len(key) < 16:
            raise PolicyIntegrityError("hmac key must be bytes of length >= 16")
        self._key = key
        self._path = Path(os.fspath(policy_path))
        self.audit = audit
        self._clock = clock
        self._overrides: dict[str, str] = {}
        self._grants: list[Grant] = []
        self._status = "ok"
        self._load()

    # -- policy file -------------------------------------------------------

    def status(self) -> str:
        """``"ok"`` or ``"corrupt"``."""
        return self._status

    def _load(self) -> None:
        """Read and verify the envelope once; any defect means corrupt."""
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return  # missing file = defaults, status stays ok
        except OSError:
            self._status = "corrupt"
            return
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._status = "corrupt"
            return
        if not isinstance(envelope, dict):
            self._status = "corrupt"
            return
        version = envelope.get("version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != _VERSION
        ):
            self._status = "corrupt"
            return
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            self._status = "corrupt"
            return
        mac = envelope.get("hmac")
        if not isinstance(mac, str):
            self._status = "corrupt"
            return
        expected = hmac.new(
            self._key, canonical_json(payload), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(mac, expected):
            self._status = "corrupt"
            return
        overrides = payload.get("tool_overrides", {})
        grants = payload.get("grants", [])
        if not isinstance(overrides, dict) or not isinstance(grants, list):
            self._status = "corrupt"
            return
        # Invalid entries are dropped, never widened; status stays ok.
        for tool, value in overrides.items():
            if (
                isinstance(tool, str)
                and isinstance(value, str)
                and value in _OVERRIDE_VALUES
            ):
                self._overrides[tool] = value
        for entry in grants:
            grant = _parse_grant(entry)
            if grant is not None:
                self._grants.append(grant)

    # -- decisions ---------------------------------------------------------

    def evaluate(self, spec, args, features) -> PolicyDecision:
        """Apply the decision order; first terminal rule wins.

        Never raises for malformed arguments or helper failures: deny/ask
        fail closed (guarded -> ``ASK`` ``policy_error`` with
        ``can_grant=False``, anything else -> ``DENY``).  Policy load
        errors are raised at construction, not here, and are re-raised
        rather than swallowed.
        """
        risk, error = _effective_risk(spec)
        if self._status == "corrupt":
            return PolicyDecision(Decision.DENY, "policy_corrupt", risk)
        if error:
            return PolicyDecision(Decision.DENY, "unknown_capability", risk)
        feature = getattr(spec, "feature", None)
        if feature is not None and not _feature_enabled(features, feature):
            return PolicyDecision(Decision.DENY, "feature_disabled", risk)
        if not isinstance(args, Mapping):
            args = {}
        name = getattr(spec, "name", None)
        override = self._overrides.get(name) if isinstance(name, str) else None
        if override == "deny":
            return PolicyDecision(Decision.DENY, "user_deny", risk)
        if override == "ask":
            try:
                scope = _requested_scope(spec, args) if risk == "guarded" else None
            except PolicyIntegrityError:
                raise
            except Exception:
                scope = None  # helper failure: never widen, can_grant stays False
            return PolicyDecision(
                Decision.ASK,
                "user_ask",
                risk,
                scope[1] if scope else None,
                scope is not None,
            )
        # Built-in decision (step 6), then matching grants (step 7).
        # User ``allow`` (step 8) never widens: it changes nothing here.
        # Helper calls (inspect_url, resolve_app/app_allowed, expanduser/
        # normpath, grant matching) must never propagate an unexpected
        # exception: guarded tools fail closed to ASK, everything else
        # fails closed to DENY.
        try:
            decision, reason = _builtin(spec, args, risk)
            if decision is Decision.ASK and risk == "guarded":
                if reason == "no_roots":
                    return PolicyDecision(Decision.ASK, reason, risk, None, False)
                scope = _requested_scope(spec, args)
                if scope is None:
                    return PolicyDecision(Decision.ASK, reason, risk)
                kind, requested = scope
                if self._match_grant(spec, kind, requested):
                    return PolicyDecision(Decision.ALLOW, "grant", risk)
                return PolicyDecision(Decision.ASK, reason, risk, requested, True)
            return PolicyDecision(decision, reason, risk)
        except PolicyIntegrityError:
            raise  # raised by load/mutations only; never swallowed here
        except Exception:
            if risk == "guarded":
                return PolicyDecision(
                    Decision.ASK, "policy_error", risk, None, False
                )
            return PolicyDecision(Decision.DENY, "policy_error", risk)

    def _match_grant(self, spec, kind: str, requested: str) -> bool:
        """True when an unexpired manifest-bounded grant covers *requested*."""
        now = float(self._clock())
        for grant in self._grants:
            if grant.tool != spec.name or not now < grant.expires_at:
                continue
            if not _grant_in_manifest(spec, grant, kind):
                continue
            if kind == "filesystem":
                try:
                    resolve_within_roots(requested, [grant.scope])
                except (ScopeError, TypeError, ValueError):
                    continue
                return True
            if kind == "domain":
                if _norm_host(requested) == _norm_host(grant.scope):
                    return True
                continue
            if os.path.normcase(requested) == os.path.normcase(grant.scope):
                return True
        return False

    # -- grants ------------------------------------------------------------

    def add_grant(
        self, spec, scope: str, source: str, ttl_days: int = 30
    ) -> str:
        """Persist a scoped grant for a guarded tool; return its id."""
        if self._status == "corrupt":
            raise PolicyIntegrityError("policy file is corrupt")
        risk, error = _effective_risk(spec)
        if error or risk != "guarded":
            raise ValueError("grants require a guarded tool")
        if not isinstance(source, str) or not source:
            raise ValueError("grant source must be a non-empty string")
        # ttl_days must be a real, positive, finite number: bool, NaN,
        # +/-inf and non-numeric types would otherwise mint a grant that
        # never expires (or an immediate one).
        if isinstance(ttl_days, bool) or not isinstance(ttl_days, (int, float)):
            raise ValueError("invalid_ttl")
        try:
            ttl = float(ttl_days)
        except OverflowError:  # absurdly large int
            raise ValueError("invalid_ttl") from None
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("invalid_ttl")
        kind_info = _scope_kind(spec)
        if kind_info is None:
            raise ScopeError("unscoped_tool")
        kind = kind_info[0]
        if kind == "filesystem":
            stored = str(resolve_within_roots(scope, spec.filesystem_roots))
        elif kind == "domain":
            if not isinstance(scope, str) or not domain_allowed(
                scope, spec.egress_allowlist
            ):
                raise ScopeError("domain_not_allowlisted")
            try:
                # Store the canonical form: IDNA ASCII, lowercase, with the
                # trailing root dot removed, so matching compares equal hosts.
                stored = scope.encode("idna").decode("ascii").lower().rstrip(".")
            except (UnicodeError, ValueError):
                raise ValueError("invalid_domain") from None
        else:
            resolved = resolve_app(scope if isinstance(scope, str) else str(scope))
            if resolved is None or not app_allowed(resolved, spec.app_allowlist):
                raise ScopeError("app_not_allowlisted")
            stored = os.path.normcase(resolved.target)
        now = float(self._clock())
        grant = Grant(
            grant_id=uuid.uuid4().hex,
            tool=spec.name,
            scope=stored,
            created_at=now,
            expires_at=now + ttl * 86400.0,
            source=source,
        )
        self._grants.append(grant)
        try:
            # Audit first: if the audit append raises, nothing is persisted.
            self._audit_change("grant", grant, spec=spec, risk=risk)
            self._persist()
        except BaseException:
            self._grants.pop()
            raise
        return grant.grant_id

    def revoke_grant(self, grant_id: str) -> bool:
        """Remove *grant_id*; False when unknown, PolicyIntegrityError when corrupt."""
        if self._status == "corrupt":
            raise PolicyIntegrityError("policy file is corrupt")
        for index, grant in enumerate(self._grants):
            if grant.grant_id != grant_id:
                continue
            del self._grants[index]
            try:
                self._audit_change("revoke", grant)
                self._persist()
            except BaseException:
                self._grants.insert(index, grant)
                raise
            return True
        return False

    def list_grants(self) -> list[Grant]:
        """Every stored grant in file order; expiry is not filtered here."""
        return list(self._grants)

    # -- internals ---------------------------------------------------------

    def _audit_change(
        self, action: str, grant: Grant, *, spec=None, risk=None
    ) -> None:
        """Append a ``policy.change`` event (no-op when no audit log)."""
        if self.audit is None:
            return
        kwargs: dict = {
            "tool": grant.tool,
            "args": {
                "action": action,
                "grant_id": grant.grant_id,
                "scope": grant.scope,
                "source": grant.source,
            },
        }
        if spec is not None:
            kwargs["risk"] = risk
            kwargs["capabilities"] = spec.capabilities
        self.audit.append("policy.change", **kwargs)

    def _persist(self) -> None:
        """Atomically write the signed envelope (temp + fsync + replace)."""
        if self._status == "corrupt":
            raise PolicyIntegrityError("refusing to overwrite a corrupt policy")
        payload = {
            "tool_overrides": dict(self._overrides),
            "grants": [_grant_entry(grant) for grant in self._grants],
        }
        mac = hmac.new(
            self._key, canonical_json(payload), hashlib.sha256
        ).hexdigest()
        envelope = {"version": _VERSION, "payload": payload, "hmac": mac}
        text = (
            json.dumps(envelope, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".policy-", suffix=".tmp", dir=os.fspath(parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(tmp_name, 0o600)  # best-effort on Windows
            except OSError:
                pass
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
