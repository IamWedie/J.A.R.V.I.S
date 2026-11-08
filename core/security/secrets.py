"""Encrypted secret store backed by a DPAPI vault file."""
import base64
import json
import logging
import os
import shutil
import tempfile

from core import vault

log = logging.getLogger("secrets")

SECRET_KEYS = frozenset(
    {
        "ZEN_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "PHONE_PIN",
        "JARVIS_PIN",
        "LICENSE_KEY",
    }
)


class SecretStoreError(RuntimeError):
    pass


class SecretMigrationError(RuntimeError):
    pass


class SecretStore:
    def __init__(self, vault_path):
        raw_path = os.fspath(vault_path)
        if isinstance(raw_path, bytes):
            raw_path = os.fsdecode(raw_path)
        self._path = raw_path
        if os.path.exists(self._path):
            self._entries = self._load_and_normalize()
        else:
            self._entries = {}

    def _load_and_normalize(self):
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except OSError as e:
            raise SecretStoreError(str(e)) from e

        parsed = None
        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, ValueError):
            parsed = None

        if (
            isinstance(parsed, dict)
            and parsed.get("version") == 1
            and isinstance(parsed.get("secrets"), dict)
        ):
            secrets = parsed["secrets"]
            valid_shape = True
            for key, value in secrets.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    valid_shape = False
                    break
            if valid_shape:
                return dict(secrets)

        entries = {}
        for line in raw_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                raise SecretStoreError("corrupt vault file")
            key, val = stripped.split("=", 1)
            key = key.strip()
            val = val.strip()
            if not key or not val.startswith("enc:"):
                raise SecretStoreError("corrupt vault file")
            payload = val[4:]
            if not payload:
                raise SecretStoreError("corrupt vault file")
            try:
                decrypted = vault.decrypt(payload)
            except Exception as e:
                raise SecretStoreError("corrupt vault file") from e
            if decrypted is None:
                raise SecretStoreError("corrupt vault file")
            entries[key] = val

        try:
            shutil.copyfile(self._path, self._path + ".bak")
        except OSError as e:
            raise SecretStoreError(str(e)) from e

        self._persist_entries(entries)
        return entries

    def _persist_entries(self, entries):
        parent = os.path.dirname(os.path.abspath(self._path))
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as e:
                raise SecretStoreError(str(e)) from e
        data = {"version": 1, "secrets": dict(entries)}
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(dir=parent, prefix=".vault-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
            except OSError:
                raise
            with open(temp_path, "r", encoding="utf-8") as f:
                verified = json.load(f)
            secrets = verified.get("secrets", {})
            for key, value in secrets.items():
                if not isinstance(value, str) or not value.startswith("enc:"):
                    raise SecretStoreError("vault verification failed")
                try:
                    decrypted = vault.decrypt(value[4:])
                except Exception as e:
                    raise SecretStoreError("vault verification failed") from e
                if decrypted is None:
                    raise SecretStoreError("vault verification failed")
            os.replace(temp_path, self._path)
            temp_path = None
        except SecretStoreError:
            if temp_path is not None and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            raise
        except Exception as e:
            if temp_path is not None and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            raise SecretStoreError(str(e)) from e

    def get(self, key):
        value = self._entries.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.startswith("enc:"):
            log.warning("undecryptable secret entry %s", key)
            return None
        try:
            decrypted = vault.decrypt(value[4:])
        except Exception:
            log.warning("undecryptable secret entry %s", key)
            return None
        if decrypted is None:
            log.warning("undecryptable secret entry %s", key)
            return None
        return decrypted

    def set(self, key, value):
        if not isinstance(key, str) or not key:
            raise SecretStoreError("key must be a non-empty string")
        if not isinstance(value, str):
            raise SecretStoreError("value must be a string")
        try:
            enc = vault.encrypt(value)
        except Exception as e:
            raise SecretStoreError("encryption failed") from e
        if enc is None:
            raise SecretStoreError("encryption failed")
        new_entries = dict(self._entries)
        new_entries[key] = "enc:" + str(enc)
        self._persist_entries(new_entries)
        self._entries = new_entries

    def delete(self, key):
        if key not in self._entries:
            return
        new_entries = dict(self._entries)
        del new_entries[key]
        self._persist_entries(new_entries)
        self._entries = new_entries

    def all(self):
        result = {}
        for key, value in list(self._entries.items()):
            if not isinstance(value, str) or not value.startswith("enc:"):
                log.warning("undecryptable secret entry %s", key)
                continue
            try:
                decrypted = vault.decrypt(value[4:])
            except Exception:
                log.warning("undecryptable secret entry %s", key)
                continue
            if decrypted is None:
                log.warning("undecryptable secret entry %s", key)
                continue
            result[key] = decrypted
        return result

    def migrate_env(self, env_path):
        env_path_str = os.fspath(env_path)
        if isinstance(env_path_str, bytes):
            env_path_str = os.fsdecode(env_path_str)
        if not os.path.exists(env_path_str):
            return []

        try:
            with open(env_path_str, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except OSError as e:
            raise SecretMigrationError(str(e)) from e

        lines = raw_text.splitlines()

        to_migrate = {}
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, val = stripped.split("=", 1)
            key = key.strip()
            val = val.strip()
            if key in SECRET_KEYS and val:
                to_migrate[key] = val

        if not to_migrate:
            return []

        try:
            for key in sorted(to_migrate):
                self.set(key, to_migrate[key])
                if self.get(key) != to_migrate[key]:
                    raise SecretMigrationError("secret migration verification failed")
        except SecretMigrationError:
            raise
        except Exception as e:
            raise SecretMigrationError(str(e)) from e

        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, val = stripped.split("=", 1)
                key = key.strip()
                val = val.strip()
                if key in SECRET_KEYS and val:
                    continue
            new_lines.append(line)

        env_dir = os.path.dirname(os.path.abspath(env_path_str))
        temp_path = None
        try:
            if env_dir:
                os.makedirs(env_dir, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(dir=env_dir, prefix=".env-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                    for line in new_lines:
                        f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            except OSError:
                raise
            os.replace(temp_path, env_path_str)
            temp_path = None
        except Exception as e:
            if temp_path is not None and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            raise SecretMigrationError(str(e)) from e

        return sorted(to_migrate)


def migrate_plaintext_secrets(env_path=None, vault_path=None):
    if env_path is None:
        import core.config as config

        env_path = config.ENV_PATH
    if vault_path is None:
        vault_path = os.fspath(env_path) + ".vault"
    store = SecretStore(vault_path)
    return store.migrate_env(env_path)
