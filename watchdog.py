"""JARVIS Crash Watchdog — monitors server and auto-restarts on failure."""
import os
import sys
import time
import subprocess
import urllib.request
import json
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
CRASH_LOG = os.path.join(LOG_DIR, "crash.log")

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    APP_EXE = sys.executable
    LAUNCH_CMD = [APP_EXE, "--hidden"]
else:
    PYTHON = os.path.join(PROJECT_DIR, "venv", "Scripts", "python.exe")
    APP_PY = os.path.join(PROJECT_DIR, "app.py")
    LAUNCH_CMD = [PYTHON, APP_PY]

HOST = "127.0.0.1"
PORT = 8741
HEALTH_URL = f"http://{HOST}:{PORT}/"

CHECK_INTERVAL = 30
MAX_CRASHES = 5
CRASH_WINDOW = 600

_crash_times = []


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(CRASH_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def send_telegram(msg):
    try:
        sys.path.insert(0, PROJECT_DIR)
        import core.config as config
        token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
        chat_id = getattr(config, "TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": msg}).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def is_server_alive():
    try:
        req = urllib.request.Request(HEALTH_URL, method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def find_jarvis_pid():
    try:
        if FROZEN:
            exe_name = os.path.basename(sys.executable)
            out = subprocess.check_output(
                ["wmic", "process", "where",
                 f"name='{exe_name}'",
                 "get", "processid"],
                text=True, timeout=10
            )
        else:
            out = subprocess.check_output(
                ["wmic", "process", "where",
                 f"commandline like '%app.py%' and name='python.exe'",
                 "get", "processid"],
                text=True, timeout=10
            )
        for line in out.strip().splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                if pid != os.getpid():
                    return pid
    except Exception:
        pass
    return None


def kill_jarvis():
    pid = find_jarvis_pid()
    if pid:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           timeout=10, capture_output=True)
            log(f"Killed JARVIS PID {pid}")
            time.sleep(2)
        except Exception as e:
            log(f"Kill failed: {e}")


def start_jarvis():
    log("Starting JARVIS...")
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.Popen(
            LAUNCH_CMD,
            cwd=PROJECT_DIR if not FROZEN else os.path.dirname(sys.executable),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        log(f"Started JARVIS PID {p.pid}")
        return p
    except Exception as e:
        log(f"Start failed: {e}")
        return None


def wait_for_server(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_server_alive():
            return True
        time.sleep(1)
    return False


def crash_restarts_too_many():
    now = time.time()
    _crash_times[:] = [t for t in _crash_times if now - t < CRASH_WINDOW]
    return len(_crash_times) >= MAX_CRASHES


def main():
    log(f"Watchdog started (frozen={FROZEN})")
    send_telegram("JARVIS watchdog started")

    while True:
        time.sleep(CHECK_INTERVAL)

        if is_server_alive():
            continue

        log("Server NOT responding!")

        if crash_restarts_too_many():
            msg = f"JARVIS crashed {MAX_CRASHES} times in {CRASH_WINDOW}s — watchdog stopping"
            log(msg)
            send_telegram(msg)
            break

        _crash_times.append(time.time())
        kill_jarvis()
        start_jarvis()

        if wait_for_server():
            msg = f"JARVIS restarted successfully (crash #{len(_crash_times)})"
            log(msg)
            send_telegram(msg)
        else:
            msg = f"JARVIS restart FAILED (crash #{len(_crash_times)})"
            log(msg)
            send_telegram(msg)

    log("Watchdog stopped")


if __name__ == "__main__":
    main()
