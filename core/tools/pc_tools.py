import ctypes
import fnmatch
import os
import subprocess
import time
import webbrowser
from datetime import datetime

import psutil
import pyautogui
import pyperclip
import pygetwindow as gw
from pycaw.pycaw import AudioUtilities

SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots")

START_MENU_DIRS = [
    os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Microsoft", "Windows", "Start Menu", "Programs"),
    os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
]

USER_DIRS = {
    "desktop": ["Desktop"],
    "documents": ["Documents"],
    "downloads": ["Downloads"],
    "pictures": ["Pictures"],
    "videos": ["Videos"],
    "music": ["Music"],
}

APP_ALIASES = {
    "notepad": "notepad",
    "calculator": "calc",
    "paint": "mspaint",
    "settings": "ms-settings:",
    "task manager": "taskmgr",
}

_app_index = None


def _index_start_menu():
    global _app_index
    if _app_index is not None:
        return _app_index
    index = []
    for root_dir in START_MENU_DIRS:
        for dirpath, dirnames, filenames in os.walk(root_dir):
            for f in filenames:
                if f.lower().endswith((".lnk", ".url")):
                    index.append((os.path.splitext(f)[0], os.path.join(dirpath, f)))
    _app_index = index
    return index


def _volume_interface():
    return AudioUtilities.GetSpeakers().EndpointVolume


def launch_app(query):
    query = str(query).strip().lower()
    alias = APP_ALIASES.get(query)
    if alias:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", alias], shell=False)
            return f"Opened {query}."
        except Exception:
            pass
    index = _index_start_menu()
    if not index:
        return "No applications found in the Start Menu."
    query_tokens = query.split()
    scored = []
    for name, path in index:
        name_l = name.lower()
        matches = sum(1 for t in query_tokens if t in name_l)
        if matches == len(query_tokens):
            scored.append((matches, -len(name_l), name, path))
        elif matches > 0:
            scored.append((matches * 10 - len(name_l) / 100.0, 0, name, path))
    if not scored:
        return f"No installed app found matching '{query}'."
    scored.sort(reverse=True)
    best_name, best_path = scored[0][2], scored[0][3]
    try:
        os.startfile(best_path)
        return f"Opened {best_name}."
    except Exception as e:
        return f"Could not open {best_name}: {e}"


def close_app(name):
    exe = str(name).strip().lower()
    if not exe.endswith(".exe"):
        exe += ".exe"
    result = subprocess.run(["taskkill", "/IM", exe, "/F"], capture_output=True, text=True)
    if result.returncode == 0:
        return f"Closed {name}."
    return f"No running process named {exe} was found."


def set_volume(level):
    level = max(0, min(100, int(level)))
    volume = _volume_interface()
    volume.SetMasterVolumeLevelScalar(level / 100.0, None)
    return f"Volume set to {level}%."


def get_volume():
    volume = _volume_interface()
    return round(volume.GetMasterVolumeLevelScalar() * 100)


def media_key(action="play_pause"):
    keys = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2, "mute": 0xAD}
    action = str(action).lower()
    if action not in keys:
        return f"Unknown media action '{action}'."
    keybd = ctypes.windll.user32.keybd_event
    keybd(keys[action], 0, 0, 0)
    keybd(keys[action], 0, 2, 0)
    return f"Sent media key: {action}."


def take_screenshot():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, datetime.now().strftime("screenshot_%Y%m%d_%H%M%S.png"))
    pyautogui.screenshot(path)
    return f"Screenshot saved to {path}"


def system_info():
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    lines = [
        f"CPU usage: {cpu}%",
        f"RAM usage: {mem.percent}% ({round(mem.used / 1e9, 1)} GB of {round(mem.total / 1e9, 1)} GB)",
    ]
    battery = psutil.sensors_battery()
    if battery:
        status = "charging" if battery.power_plugged else "on battery"
        lines.append(f"Battery: {round(battery.percent)}% ({status})")
    disk = psutil.disk_usage(os.path.expanduser("~\\"))
    lines.append(f"System disk: {disk.percent}% used")
    return "\n".join(lines)


def top_processes(metric="memory", limit=5):
    metric = str(metric).lower()
    limit = max(1, min(15, int(limit)))
    if metric not in ("memory", "ram", "cpu"):
        metric = "memory"
    skip_names = {"system idle process", "idle process", "system", "registry", "memcompression"}

    procs = []
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            name = proc.info["name"]
            if name and name.lower() not in skip_names:
                procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    def base_name(raw):
        raw = raw.lower()
        return raw[:-4] if raw.endswith(".exe") else raw

    if metric == "cpu":
        for proc in procs:
            try:
                proc.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(0.6)
        scores = {}
        for proc in procs:
            try:
                scores[base_name(proc.info["name"])] = (
                    scores.get(base_name(proc.info["name"]), 0.0) + proc.cpu_percent(interval=None)
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return "\n".join(f"{name}: {round(value, 1)}% CPU" for name, value in ranked)

    scores = {}
    for proc in procs:
        try:
            mem = proc.info["memory_info"].rss if proc.info["memory_info"] else 0
            scores[base_name(proc.info["name"])] = scores.get(base_name(proc.info["name"]), 0) + mem
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return "\n".join(f"{name}: {round(value / (1024 * 1024))} MB" for name, value in ranked)


def list_running_apps():
    skip_prefixes = ("svchost", "runtime", "csrss", "winlogon", "services", "lsass", "smss", "system", "registry", "dwm", "conhost", "python")
    apps = set()
    for proc in psutil.process_iter(["name"]):
        try:
            name = proc.info["name"]
            if name and name.lower().endswith(".exe") and not name.lower().startswith(skip_prefixes):
                apps.add(name[:-4])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return ", ".join(sorted(apps)[:60])


def search_files(pattern, location="all", limit=10):
    pattern = str(pattern).lower().replace("*", "")
    location = str(location).lower()
    limit = max(1, min(25, int(limit)))
    home = os.path.expanduser("~")
    if location in USER_DIRS:
        roots = [os.path.join(home, sub) for sub in USER_DIRS[location]]
    elif os.path.isabs(str(location)) and os.path.exists(str(location)):
        roots = [str(location)]
    else:
        roots = [os.path.join(home, sub) for subs in USER_DIRS.values() for sub in subs]
    skip_dirs = {"node_modules", ".git", "AppData", "__pycache__", "venv"}
    matches = []
    scanned = 0
    deadline = time.time() + 20
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
            for f in filenames:
                scanned += 1
                if scanned > 30000 or time.time() > deadline:
                    break
                if pattern and pattern in f.lower():
                    matches.append(os.path.join(dirpath, f))
                    if len(matches) >= limit:
                        break
            if len(matches) >= limit or scanned > 30000 or time.time() > deadline:
                break
        if len(matches) >= limit or scanned > 30000 or time.time() > deadline:
            break
    if not matches:
        return f"No files matching '{pattern}' found."
    return "\n".join(matches)


def get_clipboard():
    try:
        text = pyperclip.paste()
        if not text:
            return "Clipboard is empty."
        return text[:2000]
    except Exception as e:
        return f"Could not read clipboard: {e}"


def set_clipboard(text):
    pyperclip.copy(str(text))
    return "Copied to clipboard."


def minimize_all_windows():
    keybd = ctypes.windll.user32.keybd_event
    keybd(0x5B, 0, 0, 0)
    keybd(0x4D, 0, 0, 0)
    keybd(0x4D, 0, 2, 0)
    keybd(0x5B, 0, 2, 0)
    return "Minimized all windows."


def focus_window(title):
    windows = gw.getWindowsWithTitle(str(title))
    if not windows:
        return f"No open window matching '{title}'."
    win = windows[0]
    try:
        if win.isMinimized:
            win.restore()
        win.activate()
        return f"Focused window: {win.title}"
    except Exception as e:
        return f"Could not focus window: {e}"


def type_text(text):
    pyperclip.copy(str(text))
    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "v")
    return "Typed the text into the active window."


def open_url(url):
    target = str(url).strip()
    if not target.startswith(("http://", "https://")):
        target = "https://" + target
    webbrowser.open(target)
    return f"Opened {target}"


def lock_screen():
    subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
    return "Locked the screen."


def sleep_pc():
    subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState 0,1,0"])
    return "Putting the PC to sleep."
