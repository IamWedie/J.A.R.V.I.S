"""Tests for canonical data directory, ACL, and legacy migration (Task 20).

All tests are hermetic: temp directories only, no real ``icacls``, no
network, no server/TestClient, no manifest import/verification, and no
real data-directory changes (``tests/conftest.py`` points
``JARVIS_DATA_DIR`` at a temp dir and ``JARVIS_TEST_MODE=1``).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.config as config
import core.memory as memory
import core.voiceid as voiceid
from core.security import data_migration, winacl
from core.tools import pc_tools

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "jarvis.spec"


def test_voiceid_data_dir_delegates_to_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "data_dir", lambda: str(tmp_path))
    assert voiceid.data_dir() == str(tmp_path)


def test_memory_db_is_under_config_data_dir():
    assert os.path.basename(memory.DB_PATH) == "memory.db"
    assert os.path.dirname(os.path.abspath(memory.DB_PATH)) == os.path.abspath(
        config.data_dir()
    )


def test_screenshot_dir_is_under_config_data_dir():
    assert os.path.basename(pc_tools.SCREENSHOT_DIR) == "screenshots"
    assert os.path.dirname(os.path.abspath(pc_tools.SCREENSHOT_DIR)) == os.path.abspath(
        config.data_dir()
    )


def test_acl_command_uses_current_user(monkeypatch, tmp_path):
    calls = []

    def _recorder(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(winacl, "_current_user", lambda: "TESTPC\\tester")
    monkeypatch.setattr(winacl.subprocess, "run", _recorder)

    winacl.restrict_to_current_user(tmp_path)

    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [
        "icacls",
        str(tmp_path),
        "/inheritance:r",
        "/grant:r",
        "TESTPC\\tester:(OI)(CI)F",
    ]
    assert kwargs.get("shell") is not True

    def _failing(argv, **kwargs):
        return SimpleNamespace(returncode=5)

    monkeypatch.setattr(winacl.subprocess, "run", _failing)
    with pytest.raises(OSError):
        winacl.restrict_to_current_user(tmp_path)


def test_legacy_data_is_copied_and_not_deleted(tmp_path):
    app_dir = tmp_path / "app"
    data_dir = tmp_path / "data"
    app_dir.mkdir()
    data_dir.mkdir()

    (app_dir / "memory.db").write_text("db", encoding="utf-8")
    voices_src = app_dir / "voices"
    voices_src.mkdir()
    (voices_src / "a.wav").write_text("wav", encoding="utf-8")
    (app_dir / ".env").write_text("KEY=1", encoding="utf-8")

    copied = data_migration.migrate_legacy_data(app_dir, data_dir)

    assert copied == ["memory.db", "voices", ".env"]
    assert (data_dir / "memory.db").read_text(encoding="utf-8") == "db"
    assert (data_dir / "voices" / "a.wav").read_text(encoding="utf-8") == "wav"
    assert (data_dir / ".env").read_text(encoding="utf-8") == "KEY=1"
    # Copies, never moves: sources must remain.
    assert (app_dir / "memory.db").exists()
    assert (app_dir / "voices" / "a.wav").exists()
    assert (app_dir / ".env").exists()
    assert list(data_dir.glob(".migrated-*"))


def test_legacy_migration_does_not_overwrite_existing(tmp_path):
    app_dir = tmp_path / "app"
    data_dir = tmp_path / "data"
    app_dir.mkdir()
    data_dir.mkdir()

    (app_dir / "memory.db").write_text("old", encoding="utf-8")
    (data_dir / "memory.db").write_text("new", encoding="utf-8")
    (app_dir / ".env").write_text("KEY=1", encoding="utf-8")
    voices_src = app_dir / "voices"
    voices_src.mkdir()
    (voices_src / "a.wav").write_text("wav", encoding="utf-8")

    copied = data_migration.migrate_legacy_data(app_dir, data_dir)

    assert (data_dir / "memory.db").read_text(encoding="utf-8") == "new"
    assert "memory.db" not in copied
    assert copied == ["voices", ".env"]
    assert (data_dir / "voices" / "a.wav").read_text(encoding="utf-8") == "wav"
    assert (data_dir / ".env").read_text(encoding="utf-8") == "KEY=1"


def test_frozen_spec_bundles_manifest_and_signature():
    text = SPEC_PATH.read_text(encoding="utf-8")
    assert '("core/security/tool_manifest.json", "core/security")' in text
    assert '("core/security/tool_manifest.sig", "core/security")' in text
