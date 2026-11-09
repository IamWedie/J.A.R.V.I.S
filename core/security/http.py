"""HTTP token boundary and response hardening helpers."""

from __future__ import annotations

from html import escape

TOKEN_HEADER = "X-JARVIS-Token"

SECURITY_HEADERS: dict[str, str] = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' ws://127.0.0.1:8741 ws://localhost:8741"
    ),
    "Cache-Control": "no-store",
}


def inject_token_meta(html: str, token: str) -> str:
    """Insert the launch-token meta tag immediately after the opening head."""
    meta = f'<meta name="jarvis-token" content="{escape(token, quote=True)}">'
    marker = "<head>"
    index = html.lower().find(marker)
    if index == -1:
        return meta + "\n" + html
    insert_at = index + len(marker)
    return html[:insert_at] + "\n" + meta + html[insert_at:]


def security_headers() -> dict[str, str]:
    """Return a fresh copy so callers cannot mutate the shared defaults."""
    return dict(SECURITY_HEADERS)
