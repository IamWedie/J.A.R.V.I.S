"""Known tool capabilities and their minimum risk floors.

A tool's declared risk must be at least the highest minimum risk of the
capabilities it requests (``safe < guarded < sensitive < critical``).
Any capability name absent from this table is unknown and rejected when a
manifest is loaded.
"""
from __future__ import annotations

#: Risk severity order, lowest first.
RISK_ORDER: dict[str, int] = {
    "safe": 0,
    "guarded": 1,
    "sensitive": 2,
    "critical": 3,
}

#: Minimum risk a tool must declare to use each capability.
CAPABILITY_MIN_RISK: dict[str, str] = {
    "system.read": "safe",
    "system.control": "guarded",
    "system.power": "sensitive",
    "process.launch": "guarded",
    "process.terminate": "sensitive",
    "window.control": "guarded",
    "input.keyboard": "sensitive",
    "input.pointer": "sensitive",
    "clipboard.read": "sensitive",
    "clipboard.write": "sensitive",
    "filesystem.read": "guarded",
    "filesystem.write": "sensitive",
    "filesystem.delete": "sensitive",
    "network.read": "guarded",
    "network.egress": "sensitive",
    "screen.capture": "sensitive",
    "screen.ocr": "sensitive",
    "audio.input": "sensitive",
    "audio.output": "guarded",
    "memory.read": "sensitive",
    "memory.write": "guarded",
    "data.egress": "sensitive",
    "media.control": "guarded",
    "phone.control": "critical",
    "phone.comms": "critical",
    "security.admin": "critical",
    "audit.read": "sensitive",
}

#: The exact set of known capability names (27).
CAPABILITIES: frozenset[str] = frozenset(CAPABILITY_MIN_RISK)
