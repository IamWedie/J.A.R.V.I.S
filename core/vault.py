"""JARVIS Secrets Vault — encrypts sensitive config using Windows DPAPI."""
import base64
import ctypes
import ctypes.wintypes
import os

from core.logging_setup import get_logger

log = get_logger("vault")

DPAPI_SIMPLE_BLOB = 0x01
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob_to_bytes(blob):
    return ctypes.string_at(blob.pbData, blob.cbData)


def _bytes_to_blob(data):
    blob = DATA_BLOB()
    blob.cbData = len(data)
    blob.pbData = ctypes.create_string_buffer(data, len(data))
    return blob


def encrypt(plaintext):
    try:
        data_in = _bytes_to_blob(plaintext.encode("utf-8"))
        data_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(data_in), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out)
        ):
            encrypted = _blob_to_bytes(data_out)
            ctypes.windll.kernel32.LocalFree(data_out.pbData)
            return base64.b64encode(encrypted).decode("ascii")
    except Exception as e:
        log.warning("DPAPI encrypt failed: %s", e)
    return None


def decrypt(encoded):
    try:
        encrypted = base64.b64decode(encoded)
        data_in = _bytes_to_blob(encrypted)
        data_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(data_in), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out)
        ):
            decrypted = _blob_to_bytes(data_out)
            ctypes.windll.kernel32.LocalFree(data_out.pbData)
            return decrypted.decode("utf-8")
    except Exception as e:
        log.warning("DPAPI decrypt failed: %s", e)
    return None


SECRET_KEYS = {"ZEN_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "PHONE_PIN", "JARVIS_PIN", "LICENSE_KEY"}


def encrypt_env_file(env_path):
    from core.security.secrets import migrate_plaintext_secrets

    return migrate_plaintext_secrets(env_path, os.fspath(env_path) + ".vault")


def decrypt_env_value(val):
    if val.startswith("enc:"):
        dec = decrypt(val[4:])
        return dec if dec else val[4:]
    return val


def load_vault(env_path):
    vault_path = env_path + ".vault"
    if not os.path.exists(vault_path):
        return
    result = {}
    with open(vault_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            result[key.strip()] = decrypt_env_value(val.strip())
    return result
