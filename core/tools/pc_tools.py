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


def ocr_screenshot():
    try:
        import winocr
        import PIL.Image
        import io
        import asyncio
        img = pyautogui.screenshot()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        pil_img = PIL.Image.open(buf)

        async def _ocr():
            return await winocr.recognize_pil(pil_img, lang="en")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, _ocr()).result(timeout=15)
        else:
            result = asyncio.run(_ocr())

        text = result.text if result else ""
        if not text.strip():
            return "No text detected on screen."
        return text.strip()
    except Exception as e:
        return f"OCR failed: {e}"


def describe_screen():
    try:
        import winocr
        import PIL.Image
        import io
        import asyncio
        active = ""
        try:
            w = gw.getActiveWindow()
            if w:
                active = w.title or ""
        except Exception:
            pass
        img = pyautogui.screenshot()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        pil_img = PIL.Image.open(buf)

        async def _ocr():
            return await winocr.recognize_pil(pil_img, lang="en")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, _ocr()).result(timeout=15)
        else:
            result = asyncio.run(_ocr())

        text = result.text if result else ""
        parts = []
        if active:
            parts.append(f"Active window: {active}")
        if text.strip():
            parts.append(f"Visible text:\n{text.strip()[:2000]}")
        if not parts:
            return "No readable content on screen."
        return "\n\n".join(parts)
    except Exception as e:
        return f"Screen read failed: {e}"


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


def wifi_status():
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=10
        )
        out = result.stdout + result.stderr
        ssid = signal = ipv4 = state = ""
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("SSID") and not line.startswith("BSS"):
                ssid = line.split(":", 1)[-1].strip()
            elif "Signal" in line:
                signal = line.split(":", 1)[-1].strip()
            elif line.startswith("IPv4"):
                ipv4 = line.split(":", 1)[-1].strip()
            elif line.startswith("State"):
                state = line.split(":", 1)[-1].strip()
        if not ssid and not state:
            if "elevation" in out.lower() or "administrator" in out.lower():
                return "WiFi status requires admin privileges. Run JARVIS as admin."
            if "not found" in out.lower() or "no wireless" in out.lower():
                return "No WiFi adapter found on this system."
            return "WiFi is off or no adapter found."
        parts = [f"State: {state}"]
        if ssid:
            parts.append(f"SSID: {ssid}")
        if signal:
            parts.append(f"Signal: {signal}")
        if ipv4:
            parts.append(f"IP: {ipv4}")
        return "\n".join(parts)
    except Exception as e:
        return f"WiFi status failed: {e}"


def wifi_toggle(state="toggle"):
    state = str(state).lower().strip()
    if state in ("on", "enable", "1"):
        target = "enable"
    elif state in ("off", "disable", "0"):
        target = "disable"
    else:
        # Auto-detect current state
        try:
            out = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, text=True, timeout=10
            ).stdout
            target = "disable" if "connected" in out.lower() else "enable"
        except Exception:
            target = "enable"
    for iface in ["Wi-Fi", "Wi-Fi 2", "Wireless Network Connection", "WLAN"]:
        result = subprocess.run(
            ["netsh", "interface", "set", "interface", iface, f"admin={target}"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return f"WiFi {target}d via {iface}."
    return f"Could not toggle WiFi. May require admin privileges."


def wifi_list():
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            capture_output=True, text=True, timeout=15
        )
        out = result.stdout + result.stderr
        if "elevation" in out.lower() or "administrator" in out.lower():
            return "WiFi scan requires admin privileges."
        networks = []
        current = {}
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("SSID") and not line.startswith("BSS"):
                if current.get("name"):
                    networks.append(current)
                current = {"name": line.split(":", 1)[-1].strip()}
            elif "Authentication" in line:
                current["auth"] = line.split(":", 1)[-1].strip()
            elif "Signal" in line:
                current["signal"] = line.split(":", 1)[-1].strip()
        if current.get("name"):
            networks.append(current)
        if not networks:
            return "No WiFi networks found."
        lines = []
        for n in networks[:10]:
            sig = n.get("signal", "?")
            auth = n.get("auth", "")
            lines.append(f"{n['name']} ({sig} signal, {auth})")
        return "\n".join(lines)
    except Exception as e:
        return f"WiFi list failed: {e}"


def speed_test():
    try:
        import speedtest as st
        spd = st.Speedtest()
        spd.get_best_server()
        download = spd.download() / 1e6
        upload = spd.upload() / 1e6
        ping = spd.results.ping
        return f"Download: {download:.1f} Mbps\nUpload: {upload:.1f} Mbps\nPing: {ping:.0f} ms"
    except ImportError:
        return "Speed test requires 'speedtest-cli'. Run: pip install speedtest-cli"
    except Exception as e:
        return f"Speed test failed: {e}"


def move_file(source, destination):
    import shutil
    source = os.path.expanduser(str(source).strip())
    dest = os.path.expanduser(str(destination).strip())
    if not os.path.exists(source):
        return f"Source not found: {source}"
    try:
        if os.path.isdir(dest):
            dest = os.path.join(dest, os.path.basename(source))
        shutil.move(source, dest)
        return f"Moved to {dest}"
    except Exception as e:
        return f"Move failed: {e}"


def copy_file(source, destination):
    import shutil
    source = os.path.expanduser(str(source).strip())
    dest = os.path.expanduser(str(destination).strip())
    if not os.path.exists(source):
        return f"Source not found: {source}"
    try:
        if os.path.isdir(dest):
            dest = os.path.join(dest, os.path.basename(source))
        shutil.copy2(source, dest)
        return f"Copied to {dest}"
    except Exception as e:
        return f"Copy failed: {e}"


def delete_file(path):
    path = os.path.expanduser(str(path).strip())
    if not os.path.exists(path):
        return f"File not found: {path}"
    try:
        if os.path.isdir(path):
            import shutil
            shutil.rmtree(path)
            return f"Deleted folder: {path}"
        os.remove(path)
        return f"Deleted: {path}"
    except Exception as e:
        return f"Delete failed: {e}"


def open_folder(path):
    path = os.path.expanduser(str(path).strip())
    if not os.path.exists(path):
        return f"Path not found: {path}"
    try:
        if os.path.isfile(path):
            path = os.path.dirname(path)
        os.startfile(path)
        return f"Opened {path}"
    except Exception as e:
        return f"Could not open folder: {e}"


def set_brightness(level):
    level = max(0, min(100, int(level)))
    try:
        import wmi
        w = wmi.WMI(namespace="wmi")
        w.WmiMonitorBrightnessMethods()[0].WmiSetBrightness(level, 0)
        return f"Brightness set to {level}%."
    except ImportError:
        # Fallback: use PowerShell
        try:
            subprocess.run(
                ["powershell", "-Command",
                 f"(Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})"],
                capture_output=True, timeout=10
            )
            return f"Brightness set to {level}% (PowerShell fallback)."
        except Exception:
            return "Brightness control requires the 'wmi' package. Run: pip install wmi"
    except Exception as e:
        return f"Brightness control failed: {e}"


def get_brightness():
    try:
        import wmi
        w = wmi.WMI(namespace="wmi")
        brightness = w.WmiMonitorBrightness()[0].CurrentBrightness
        return f"Brightness: {brightness}%"
    except ImportError:
        try:
            out = subprocess.check_output(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightness).CurrentBrightness"],
                text=True, timeout=10
            ).strip()
            return f"Brightness: {out}%"
        except Exception:
            return "Brightness read requires 'wmi' package. Run: pip install wmi"
    except Exception as e:
        return f"Brightness read failed: {e}"


def shutdown_pc(action="shutdown", timer=0):
    action = str(action).lower().strip()
    timer = max(0, int(timer))
    commands = {
        "shutdown": f"shutdown /s /t {timer}",
        "restart": f"shutdown /r /t {timer}",
        "cancel": "shutdown /a",
        "hibernate": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
        "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
    }
    cmd = commands.get(action)
    if not cmd:
        return f"Unknown action '{action}'. Use: shutdown, restart, cancel, hibernate, sleep."
    try:
        subprocess.Popen(cmd, shell=True)
        if timer > 0:
            return f"PC will {action} in {timer} seconds."
        return f"PC {action} initiated."
    except Exception as e:
        return f"Failed to {action}: {e}"


def set_wallpaper(path):
    path = os.path.expanduser(str(path).strip())
    if not os.path.exists(path):
        return f"Image not found: {path}"
    try:
        import ctypes
        ctypes.windll.user32.SystemParametersInfoW(20, 0, path, 3)
        return f"Wallpaper set to {os.path.basename(path)}"
    except Exception as e:
        return f"Failed to set wallpaper: {e}"


def maximize_window(title):
    windows = gw.getWindowsWithTitle(str(title))
    if not windows:
        return f"No open window matching '{title}'."
    win = windows[0]
    try:
        if win.isMinimized:
            win.restore()
        win.maximize()
        return f"Maximized: {win.title}"
    except Exception as e:
        return f"Could not maximize: {e}"


def snap_window(title, direction="left"):
    windows = gw.getWindowsWithTitle(str(title))
    if not windows:
        return f"No open window matching '{title}'."
    win = windows[0]
    direction = str(direction).lower().strip()
    try:
        if win.isMinimized:
            win.restore()
        screen_w = win.screen.width
        screen_h = win.screen.height
        if direction in ("left", "l"):
            win.moveTo(0, 0)
            win.resizeTo(screen_w // 2, screen_h)
        elif direction in ("right", "r"):
            win.moveTo(screen_w // 2, 0)
            win.resizeTo(screen_w // 2, screen_h)
        elif direction in ("top", "up", "t", "u"):
            win.moveTo(0, 0)
            win.resizeTo(screen_w, screen_h // 2)
        elif direction in ("bottom", "down", "b", "d"):
            win.moveTo(0, screen_h // 2)
            win.resizeTo(screen_w, screen_h // 2)
        else:
            return f"Unknown direction '{direction}'. Use: left, right, top, bottom."
        return f"Snapped {win.title} to {direction}"
    except Exception as e:
        return f"Could not snap window: {e}"


def screenshot_window(title):
    windows = gw.getWindowsWithTitle(str(title))
    if not windows:
        return f"No open window matching '{title}'."
    win = windows[0]
    try:
        if win.isMinimized:
            win.restore()
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOT_DIR, datetime.now().strftime("window_%Y%m%d_%H%M%S.png"))
        # Use region screenshot
        region = (win.left, win.top, win.width, win.height)
        img = pyautogui.screenshot(region=region)
        img.save(path)
        return f"Window screenshot saved: {path}"
    except Exception as e:
        return f"Could not screenshot window: {e}"
