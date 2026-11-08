"""One-time copy of legacy app-directory data into the canonical data dir.

Copies only; never moves or overwrites. Each item is best-effort so one
locked file cannot stop the remaining items.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime

from core.logging_setup import get_logger

log = get_logger("data_migration")

LEGACY_ITEMS = [
    "memory.db",
    "voices",
    ".env",
    ".env.vault",
    "screenshots",
]


def migrate_legacy_data(app_dir, data_dir) -> list[str]:
    src_root = os.path.abspath(os.fspath(app_dir))
    dst_root = os.path.abspath(os.fspath(data_dir))
    if os.path.normcase(src_root) == os.path.normcase(dst_root):
        return []
    os.makedirs(dst_root, exist_ok=True)

    copied: list[str] = []
    for name in LEGACY_ITEMS:
        src = os.path.join(src_root, name)
        dst = os.path.join(dst_root, name)
        if not os.path.exists(src) or os.path.exists(dst):
            continue
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        except OSError as e:
            log.warning("legacy data migration failed for %s: %s", name, e)
            continue
        copied.append(name)

    if copied:
        try:
            now = datetime.now()
            marker_name = f".migrated-{now.strftime('%Y%m%d-%H%M%S')}"
            marker_path = os.path.join(dst_root, marker_name)
            if os.path.exists(marker_path):
                marker_path = os.path.join(
                    dst_root, f"{marker_name}-{now.strftime('%f')}"
                )
            with open(marker_path, "w", encoding="utf-8", newline="\n") as handle:
                for name in copied:
                    handle.write(name + "\n")
        except OSError as e:
            log.warning("legacy data migration marker failed: %s", e)

    return copied
