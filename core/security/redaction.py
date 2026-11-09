"""Log redaction helpers for secrets."""
import logging
import re

_SK_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
_JARV_RE = re.compile(r"JARV-[A-Z0-9-]{8,}")
_ASSIGN_RE = re.compile(
    r"(?P<prefix>(?:[\"']?\s*(?:[A-Za-z0-9_-]*(?:api[_\-]?key|token|secret|password|pin|license[_\-]?key)[A-Za-z0-9_-]*)\s*[\"']?\s*[:=]\s*))"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[^\s\"',\]}]+)"
    r"(?P<close>[\"']?)",
    re.IGNORECASE,
)


def redact(text):
    if not isinstance(text, str):
        return text

    def _repl(match):
        return match.group("prefix") + (match.group("quote") or "") + "[REDACTED]" + (match.group("close") or "")

    out = _ASSIGN_RE.sub(_repl, text)
    out = _SK_RE.sub("[REDACTED]", out)
    out = _JARV_RE.sub("[REDACTED]", out)
    return out


class SecretRedactionFilter(logging.Filter):
    def filter(self, record):
        try:
            message = record.getMessage()
            redacted = redact(message)
            if redacted != message:
                record.msg = redacted
                record.args = ()
        except Exception:
            pass
        return True
