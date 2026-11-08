"""Tests for SecretStore and plaintext-secret migration."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def test_secret_store_round_trip(tmp_path):
    from core.security.secrets import SecretStore

    vault_path = tmp_path / "config.env.vault"
    store = SecretStore(str(vault_path))
    store.set("ZEN_API_KEY", "sk-abc123")
    store.set("JARVIS_PIN", "123456")
    assert store.get("ZEN_API_KEY") == "sk-abc123"
    assert store.get("JARVIS_PIN") == "123456"
    assert store.all() == {"JARVIS_PIN": "123456", "ZEN_API_KEY": "sk-abc123"}
    store.delete("ZEN_API_KEY")
    assert store.get("ZEN_API_KEY") is None
    store2 = SecretStore(str(vault_path))
    assert store2.get("JARVIS_PIN") == "123456"
    assert store2.get("ZEN_API_KEY") is None


def test_vault_is_not_plaintext(tmp_path):
    from core.security.secrets import SecretStore

    vault_path = tmp_path / "config.env.vault"
    store = SecretStore(str(vault_path))
    store.set("ZEN_API_KEY", "sk-test-secret-value")
    raw = vault_path.read_bytes()
    assert b"sk-test-secret-value" not in raw
    data = json.loads(raw.decode("utf-8"))
    assert data["version"] == 1
    assert data["secrets"]["ZEN_API_KEY"].startswith("enc:")


def test_migrate_removes_plaintext_only_after_success(tmp_path):
    from core.security.secrets import SecretStore, migrate_plaintext_secrets

    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment line\nZEN_API_KEY=sk-migrate-secret\nTTS_VOICE=en-US-AndrewNeural\n",
        encoding="utf-8",
    )
    vault_path = tmp_path / ".env.vault"
    migrated = migrate_plaintext_secrets(str(env_path), str(vault_path))
    assert migrated == ["ZEN_API_KEY"]
    remaining = env_path.read_text(encoding="utf-8")
    assert "sk-migrate-secret" not in remaining
    assert "ZEN_API_KEY" not in remaining
    assert "TTS_VOICE=en-US-AndrewNeural" in remaining
    assert "# comment line" in remaining
    store = SecretStore(str(vault_path))
    assert store.get("ZEN_API_KEY") == "sk-migrate-secret"


def test_migrate_failure_keeps_plaintext(tmp_path, monkeypatch):
    from core import vault
    from core.security.secrets import SecretMigrationError, migrate_plaintext_secrets

    env_path = tmp_path / ".env"
    env_text = "# comment\nZEN_API_KEY=sk-keep-me\nTTS_VOICE=voice\n"
    env_path.write_text(env_text, encoding="utf-8")
    before = env_path.read_bytes()
    vault_path = tmp_path / ".env.vault"
    monkeypatch.setattr(vault, "encrypt", lambda plaintext: None)
    try:
        migrate_plaintext_secrets(str(env_path), str(vault_path))
    except SecretMigrationError:
        pass
    else:
        raise AssertionError("expected SecretMigrationError")
    assert env_path.read_bytes() == before


def test_save_settings_writes_secrets_to_vault(tmp_path, monkeypatch):
    import core.config as config
    from core.security.secrets import SecretStore

    env_path = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", str(env_path))
    config.save_settings({"ZEN_API_KEY": "sk-x", "TTS_VOICE": "voice"})
    env_text = env_path.read_text(encoding="utf-8")
    assert "TTS_VOICE=voice" in env_text
    assert "ZEN_API_KEY" not in env_text
    vault_path = str(env_path) + ".vault"
    store = SecretStore(vault_path)
    assert store.get("ZEN_API_KEY") == "sk-x"


def test_legacy_vault_is_migrated(tmp_path):
    from core import vault
    from core.security.secrets import SecretStore

    enc = vault.encrypt("sk-legacy-value")
    assert enc is not None
    vault_path = tmp_path / "config.env.vault"
    vault_path.write_text(f"ZEN_API_KEY=enc:{enc}\n", encoding="utf-8")
    store = SecretStore(str(vault_path))
    assert store.get("ZEN_API_KEY") == "sk-legacy-value"
    data = json.loads(vault_path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert (tmp_path / "config.env.vault.bak").exists()


def test_installer_does_not_write_secrets():
    installer_path = Path(__file__).resolve().parents[2] / "installer.iss"
    content = installer_path.read_text(encoding="utf-8")
    assert "ZEN_API_KEY=" not in content
    assert "JARVIS_PIN=" not in content
    assert "LICENSE_KEY=" not in content
