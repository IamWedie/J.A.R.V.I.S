import os
import sys

from dotenv import load_dotenv

FROZEN = getattr(sys, "frozen", False)


def data_dir():
    base = os.environ.get("JARVIS_DATA_DIR")
    if not base:
        if FROZEN:
            base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "JARVIS")
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(base, exist_ok=True)
    return base


PROJECT_DIR = data_dir()
ENV_PATH = os.path.join(PROJECT_DIR, "config.env" if FROZEN else ".env")
load_dotenv(ENV_PATH)

try:
    from core.vault import load_vault
    _vault = load_vault(ENV_PATH)
    if _vault:
        for k, v in _vault.items():
            os.environ[k] = v
except Exception:
    pass

ASSISTANT_NAME = "JARVIS"

def _read_version():
    try:
        vpath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")
        with open(vpath, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        import re
        m = re.search(r'"([^"]+)"', raw)
        return m.group(1) if m else raw
    except Exception:
        return "2.1.0"

VERSION = _read_version()

ZEN_API_KEY = os.getenv("ZEN_API_KEY", "").strip()
ZEN_BASE_URL = "https://opencode.ai/zen/v1"
DEFAULT_MODEL = os.getenv("ZEN_MODEL", "").strip() or "x-preview-f-free"

STT_MODEL = os.getenv("STT_MODEL", "").strip() or "tiny.en"
TTS_VOICE = os.getenv("TTS_VOICE", "").strip() or "en-US-AndrewNeural"
TTS_RATE = os.getenv("TTS_RATE", "").strip() or "+30%"
STARTUP_SOUND = os.getenv("STARTUP_SOUND", "").strip()

WAKE_ENABLED_DEFAULT = os.getenv("WAKE_ENABLED", "").strip().lower() in ("1", "true", "yes")
CONVERSATION_TIMEOUT = int(os.getenv("CONVERSATION_TIMEOUT", "60").strip() or "60")
STT_LANG = os.getenv("STT_LANG", "").strip() or None
MULTILINGUAL = os.getenv("MULTILINGUAL", "").strip().lower() in ("1", "true", "yes")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
LICENSE_SECRET = os.getenv("LICENSE_SECRET", "").strip()  # legacy (unused w/ Ed25519)
LICENSE_SERVER_URL = os.getenv("LICENSE_SERVER_URL", "").strip().rstrip("/")
LICENSE_KEY = os.getenv("LICENSE_KEY", "").strip()
PHONE_PIN = os.getenv("PHONE_PIN", "").strip()
JARVIS_PIN = os.getenv("JARVIS_PIN", "").strip()
JARVIS_PIN_MIN_LENGTH = int(os.getenv("JARVIS_PIN_MIN_LENGTH", "6").strip() or "6")
PIN_MAX_ATTEMPTS = int(os.getenv("PIN_MAX_ATTEMPTS", "5").strip() or "5")
PIN_LOCKOUT_SECONDS = int(os.getenv("PIN_LOCKOUT_SECONDS", "300").strip() or "300")
PHONE_ADDR = os.getenv("PHONE_ADDR", "").strip()
PHONE_PORT = int((os.getenv("PHONE_PORT", "5555").strip() or "5555"))
PHONE_SERIAL = os.getenv("PHONE_SERIAL", "").strip()
LOCAL_VISION_URL = ""  # disabled until Phase D
LOCAL_VISION_MODEL = ""  # disabled until Phase D


def resolve_path(p):
    if not p:
        return ""
    if os.path.isabs(p):
        return p
    return os.path.join(PROJECT_DIR, p)


def models_dir():
    cand = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
    return cand if os.path.isdir(cand) else ""

HOST = "127.0.0.1"
PORT = 8741


def save_settings(updates):
    lines = []
    existing = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k, v = stripped.split("=", 1)
                    existing[k.strip()] = v.strip()
                else:
                    lines.append(line)
    for k, v in updates.items():
        existing[k] = str(v)
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
        for k, v in existing.items():
            f.write(f"{k}={v}\n")


def validate_pin(pin):
    """Return (bool, reason) for a proposed JARVIS_PIN.

    Public-release safety: the approval PIN must not be trivially guessable, and
    must not fall back to a default when unset. Keys are checked against a small
    blocklist of the most common defaults (e.g. '0910', '1234', '0000')."""
    pin = (pin or "").strip()
    if not pin:
        return False, "PIN is not set — set JARVIS_PIN (at least {} characters).".format(JARVIS_PIN_MIN_LENGTH)
    if len(pin) < JARVIS_PIN_MIN_LENGTH:
        return False, "PIN is too short — minimum {} characters.".format(JARVIS_PIN_MIN_LENGTH)
    if pin.isdigit() and len(pin) <= 6 and pin in _WEAK_NUMERIC_PINS:
        return False, "PIN is a common default — choose something harder to guess."
    return True, ""


_WEAK_NUMERIC_PINS = {
    "0", "00", "000", "0000", "00000", "000000",
    "1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999",
    "1234", "12345", "123456", "4321", "9876", "2580",
    "0910", "1004", "1212", "1122", "6969", "1590", "7777", "654321", "112233",
    "000000", "0101", "0011", "1010", "111",
}


def pin_is_strong():
    """True if the configured JARVIS_PIN passes validate_pin."""
    valid, _ = validate_pin(JARVIS_PIN)
    return valid


VOICE_CHOICES = [
    ("en-US-AndrewNeural", "Andrew (lively US male)"),
    ("en-US-BrianNeural", "Brian (casual US male)"),
    ("en-US-AvaNeural", "Ava (US female, bright)"),
    ("en-US-AriaNeural", "Aria (US female)"),
    ("en-US-GuyNeural", "Guy (deep US male, slow)"),
    ("en-GB-RyanNeural", "Ryan (British male)"),
    ("ar-TN-HediNeural", "Hedi (Tunisian Arabic male)"),
    ("ar-TN-ReemNeural", "Reem (Tunisian Arabic female)"),
]

RATE_CHOICES = ["+0%", "+20%", "+30%", "+40%", "+50%"]
