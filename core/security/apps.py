"""Start Menu application resolution and allowlist enforcement.

The heavy ``core.tools.pc_tools`` module stays out of the security layer:
this module only walks the trusted Start Menu roots for ``.lnk`` files and
proves that a resolved shortcut lives inside those roots.  Matching is exact
after normalization - never fuzzy, never an alias table, never a shell
lookup - and a display name alone is never sufficient to pass.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.security.scopes import is_reparse_point

__all__ = [
    "DEFAULT_START_MENU_DIRS",
    "ResolvedApp",
    "app_allowed",
    "normalize_name",
    "resolve_app",
]

DEFAULT_START_MENU_DIRS = [
    os.path.join(
        os.environ.get("ProgramData", r"C:\ProgramData"),
        "Microsoft", "Windows", "Start Menu", "Programs",
    ),
    os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs",
    ),
]


@dataclass(frozen=True)
class ResolvedApp:
    """A Start Menu shortcut proven to live inside the trusted roots."""

    display_name: str
    target: str
    kind: str = "shortcut"


def normalize_name(value: str) -> str:
    """Lowercase, trim, and collapse internal whitespace for exact matching."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).lower()


def _start_menu_roots(start_menu_dirs: Sequence[str] | None) -> list[str]:
    if start_menu_dirs is None:
        return list(DEFAULT_START_MENU_DIRS)
    return [root for root in start_menu_dirs if isinstance(root, str) and root]


def _root_realpaths(start_menu_dirs: Sequence[str] | None) -> list[str]:
    roots = []
    for root in _start_menu_roots(start_menu_dirs):
        try:
            roots.append(
                os.path.normcase(os.path.realpath(os.path.expanduser(root)))
            )
        except (OSError, ValueError):
            continue
    return roots


def _valid_shortcut(target: str, start_menu_dirs: Sequence[str] | None) -> bool:
    """An existing ``.lnk`` that is not a reparse point and lies in the roots."""
    if not isinstance(target, str) or not target:
        return False
    if not os.path.isfile(target):
        return False
    if Path(target).suffix.lower() != ".lnk":
        return False
    if is_reparse_point(target):
        return False
    roots = _root_realpaths(start_menu_dirs)
    if not roots:
        return False
    try:
        real = os.path.normcase(os.path.realpath(target))
    except (OSError, ValueError):
        return False
    for root in roots:
        try:
            if os.path.commonpath([root, real]) == root:
                return True
        except (ValueError, OSError):
            continue
    return False


def resolve_app(
    query: str, start_menu_dirs: Sequence[str] | None = None
) -> ResolvedApp | None:
    """Resolve *query* to a Start Menu shortcut by exact normalized stem.

    Only the given roots (or the defaults) are walked, only ``.lnk`` files
    whose stem equals the normalized query exactly are considered, and the
    first candidate that passes containment checks wins.  No token, prefix,
    fuzzy, alias, or shell matching ever runs; unknown queries return None.
    """
    normalized = normalize_name(query)
    if not normalized:
        return None
    candidates = []
    for root in _start_menu_roots(start_menu_dirs):
        base = os.path.expanduser(root)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for filename in filenames:
                if not filename.lower().endswith(".lnk"):
                    continue
                stem = os.path.splitext(filename)[0]
                if normalize_name(stem) == normalized:
                    candidates.append(os.path.join(dirpath, filename))
    candidates.sort(key=os.path.normcase)
    for target in candidates:
        if _valid_shortcut(target, start_menu_dirs):
            return ResolvedApp(
                display_name=Path(target).stem,
                target=os.path.abspath(target),
                kind="shortcut",
            )
    return None


def app_allowed(
    resolved: ResolvedApp,
    allowlist: Sequence[str],
    start_menu_dirs: Sequence[str] | None = None,
) -> bool:
    """True only when the allowlisted display name *and* target both pass.

    A display-name-only match (empty, missing, non-``.lnk``, reparse-point,
    or out-of-root target) is rejected.
    """
    if not isinstance(resolved, ResolvedApp):
        return False
    name = normalize_name(resolved.display_name)
    if not name or not resolved.target:
        return False
    try:
        allowed = {normalize_name(entry) for entry in allowlist}
    except TypeError:
        return False
    if name not in allowed:
        return False
    return _valid_shortcut(resolved.target, start_menu_dirs)
