"""Canonical scope matching for filesystem roots, domains, and URLs.

Security-critical primitives consumed by the policy engine:

- ``resolve_within_roots``: strict containment of a raw path inside the
  manifest's filesystem roots, rejecting UNC/device/root-relative paths,
  drive-relative paths, ``..`` components, and 8.3 short-name aliases
  *before* resolution and re-checking containment *after* it.
- ``domain_allowed`` / ``inspect_url``: exact and single-label wildcard
  domain matching plus SSRF-safe URL inspection (scheme, port, userinfo,
  and public-address checks).

``ScopeError`` is defined once in :mod:`core.security.capabilities` and
re-exported here so both import paths name the same class.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

from core.security.capabilities import (  # noqa: F401  (re-exported)
    CAPABILITY_MIN_RISK,
    RISK_ORDER,
    ScopeError,
)

__all__ = [
    "CAPABILITY_MIN_RISK",
    "RISK_ORDER",
    "ScopeError",
    "UrlCheck",
    "domain_allowed",
    "expand_root",
    "inspect_url",
    "is_reparse_point",
    "resolve_within_roots",
]

#: 8.3 short-name alias component, e.g. ``PROGRA~1``.
_SHORT_NAME_RE = re.compile(r"[^~]{1,6}~\d")

#: Characters a host or pattern may never contain (wildcards are handled
#: separately, before this check runs).
_FORBIDDEN_HOST_CHARS = frozenset(":/\\@%?#*")


def _has_control(value: str) -> bool:
    """True when *value* holds NUL or other control characters."""
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _has_control_or_space(value: str) -> bool:
    """True when *value* holds whitespace or control characters."""
    return any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)


def expand_root(root: str) -> Path:
    """Expand ``~`` and resolve a manifest root to an absolute path.

    Only ``os.path.expanduser`` runs - never ``expandvars`` - so environment
    injection cannot rewrite a root.
    """
    if not isinstance(root, str) or not root:
        raise ScopeError("empty_path")
    if _has_control(root):
        raise ScopeError("empty_path")
    expanded = os.path.expanduser(root)
    if not expanded:
        raise ScopeError("empty_path")
    return Path(expanded).resolve(strict=False)


def is_reparse_point(path: str | Path) -> bool:
    """True when *path* itself is a symlink (POSIX) or reparse point (Windows)."""
    if os.name != "nt":
        return os.path.islink(path)
    try:
        attributes = os.lstat(path).st_file_attributes
    except (OSError, ValueError, AttributeError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def resolve_within_roots(raw: str, roots: Sequence[str]) -> Path:
    """Resolve *raw* and prove it stays inside one of *roots*.

    Rejections raise :class:`ScopeError` with a short token message that
    never echoes the raw path: ``empty_path``, ``unc_path``,
    ``drive_relative``, ``dotdot``, ``short_name``, ``ads_stream``,
    ``invalid_component``, ``path_outside_roots``, ``reparse_point``,
    ``no_roots``.
    """
    if not isinstance(raw, str) or not raw:
        raise ScopeError("empty_path")
    if _has_control(raw):
        raise ScopeError("empty_path")
    if isinstance(roots, str) or not roots:
        raise ScopeError("no_roots")
    # UNC (\\server, //server), device (\\?\, \\.\), root-relative paths.
    if raw[0] in "\\/":
        raise ScopeError("unc_path")
    # Drive present but not a fully absolute X:\ or X:/ path -> C:foo.
    win = PureWindowsPath(raw)
    if win.drive:
        if not (
            len(raw) >= 3
            and raw[1] == ":"
            and raw[0].isalpha()
            and raw[2] in "\\/"
        ):
            raise ScopeError("drive_relative")
    # Component rules inspect everything after the drive part (``C:\``) and
    # any root part (``\``/``/``), so the drive colon never reaches the ADS
    # check while ``file.txt:stream`` still does.
    parts = win.parts[1:] if win.drive else win.parts
    if parts and parts[0] in ("\\", "/"):
        parts = parts[1:]
    for component in parts:
        if component == "..":
            raise ScopeError("dotdot")
        if _SHORT_NAME_RE.match(component):
            raise ScopeError("short_name")
        if ":" in component:
            raise ScopeError("ads_stream")
        # Windows strips trailing dots/spaces at the API level, turning
        # ``.. `` / ``...`` / ``name.`` into a different component.
        if component != component.rstrip(" ."):
            raise ScopeError("invalid_component")
    candidate = Path(raw).resolve(strict=False)
    try:
        candidate_real = os.path.normcase(os.path.realpath(candidate))
    except (ValueError, OSError):
        raise ScopeError("path_outside_roots") from None
    matched = False
    for root in roots:
        expanded = expand_root(root)
        try:
            root_real = os.path.normcase(os.path.realpath(expanded))
            common = os.path.commonpath([root_real, candidate_real])
        except (ValueError, OSError):
            # Different drives (or unreadable roots) cannot contain it.
            continue
        if common == root_real:
            matched = True
            break
    if not matched:
        raise ScopeError("path_outside_roots")
    for probe in (candidate, Path(raw)):
        try:
            exists = probe.exists()
        except OSError:
            exists = False
        if exists and is_reparse_point(probe):
            raise ScopeError("reparse_point")
    return candidate


def _normalize_domain(
    value: str, *, allow_wildcard: bool = False
) -> tuple[str, bool] | None:
    """Normalize a host or pattern to ``(ascii_name, is_wildcard)`` or None.

    Lowercases, strips exactly one trailing dot, IDNA-encodes, and enforces
    1..63 character labels without leading/trailing hyphens.  Wildcard
    patterns (``*.suffix``) are accepted only when *allow_wildcard* is set;
    a bare ``*``, a wildcard TLD, or any scheme/path/port/userinfo content
    makes the value invalid, and invalid values never match.
    """
    if not isinstance(value, str) or not value:
        return None
    lowered = value.lower()
    if _has_control_or_space(lowered):
        return None
    is_wildcard = False
    if lowered.startswith("*."):
        if not allow_wildcard:
            return None
        is_wildcard = True
        lowered = lowered[2:]
    if lowered.endswith("."):
        lowered = lowered[:-1]
    if not lowered:
        return None
    if any(ch in _FORBIDDEN_HOST_CHARS for ch in lowered):
        return None
    try:
        ascii_name = lowered.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return None
    for label in ascii_name.split("."):
        if not 1 <= len(label) <= 63:
            return None
        if label.startswith("-") or label.endswith("-"):
            return None
    return ascii_name, is_wildcard


def domain_allowed(host: str, patterns: Sequence[str]) -> bool:
    """True when *host* matches an exact or single-label wildcard pattern.

    ``*.example.com`` matches ``a.example.com`` but never the bare
    ``example.com`` and never ``a.b.example.com``.  Empty pattern lists and
    invalid patterns simply never match; this predicate never raises.
    """
    if not patterns:
        return False
    normalized = _normalize_domain(host)
    if normalized is None:
        return False
    host_name, _ = normalized
    host_labels = host_name.split(".")
    for pattern in patterns:
        parsed = _normalize_domain(pattern, allow_wildcard=True)
        if parsed is None:
            continue
        pattern_name, is_wildcard = parsed
        if not is_wildcard:
            if host_name == pattern_name:
                return True
            continue
        suffix_labels = pattern_name.split(".")
        # ``*.com`` (single-label suffix) can never match.
        if len(suffix_labels) < 2:
            continue
        if len(host_labels) == len(suffix_labels) + 1 and (
            host_labels[1:] == suffix_labels
        ):
            return True
    return False


@dataclass(frozen=True)
class UrlCheck:
    """Result of :func:`inspect_url`: the URL, its normalized host, verdict."""

    url: str
    host: str
    allowlisted: bool


def _public_unicast(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for a global, public unicast address."""
    if (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    return bool(address.is_global)


def _dns_addresses(host: str, port: int) -> list | None:
    """Resolve *host* to IP addresses, or None on any resolution failure."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError):
        return None
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except (ValueError, IndexError):
            return None
    if not addresses:
        return None
    return addresses


def inspect_url(url: str, allowlist: Sequence[str]) -> UrlCheck:
    """Inspect an untrusted URL without ever raising.

    Accepts only ``http``/``https`` on port 80/443 without userinfo, resolves
    the host (IP literals directly), and requires every resolved address to be
    a global public unicast address - loopback, private, link-local,
    multicast, reserved, unspecified, and DNS failures all reject.  The
    returned host is the normalized host, or ``""`` on any failure.
    """
    if not isinstance(url, str) or not url:
        return UrlCheck("", "", False)
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https"):
            return UrlCheck(url, "", False)
        netloc = parts.netloc
        if not netloc or "@" in netloc:
            return UrlCheck(url, "", False)
        if parts.username is not None or parts.password is not None:
            return UrlCheck(url, "", False)
        try:
            port = parts.port
        except ValueError:
            return UrlCheck(url, "", False)
        if port is None:
            port = 443 if parts.scheme.lower() == "https" else 80
        elif port not in (80, 443):
            return UrlCheck(url, "", False)
        try:
            host = parts.hostname
        except ValueError:
            return UrlCheck(url, "", False)
        if not host or _has_control_or_space(host):
            return UrlCheck(url, "", False)
        if "%" in host or "\\" in host:
            return UrlCheck(url, "", False)
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            addresses = [literal]
            normalized = host.lower()
        else:
            normalized_domain = _normalize_domain(host)
            if normalized_domain is None:
                return UrlCheck(url, "", False)
            normalized = normalized_domain[0]
            addresses = _dns_addresses(normalized, port)
            if addresses is None:
                return UrlCheck(url, "", False)
        for address in addresses:
            if not _public_unicast(address):
                return UrlCheck(url, "", False)
        return UrlCheck(url, normalized, domain_allowed(normalized, allowlist))
    except Exception:
        return UrlCheck(url if isinstance(url, str) else "", "", False)
