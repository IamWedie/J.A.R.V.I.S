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


def set_voice_id_checker(checker_fn):
    global _voice_id_check
    _voice_id_check = checker_fn


def create_request(tool_name, description, source="ui"):
    req = ApprovalRequest(tool_name, description, source)
    _pending[tool_name] = req
    log.info("approval needed: %s [%s] — %s", tool_name, source, description)
    return req


def resolve_by_pin(pin, tool_name=None):
    from core import config
    if tool_name and tool_name in _pending:
        req = _pending.pop(tool_name)
        correct = pin.strip() == getattr(config, "JARVIS_PIN", "")
        if correct:
            log.info("PIN approved: %s", tool_name)
            req.resolve(True)
            return True, req.description
        else:
            log.warning("Wrong PIN for %s", tool_name)
            req.resolve(False)
            return False, req.description

    for name, req in list(_pending.items()):
        correct = pin.strip() == getattr(config, "JARVIS_PIN", "")
        if correct:
            _pending.pop(name)
            log.info("PIN approved: %s", name)
            req.resolve(True)
            return True, req.description
    return False, None


def cancel_all():
    for name, req in list(_pending.items()):
        req.resolve(False)
    _pending.clear()


def get_pending():
    return dict(_pending)
