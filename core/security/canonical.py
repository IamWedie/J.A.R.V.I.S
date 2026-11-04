"""Canonical JSON serialization shared by the audit log and manifest signing."""
from __future__ import annotations

import json


def canonical_json(value) -> bytes:
    """Deterministic UTF-8 JSON bytes: sorted keys, compact separators."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
