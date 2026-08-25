import os
import subprocess
import sys
import tempfile
import time

ADB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "platform-tools", "adb.exe")


def _run(args, timeout=15):
    try:
        r = subprocess.run([ADB] + args, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        return f"error: {e}"


def _shell(cmd, timeout=15):
    return _run(["shell"] + cmd.split(), timeout=timeout)


def connected():
    out = _run(["devices"])
    lines = [l for l in out.splitlines()[1:] if l.strip() and "device" in l]
    return len(lines) > 0


def device_info():
    if not connected():
        return "Phone not connected."
    model = _shell("getprop ro.product.model")
    android = _shell("getprop ro.build.version.release")
    brand = _shell("getprop ro.product.brand")
    res = _shell("wm size")
    bat = _shell("dumpsys battery")
    level = ""
    charging = ""
    for line in bat.splitlines():
        if "level:" in line:
            level = line.split(":")[-1].strip()
        if "AC powered:" in line:
            charging = "charging" if "true" in line.lower() else ""
        if "USB powered:" in line and "true" in line.lower():
            charging = "USB charging"
        if "Wireless powered:" in line and "true" in line.lower():
            charging = "wireless charging"
    return (f"{brand} {model} | Android {android} | {res} | "
            f"Battery: {level}%{' (' + charging + ')' if charging else ''}")


def battery():
    bat = _shell("dumpsys battery")
    info = {}
    for line in bat.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    level = info.get("level", "?")
    status = info.get("status", "?")
    temp = info.get("temperature", "?")
    status_map = {"2": "charging", "3": "discharging", "4": "not charging", "5": "full"}
    return f"Battery: {level}% ({status_map.get(status, status)}), temp: {int(temp)//10 if temp.isdigit() else temp}C"


def screenshot():
    remote = "/sdcard/jarvis_screenshot.png"
    local = os.path.join(tempfile.gettempdir(), "jarvis_screenshot.png")
    _shell(f"screencap -p {remote}")
    _run(["pull", remote, local])
    _shell(f"rm {remote}")
    return local if os.path.exists(local) else "Screenshot failed."


def home():
    _shell("input keyevent KEYCODE_HOME")
    return "Home pressed."


def back():
    _shell("input keyevent KEYCODE_BACK")
    return "Back pressed."


def recent():
    _shell("input keyevent KEYCODE_APP_SWITCH")
    return "Recent apps."


def screen_on():
    _shell("input keyevent KEYCODE_WAKEUP")
    return "Screen on."


def screen_off():
    _shell("input keyevent KEYCODE_SLEEP")
    return "Screen off."


def volume_up():
    _shell("input keyevent KEYCODE_VOLUME_UP")
    return "Volume up."


def volume_down():
    _shell("input keyevent KEYCODE_VOLUME_DOWN")
    return "Volume down."


def mute():
    _shell("input keyevent KEYCODE_VOLUME_MUTE")
    return "Muted."


def open_app(package):
    _shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    return f"Opened {package}."


def close_app(package):
    _shell(f"am force-stop {package}")
    return f"Closed {package}."


def list_apps():
    out = _shell("pm list packages -3")
    pkgs = [l.replace("package:", "") for l in out.splitlines() if l.startswith("package:")]
    if not pkgs:
        return "No third-party apps found."
    return "Installed apps:\n" + "\n".join(f"  - {p}" for p in sorted(pkgs))


def tap(x, y):
    _shell(f"input tap {x} {y}")
    return f"Tapped ({x}, {y})."


def swipe(x1, y1, x2, y2, duration_ms=300):
    _shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")
    return f"Swiped ({x1},{y1}) to ({x2},{y2})."


def type_text(text):
    escaped = text.replace(" ", "%s").replace("&", "\\&").replace("'", "\\'")
    _shell(f"input text '{escaped}'")
    return f"Typed: {text}"


def open_url(url):
    _shell(f'am start -a android.intent.action.VIEW -d "{url}"')
    return f"Opened {url} on phone."


def current_activity():
    out = _shell("dumpsys activity activities | grep mResumedActivity")
    return out if out else "Could not determine current activity."
