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

ASSISTANT_NAME = "JARVIS"

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


VOICE_CHOICES = [
    ("en-US-AndrewNeural", "Andrew (lively US male)"),
    ("en-US-BrianNeural", "Brian (casual US male)"),
    ("en-US-AvaNeural", "Ava (US female, bright)"),
    ("en-US-AriaNeural", "Aria (US female)"),
    ("en-US-GuyNeural", "Guy (deep US male, slow)"),
    ("en-GB-RyanNeural", "Ryan (British male)"),
]

RATE_CHOICES = ["+0%", "+20%", "+30%", "+40%", "+50%"]
