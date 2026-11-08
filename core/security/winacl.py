"""Windows ACL hardening for the canonical data directory.

Restricts a directory to the current Windows user via ``icacls`` using a
list argv with no shell, so no user-controlled argument reaches a shell.
"""

from __future__ import annotations

import getpass
import os
import subprocess


def _current_user() -> str:
    user = (os.environ.get("USERNAME", "") or "").strip()
    if not user:
        user = getpass.getuser().strip()
    domain = (os.environ.get("USERDOMAIN", "") or "").strip()
    if domain:
        return f"{domain}\\{user}"
    return user


def restrict_to_current_user(path, username=None) -> None:
    user = (username or _current_user()).strip()
    if not user:
        raise OSError("empty user for ACL restriction")
    result = subprocess.run(
        ["icacls", os.fspath(path), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise OSError("icacls ACL restriction failed")
