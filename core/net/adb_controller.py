import os
import re
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


def _shell_raw(cmd, timeout=15):
    return _run(["shell", cmd], timeout=timeout)


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
        if "AC powered:" in line and "true" in line.lower():
            charging = "charging"
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
    time.sleep(0.3)
    _shell("input keyevent KEYCODE_MENU")
    return "Screen on."


def unlock():
    _shell("input keyevent KEYCODE_WAKEUP")
    time.sleep(0.5)
    _shell("input swipe 540 2000 540 500 300")
    time.sleep(0.5)
    lock = _shell_raw("dumpsys window | grep mDreamingLockscreen")
    if "true" in lock.lower():
        return "Screen woke but lock screen still showing (PIN/pattern required). I cannot enter security codes."
    return "Screen unlocked."


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


def _resolve_launch_intent(package):
    out = _shell_raw(f"cmd package resolve-activity --brief {package}")
    for line in out.splitlines():
        if "/" in line and not line.startswith("priority"):
            return line.strip()
    out2 = _shell_raw(f"pm dump {package} | grep -A1 'android.intent.category.LAUNCHER'")
    for line in out2.splitlines():
        if package in line and "/" in line:
            comp = line.strip().split()[-1]
            return comp
    return None


def open_app(package):
    if package in ("camera", "cam"):
        return open_camera()
    comp = _resolve_launch_intent(package)
    if comp:
        _shell_raw(f"am start -n {comp}")
        return f"Opened {package}."
    _shell_raw(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    return f"Opened {package} (via monkey fallback)."


def open_camera():
    comp = _resolve_launch_intent("com.hihonor.camera")
    if comp:
        _shell_raw(f"am start -n {comp}")
        return "Camera opened."
    r = _shell_raw("am start -n com.hihonor.camera/.Camera")
    if "Error" in r:
        r = _shell_raw("am start -a android.media.action.STILL_IMAGE_CAMERA")
    return "Camera opened." if "Error" not in r else f"Camera failed: {r}"


def close_app(package):
    _shell_raw(f"am force-stop {package}")
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
    _shell_raw(f"input text '{escaped}'")
    return f"Typed: {text}"


def open_url(url):
    _shell_raw(f'am start -a android.intent.action.VIEW -d "{url}"')
    return f"Opened {url} on phone."


def current_activity():
    out = _shell_raw("dumpsys activity activities | grep -E 'ResumedActivity:|topResumedActivity'")
    if not out:
        out = _shell_raw("dumpsys activity activities | grep mResumedActivity")
    return out if out else "Could not determine current activity."
