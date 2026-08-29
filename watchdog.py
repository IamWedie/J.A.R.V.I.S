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
CRASH_DUMPS_DIR = os.path.join(LOG_DIR, "dumps")

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


def collect_crash_dump():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(CRASH_DUMPS_DIR, exist_ok=True)
    dump_path = os.path.join(CRASH_DUMPS_DIR, f"crash_{ts}.txt")
    lines = [f"=== JARVIS Crash Dump {ts} ===\n"]
    pid = find_jarvis_pid()
    if pid:
        try:
            out = subprocess.check_output(
                ["wmic", "process", "where", f"processid={pid}",
                 "get", "Name,CommandLine,WorkingSetSize,KernelModeTime,UserModeTime"],
                text=True, timeout=10
            )
            lines.append(f"Process info:\n{out}\n")
        except Exception:
            lines.append("Could not get process info\n")
    jarvis_log = os.path.join(LOG_DIR, "jarvis.log")
    if os.path.exists(jarvis_log):
        try:
            with open(jarvis_log, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                lines.append(f"\nLast 50 log entries:\n")
                lines.extend(all_lines[-50:])
        except Exception:
            lines.append("Could not read jarvis.log\n")
    try:
        import psutil
        proc = psutil.Process(pid) if pid else psutil.Process()
        mem = proc.memory_info()
        lines.append(f"\nMemory: RSS={mem.rss // 1024}KB, VMS={mem.vms // 1024}KB\n")
        lines.append(f"CPU: {proc.cpu_percent(interval=1)}%\n")
    except Exception:
        pass
    with open(dump_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    log(f"Crash dump saved: {dump_path}")
    return dump_path


def main():
    log(f"Watchdog started (frozen={FROZEN})")
    send_telegram("JARVIS watchdog started")

    while True:
        time.sleep(CHECK_INTERVAL)

        if is_server_alive():
            continue

        log("Server NOT responding!")
        collect_crash_dump()

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
