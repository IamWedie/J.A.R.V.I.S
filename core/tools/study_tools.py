"""Signed ``study_extract`` tool: read study files inside Documents only."""
from __future__ import annotations

import json
import os

from core.security.scopes import ScopeError, is_reparse_point, resolve_within_roots
from core.study import extractors

DOCUMENTS_ROOT = "~/Documents"


def _outside() -> ValueError:
    return ValueError("study_path_outside")


def extract_study_material(path, recursive=True, max_files=extractors.MAX_FILES_DEFAULT):
    """Extract text from *path* after proving it stays under Documents.

    The broker has already enforced the manifest scope. This re-check is
    defensive: a handler must never read a path outside its declared root.
    """
    if not isinstance(recursive, bool):
        raise ValueError("study_invalid_request")
    root = os.path.expanduser(DOCUMENTS_ROOT)
    try:
        resolved = resolve_within_roots(str(path), [root])
    except (ScopeError, TypeError, ValueError):
        raise _outside() from None
    if is_reparse_point(resolved):
        raise _outside()
    payload = extractors.extract_study_material(
        str(resolved),
        recursive=recursive,
        max_files=max_files,
        documents_root=root,
    )
    return json.dumps(payload, ensure_ascii=False)
