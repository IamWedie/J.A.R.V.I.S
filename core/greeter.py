import ctypes
import random
from datetime import datetime

import core.config as config
from core.logging_setup import get_logger

log = get_logger("greeter")


def get_idle_seconds():
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
    return max(0, int(millis / 1000))


def _time_greeting():
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 18:
        return "Good afternoon"
    return "Good evening"


START_PHRASES = [
    "{tg}, sir. All systems are online.",
    "{tg}, sir. JARVIS is at your service.",
    "Welcome back, sir. Everything is running smoothly.",
    "{tg} sir. I have been waiting for you.",
]

WELCOME_BACK_PHRASES = [
    "Welcome back, sir.",
    "Welcome back, sir. You were away for {away_min} minutes.",
    "Good to see you again, sir.",
    "There you are, sir. I kept everything warm for you.",
]


def start_phrase():
    return random.choice(START_PHRASES).format(tg=_time_greeting())


def welcome_back_phrase(away_minutes):
    phrase = random.choice(WELCOME_BACK_PHRASES)
    return phrase.format(away_min=away_minutes)
