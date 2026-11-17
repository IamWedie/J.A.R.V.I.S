from __future__ import annotations

import os
import re

from core.security.scopes import ScopeError, resolve_within_roots

DOCUMENTS_ROOT = "~/Documents"
_SHORT_NAME_RE = re.compile(r"[^~]{1,6}~\d")
_DRIVE_RE = re.compile(r"^[A-Za-z]:$")
_DRIVE_RELATIVE_RE = re.compile(r"^[A-Za-z]:(?![\\/])")


class StudyPathError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _root(documents_root: str | None) -> str:
    return documents_root or os.path.expanduser(DOCUMENTS_ROOT)


def _invalid(message: str = "") -> StudyPathError:
    return StudyPathError("study_path_invalid", message or "Invalid study path.")


def normalize_study_path(raw: str, *, documents_root: str | None = None) -> str:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise _invalid()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise _invalid()

    root = _root(documents_root)

    if raw.startswith("~"):
        if raw == "~" or raw.startswith("~\\") or raw.startswith("~/"):
            raw = os.path.expanduser("~") + raw[1:]
        else:
            raise _invalid()

    if raw.startswith(("\\\\", "//", "\\", "/", "\\\\?\\", "\\\\.\\")):
        raise _invalid()
    if _DRIVE_RELATIVE_RE.match(raw):
        raise _invalid()

    normalized = raw.replace("/", "\\")
    parts = [part for part in normalized.split("\\") if part]
    for index, part in enumerate(parts):
        if index == 0 and _DRIVE_RE.match(part):
            continue
        if part in (".", "..") or ":" in part or part.endswith((".", " ")):
            raise _invalid()
        if _SHORT_NAME_RE.match(part):
            raise _invalid()

    if not os.path.isabs(normalized):
        if parts and parts[0].casefold() == "documents":
            parts = parts[1:]
        normalized = os.path.join(root, *parts)

    try:
        resolved = resolve_within_roots(normalized, [root])
    except (ScopeError, TypeError, ValueError):
        raise StudyPathError("study_path_outside", "Path is outside Documents.") from None
    return str(resolved)


def documents_relative(path: str, *, documents_root: str | None = None) -> str:
    root = _root(documents_root)
    try:
        relative = os.path.relpath(path, root)
    except (TypeError, ValueError):
        return os.path.basename(path)
    if relative == ".":
        return "Documents"
    if relative.startswith(".."):
        return os.path.basename(path)
    return relative.replace("/", "\\")
