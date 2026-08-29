"""JARVIS Approval System — voice approval + Telegram PIN."""
import asyncio
import time

from core.logging_setup import get_logger

log = get_logger("approval")

VOICE_APPROVAL_TIMEOUT = 12
TELEGRAM_APPROVAL_TIMEOUT = 60


class ApprovalRequest:
    def __init__(self, tool_name, description, source="ui"):
        self.tool_name = tool_name
        self.description = description
        self.source = source
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        self.future = loop.create_future()
        self.created_at = time.time()
        self.resolved = False

    def resolve(self, approved):
        if not self.resolved:
            self.resolved = True
            if not self.future.done():
                self.future.set_result(approved)


_pending = {}
_voice_id_check = None

# Brute-force lockout state: source_id -> {"count": int, "locked_until": float}
_pin_attempts = {}


def _lockout_state(source_id):
    """Return the state dict only when currently locked out, else None."""
    key = str(source_id or "default")
    now = time.time()
    state = _pin_attempts.get(key)
    if state and state.get("locked_until", 0) > now:
        return state
    # expired -> reset
    if state and state.get("locked_until", 0):
        _pin_attempts.pop(key, None)
    return None


def pin_lockout_status(source_id=None):
    """Return dict describing PIN lockout for a source, or None if not locked."""
    state = _lockout_state(source_id)
    if not state or state.get("locked_until", 0) <= time.time():
        return {"locked": False, "remaining": 0, "attempts_left": _attempts_left(source_id)}
    remaining = max(1, int(state["locked_until"] - time.time()))
    return {"locked": True, "remaining": remaining, "attempts_left": 0}


def _attempts_left(source_id):
    from core import config
    state = _lockout_state(source_id)
    if not state:
        return _max_attempts()
    return max(0, _max_attempts() - state.get("count", 0))


def _max_attempts():
    from core import config
    return max(1, int(getattr(config, "PIN_MAX_ATTEMPTS", 5) or 5))


def _lockout_seconds():
    from core import config
    return max(1, int(getattr(config, "PIN_LOCKOUT_SECONDS", 300) or 300))


def _record_pin_failure(source_id):
    key = str(source_id or "default")
    max_attempts = _max_attempts()
    state = _pin_attempts.get(key) or {"count": 0, "locked_until": 0}
    state["count"] = state.get("count", 0) + 1
    if state["count"] >= max_attempts:
        state["locked_until"] = time.time() + _lockout_seconds()
        state["count"] = 0
        log.warning("PIN locked out for %s after %d failures", key, max_attempts)
    _pin_attempts[key] = state


def _record_pin_success(source_id):
    key = str(source_id or "default")
    _pin_attempts.pop(key, None)


def set_voice_id_checker(checker_fn):
    global _voice_id_check
    _voice_id_check = checker_fn


def create_request(tool_name, description, source="ui"):
    req = ApprovalRequest(tool_name, description, source)
    _pending[tool_name] = req
    log.info("approval needed: %s [%s] — %s", tool_name, source, description)
    return req


def resolve_by_pin(pin, tool_name=None, source_id=None):
    """Resolve a pending approval via PIN with brute-force lockout.

    Returns (bool, description). A source_id (e.g. Telegram chat id) scopes the
    lockout so one attacker can't be masked by another, and so a single source
    can be rate-limited. Returns False when locked out without even checking.
    """
    if _lockout_state(source_id):
        return False, None

    from core import config
    if tool_name and tool_name in _pending:
        req = _pending.pop(tool_name)
        correct = pin.strip() == getattr(config, "JARVIS_PIN", "")
        if correct:
            _record_pin_success(source_id)
            log.info("PIN approved: %s", tool_name)
            req.resolve(True)
            return True, req.description
        else:
            _record_pin_failure(source_id)
            log.warning("Wrong PIN for %s", tool_name)
            req.resolve(False)
            return False, req.description

    for name, req in list(_pending.items()):
        correct = pin.strip() == getattr(config, "JARVIS_PIN", "")
        if correct:
            _pending.pop(name)
            _record_pin_success(source_id)
            log.info("PIN approved: %s", name)
            req.resolve(True)
            return True, req.description
    if pin.strip() != getattr(config, "JARVIS_PIN", ""):
        _record_pin_failure(source_id)
    return False, None


def cancel_all():
    for name, req in list(_pending.items()):
        req.resolve(False)
    _pending.clear()


def get_pending():
    return dict(_pending)
