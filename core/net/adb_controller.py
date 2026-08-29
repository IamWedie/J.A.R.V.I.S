import os
import re
import subprocess
import sys
import tempfile
import time

ADB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "platform-tools", "adb.exe")
_camera_facing = "back"


def _run(args, timeout=15):
    try:
        r = subprocess.run([ADB] + args, capture_output=True, timeout=timeout)
        return r.stdout.decode("utf-8", errors="replace").strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        return f"error: {e}"


def _shell(cmd, timeout=15):
    return _run(["shell"] + cmd.split(), timeout=timeout)


def _shell_raw(cmd, timeout=15):
    return _run(["shell", cmd], timeout=timeout)


def _is_connected():
    out = _run(["devices"])
    lines = [l for l in out.splitlines()[1:] if l.strip() and "device" in l]
    return len(lines) > 0


# ──────────────────── CONNECTION ────────────────────

def _connect(host, port=5555, timeout=8):
    r = subprocess.run(
        [ADB, "connect", f"{host}:{port}"],
        capture_output=True, timeout=timeout,
    )
    out = r.stdout.decode("utf-8", errors="replace").strip() or r.stderr.decode("utf-8", errors="replace").strip()
    return "connected" in out.lower() or "already connected" in out.lower()


def connect_phone():
    """Ensure ADB is connected to the phone.

    We connect ONLY to the configured VPN address (PHONE_ADDR) — we never scan
    the LAN or auto-discover, so wireless debugging is never exposed to
    strangers on public/untrusted networks. The phone joins our private overlay
    VPN and is reached through that encrypted tunnel. ADB still requires the
    phone to accept our pairing cert, and a PHONE_SERIAL whitelist adds a second
    identity layer. Returns True if a verified connection is active.
    """
    from core import config

    addr = (config.PHONE_ADDR or "").strip()
    if not addr:
        return False
    if _is_connected():
        return True
    if _connect(addr, config.PHONE_PORT):
        if _verify_serial():
            log_connected(addr, config.PHONE_PORT)
            return True
        _disconnect(addr, config.PHONE_PORT)
    return False


def _verify_serial():
    """Confirm the connected device matches the enrolled PHONE_SERIAL, if set."""
    from core import config
    expected = (config.PHONE_SERIAL or "").strip()
    if not expected:
        return True
    serial = _shell("getprop ro.serialno").strip()
    return bool(serial) and serial == expected


def _disconnect(host, port):
    try:
        subprocess.run([ADB, "disconnect", f"{host}:{port}"],
                       capture_output=True, timeout=8)
    except Exception:
        pass


def connected():
    return "Phone is connected." if connect_phone() else "Phone NOT connected — set PHONE_ADDR (VPN address) and pair this PC with the phone."


def log_connected(host, port):
    try:
        from core.logging_setup import get_logger
        get_logger("adb").info("auto-connected to phone at %s:%s", host, port)
    except Exception:
        pass


def device_info():
    if not connect_phone():
        return "Phone not connected."
    model = _shell("getprop ro.product.model")
    android = _shell("getprop ro.build.version.release")
    brand = _shell("getprop ro.product.brand")
    res = _shell("wm size")
    density = _shell("wm density")
    bat = _shell_raw("dumpsys battery")
    level, charging, temp = "", "", ""
    for line in bat.splitlines():
        if "level:" in line:
            level = line.split(":")[-1].strip()
        if "temperature:" in line:
            temp = line.split(":")[-1].strip()
        if "AC powered:" in line and "true" in line.lower():
            charging = "AC"
        if "USB powered:" in line and "true" in line.lower():
            charging = "USB"
        if "Wireless powered:" in line and "true" in line.lower():
            charging = "wireless"
    temp_c = f"{int(temp)//10}C" if temp.isdigit() else temp
    return (f"{brand} {model} | Android {android} | {res} {density} | "
            f"Battery: {level}%{' (' + charging + ')' if charging else ''} {temp_c}")


# ──────────────────── POWER ────────────────────

def battery():
    bat = _shell_raw("dumpsys battery")
    info = {}
    for line in bat.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    level = info.get("level", "?")
    status = info.get("status", "?")
    temp = info.get("temperature", "?")
    voltage = info.get("voltage", "?")
    health = info.get("health", "?")
    status_map = {"1": "unknown", "2": "charging", "3": "discharging", "4": "not charging", "5": "full"}
    health_map = {"1": "unknown", "2": "good", "3": "overheat", "4": "dead", "5": "over voltage", "6": "unspecified failure"}
    return (f"Battery: {level}% | {status_map.get(status, status)} | "
            f"{int(temp)//10 if str(temp).isdigit() else temp}C | "
            f"{voltage}mV | {health_map.get(health, health)}")


def reboot():
    _shell("reboot")
    return "Rebooting phone..."


def reboot_recovery():
    _shell("reboot recovery")
    return "Rebooting to recovery mode..."


def reboot_bootloader():
    _shell("reboot bootloader")
    return "Rebooting to bootloader..."


def shutdown():
    _shell("reboot -p")
    return "Shutting down phone..."


def power_off():
    return shutdown()


def safe_mode():
    _shell("setprop persist.sys.safemode 1")
    _shell("reboot")
    return "Rebooting into safe mode..."


# ──────────────────── SCREEN ────────────────────

def screen_on():
    _shell("input keyevent KEYCODE_WAKEUP")
    time.sleep(0.3)
    _shell("input keyevent KEYCODE_MENU")
    return "Screen on."


def screen_off():
    _shell("input keyevent KEYCODE_SLEEP")
    return "Screen off."


def unlock():
    _shell("input keyevent KEYCODE_WAKEUP")
    time.sleep(0.5)
    _shell("input swipe 540 2000 540 500 300")
    time.sleep(0.5)
    lock = _shell_raw("dumpsys window | grep mDreamingLockscreen")
    if "true" in lock.lower():
        return "Screen woke but lock screen still showing (PIN/pattern required)."
    return "Screen unlocked."


def unlock_with_pin(pin):
    _shell("input keyevent KEYCODE_WAKEUP")
    time.sleep(0.5)
    _shell("input swipe 540 2000 540 500 300")
    time.sleep(1.0)
    _shell_raw(f"input text {pin}")
    time.sleep(0.3)
    _shell("input keyevent KEYCODE_ENTER")
    time.sleep(1.0)
    lock = _shell_raw("dumpsys window | grep mDreamingLockscreen")
    if "true" in lock.lower():
        return "PIN entered but lock screen still showing — PIN may be incorrect."
    return "Phone unlocked with PIN."


def screen_brightness(level):
    _shell_raw(f"settings put system screen_brightness {level}")
    return f"Brightness set to {level}."


def screen_rotation(auto):
    val = 1 if auto else 0
    _shell_raw(f"settings put system accelerometer_rotation {val}")
    return f"Auto-rotation {'on' if auto else 'off'}."


def screen_rotate(degrees):
    _shell_raw(f"settings put system user_rotation {degrees // 90}")
    return f"Screen rotated to {degrees} degrees."


def screenshot():
    remote = "/sdcard/jarvis_screenshot.png"
    local = os.path.join(tempfile.gettempdir(), "jarvis_screenshot.png")
    _shell(f"screencap -p {remote}")
    _run(["pull", remote, local])
    _shell(f"rm {remote}")
    return local if os.path.exists(local) else "Screenshot failed."


def screen_record(seconds=10):
    remote = "/sdcard/jarvis_recording.mp4"
    local = os.path.join(tempfile.gettempdir(), "jarvis_recording.mp4")
    _shell_raw(f"screenrecord --time-limit {min(seconds, 180)} {remote}")
    _run(["pull", remote, local])
    _shell(f"rm {remote}")
    return local if os.path.exists(local) else "Recording failed."


# ──────────────────── INPUT ────────────────────

def home():
    _shell("input keyevent KEYCODE_HOME")
    return "Home pressed."


def back():
    _shell("input keyevent KEYCODE_BACK")
    return "Back pressed."


def recent():
    _shell("input keyevent KEYCODE_APP_SWITCH")
    return "Recent apps."


def tap(x, y):
    _shell(f"input tap {x} {y}")
    return f"Tapped ({x}, {y})."


def long_press(x, y, duration_ms=1000):
    _shell(f"input swipe {x} {y} {x} {y} {duration_ms}")
    return f"Long pressed ({x}, {y})."


def swipe(x1, y1, x2, y2, duration_ms=300):
    _shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")
    return f"Swiped ({x1},{y1}) to ({x2},{y2})."


def swipe_up():
    _shell("input swipe 540 1800 540 500 300")
    return "Swiped up."


def swipe_down():
    _shell("input swipe 540 500 540 1800 300")
    return "Swiped down."


def swipe_left():
    _shell("input swipe 900 1200 100 1200 300")
    return "Swiped left."


def swipe_right():
    _shell("input swipe 100 1200 900 1200 300")
    return "Swiped right."


def type_text(text):
    escaped = text.replace(" ", "%s").replace("&", "\\&").replace("'", "\\'").replace('"', '\\"')
    _shell_raw(f"input text '{escaped}'")
    return f"Typed: {text}"


def type_text_slow(text):
    for ch in text:
        if ch == " ":
            _shell("input keyevent KEYCODE_SPACE")
        else:
            _shell_raw(f"input text '{ch}'")
        time.sleep(0.05)
    return f"Typed slowly: {text}"


def key_event(keycode):
    _shell(f"input keyevent {keycode}")
    return f"Key event {keycode} sent."


def volume_up():
    _shell("input keyevent KEYCODE_VOLUME_UP")
    return "Volume up."


def volume_down():
    _shell("input keyevent KEYCODE_VOLUME_DOWN")
    return "Volume down."


def volume_mute():
    _shell("input keyevent KEYCODE_VOLUME_MUTE")
    return "Muted."


def set_volume(level):
    _shell_raw(f"media volume --set {level} --stream 3")
    return f"Media volume set to {level}."


def get_volume():
    out = _shell_raw("media volume --get --stream 3")
    return out if out else "Could not get volume."


# ──────────────────── NAVIGATION ────────────────────

def open_app(package):
    if package in ("camera", "cam"):
        return open_camera()
    if package in ("settings", "setting"):
        return _shell_raw("am start -a android.settings.SETTINGS") or "Settings opened."
    if package in ("chrome", "browser"):
        return _shell_raw("am start -a android.intent.action.VIEW -d http://www.google.com") or "Browser opened."
    if package in ("phone", "dialer", "dial"):
        return _shell_raw("am start -a android.intent.action.DIAL") or "Dialer opened."
    if package in ("contacts", "contact"):
        return _shell_raw("am start -a android.intent.action.VIEW -d content://com.android.contacts/contacts") or "Contacts opened."
    if package in ("messages", "sms", "msg"):
        return _shell_raw("am start -a android.intent.action.MAIN -c android.intent.category.APP_MESSAGING") or "Messages opened."
    if package in ("gallery", "photos"):
        return _shell_raw("am start -a android.intent.action.VIEW -d content://media/external/images/media") or "Gallery opened."
    if package in ("youtube",):
        comp = _resolve_launch_intent("com.google.android.youtube")
        if comp:
            _shell_raw(f"am start -n {comp}")
            return "YouTube opened."
    comp = _resolve_launch_intent(package)
    if comp:
        _shell_raw(f"am start -n {comp}")
        return f"Opened {package}."
    _shell_raw(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    return f"Opened {package} (via monkey fallback)."


def close_app(package):
    _shell_raw(f"am force-stop {package}")
    return f"Closed {package}."


def clear_app_data(package):
    _shell_raw(f"pm clear {package}")
    return f"Cleared data for {package}."


def list_apps():
    out = _shell("pm list packages -3")
    pkgs = [l.replace("package:", "") for l in out.splitlines() if l.startswith("package:")]
    if not pkgs:
        return "No third-party apps found."
    return f"Third-party apps ({len(pkgs)}):\n" + "\n".join(f"  - {p}" for p in sorted(pkgs))


def list_all_apps():
    out = _shell("pm list packages")
    pkgs = [l.replace("package:", "") for l in out.splitlines() if l.startswith("package:")]
    return f"All apps ({len(pkgs)}):\n" + "\n".join(f"  - {p}" for p in sorted(pkgs))


def app_info(package):
    out = _shell_raw(f"dumpsys package {package} | head -30")
    return out if out else f"Could not get info for {package}."


def is_app_running(package):
    out = _shell_raw(f"pidof {package}")
    return f"{package} is running (PID: {out})." if out else f"{package} is NOT running."


def current_activity():
    out = _shell_raw("dumpsys activity activities | grep -E 'ResumedActivity:|topResumedActivity'")
    if not out:
        out = _shell_raw("dumpsys activity activities | grep mResumedActivity")
    return out if out else "Could not determine current activity."


def _resolve_launch_intent(package):
    out = _shell_raw(f"cmd package resolve-activity --brief {package}")
    for line in out.splitlines():
        if "/" in line and not line.startswith("priority"):
            return line.strip()
    return None


# ──────────────────── CAMERA ────────────────────

def open_camera():
    global _camera_facing
    _camera_facing = "back"
    comp = _resolve_launch_intent("com.hihonor.camera")
    if comp:
        _shell_raw(f"am start -n {comp}")
        return "Camera opened."
    r = _shell_raw("am start -n com.hihonor.camera/.Camera")
    if "Error" in r:
        r = _shell_raw("am start -a android.media.action.STILL_IMAGE_CAMERA")
    return "Camera opened." if "Error" not in r else f"Camera failed: {r}"


def take_photo():
    time.sleep(0.5)
    _shell("input tap 540 2200")
    time.sleep(1.0)
    return "Photo taken."


def take_selfie():
    time.sleep(0.5)
    if get_camera_facing() != "front":
        _shell("input tap 950 2100")
        time.sleep(1.0)
    _shell("input tap 540 2200")
    time.sleep(1.0)
    return "Selfie taken."


def toggle_flash():
    _shell_raw("am broadcast -a com.hihonor.camera.FLASH_TOGGLE")
    time.sleep(0.5)
    return "Flash toggled."


def flash_on():
    toggle_flash()
    return "Flash on."


def flash_off():
    toggle_flash()
    return "Flash off."


def selfie_verify():
    if not connect_phone():
        return None, "Phone not connected."
    if not is_screen_on():
        screen_on()
        time.sleep(1)
    if is_locked():
        import core.config as cfg
        pin = getattr(cfg, "PHONE_PIN", "") or "0910"
        unlock_with_pin(pin)
        time.sleep(2)
    if is_in_use():
        return None, "Phone in use, try again later."
    open_camera()
    time.sleep(2)
    if get_camera_facing() != "front":
        switch_camera()
        time.sleep(1)
    take_selfie()
    time.sleep(2)
    out = _shell_raw("ls -t /sdcard/DCIM/Camera/")
    photos = [f for f in out.splitlines() if f.strip().endswith((".jpg", ".jpeg", ".png"))]
    if not photos:
        return None, "No photo found after selfie."
    phone_path = f"/sdcard/DCIM/Camera/{photos[0]}"
    local = pull_file(phone_path)
    if os.path.exists(local) and os.path.getsize(local) > 1000:
        return local, "Selfie captured."
    return None, "Failed to pull selfie."


def switch_camera():
    global _camera_facing
    _shell("input tap 950 2100")
    time.sleep(1.0)
    _camera_facing = "front" if _camera_facing == "back" else "back"
    return f"Camera switched to {_camera_facing}."


def get_camera_facing():
    return _camera_facing


def is_locked():
    out = _shell_raw("dumpsys window | grep -E 'mDreamingLockscreen|mShowingLockscreen|isStatusBarKeyguard'")
    if "mDreamingLockscreen=true" in out or "mShowingLockscreen=true" in out:
        return True
    keyguard = _shell_raw("dumpsys window | grep 'isStatusBarKeyguard'")
    if "true" in keyguard.lower():
        return True
    return False


def is_screen_on():
    out = _shell_raw("dumpsys power | grep mWakefulness")
    return "Awake" in out


def is_in_use():
    if not is_screen_on():
        return False
    activity = _shell_raw("dumpsys activity activities | grep -E 'ResumedActivity:'")
    pkg = ""
    m = re.search(r"u0 (\S+?)/", activity)
    if m:
        pkg = m.group(1)
    if not pkg or "launcher" in pkg.lower():
        return False
    ime = _shell_raw("dumpsys input_method | grep mInputShown")
    if "true" in ime.lower():
        return True
    if pkg and "launcher" not in pkg.lower():
        return True
    return False


def record_video(seconds=10):
    time.sleep(0.5)
    _shell("input tap 540 2200")
    time.sleep(0.5)
    _shell("input keyevent KEYCODE_CAMERA")
    time.sleep(seconds)
    _shell("input keyevent KEYCODE_CAMERA")
    return f"Recorded {seconds}s video."


# ──────────────────── CLIPBOARD ────────────────────

def get_clipboard():
    out = _shell_raw("dumpsys clipboard")
    for line in out.splitlines():
        if "primary" in line.lower() or "text" in line.lower():
            return line.strip()
    return out if out else "Clipboard is empty."


def set_clipboard(text):
    _shell_raw(f'am broadcast -a clipper.set -e text "{text}"')
    _shell_raw(f"input text '{text.replace(' ', '%s')}'")
    return f"Clipboard set: {text}"


def copy_from_pc(text):
    _shell_raw(f'am broadcast -a android.intent.action.CLIPBOARD_CHANGED -e text "{text}"')
    return f"Sent to phone clipboard: {text[:50]}..."


def share_text(text):
    _shell_raw(f'am start -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT "{text}"')
    return "Share dialog opened."


def share_file(path):
    _shell_raw(f'am start -a android.intent.action.SEND -t application/octet-stream --eu android.intent.extra.STREAM "file://{path}"')
    return f"Sharing {path}."


# ──────────────────── FILES ────────────────────

def list_files(path="/sdcard/"):
    out = _shell_raw(f"ls -la {path}")
    return out if out else f"Could not list {path}"


def list_downloads():
    return list_files("/sdcard/Download/")


def list_pictures():
    return list_files("/sdcard/DCIM/Camera/")


def list_documents():
    return list_files("/sdcard/Documents/")


def list_music():
    return list_files("/sdcard/Music/")


def list_videos():
    return list_files("/sdcard/DCIM/Camera/") + "\n" + list_files("/sdcard/Movies/")


def find_files(name):
    out = _shell_raw(f"find /sdcard -name '*{name}*' -type f 2>/dev/null | head -20")
    return out if out else f"No files matching '{name}' found."


def delete_file(path):
    _shell_raw(f"rm -f {path}")
    return f"Deleted {path}."


def delete_folder(path):
    _shell_raw(f"rm -rf {path}")
    return f"Deleted folder {path}."


def file_info(path):
    out = _shell_raw(f"ls -la {path}")
    return out if out else f"File not found: {path}"


def storage_info():
    out = _shell("df -h /sdcard")
    return out if out else "Could not get storage info."


def pull_file(phone_path, local_dir=None):
    if not local_dir:
        local_dir = tempfile.gettempdir()
    local = os.path.join(local_dir, os.path.basename(phone_path))
    _run(["pull", phone_path, local])
    return local if os.path.exists(local) else f"Failed to pull {phone_path}."


def push_file(local_path, phone_path="/sdcard/Download/"):
    _run(["push", local_path, phone_path])
    return f"Pushed to {phone_path}."


def create_folder(path):
    _shell_raw(f"mkdir -p {path}")
    return f"Created {path}."


def move_file(src, dst):
    _shell_raw(f"mv {src} {dst}")
    return f"Moved {src} to {dst}."


def copy_file(src, dst):
    _shell_raw(f"cp {src} {dst}")
    return f"Copied {src} to {dst}."


def search_files(query):
    out = _shell_raw(f"find /sdcard -iname '*{query}*' 2>/dev/null | head -20")
    return out if out else f"No files matching '{query}'."


# ──────────────────── SETTINGS ────────────────────

def wifi_on():
    _shell_raw("svc wifi enable")
    return "WiFi turned on."


def wifi_off():
    _shell_raw("svc wifi disable")
    return "WiFi turned off."


def wifi_status():
    out = _shell_raw("dumpsys wifi | grep 'Wi-Fi is'")
    if not out:
        out = _shell_raw("settings get global wifi_on")
    return out if out else "Could not get WiFi status."


def bluetooth_on():
    _shell_raw("svc bluetooth enable")
    return "Bluetooth turned on."


def bluetooth_off():
    _shell_raw("svc bluetooth disable")
    return "Bluetooth turned off."


def bluetooth_status():
    out = _shell_raw("settings get global bluetooth_on")
    return f"Bluetooth is {'on' if out.strip() == '1' else 'off'}." if out else "Could not get Bluetooth status."


def airplane_on():
    _shell_raw("settings put global airplane_mode_on 1")
    _shell_raw("am broadcast -a android.intent.action.AIRPLANE_MODE --ez state true")
    return "Airplane mode ON."


def airplane_off():
    _shell_raw("settings put global airplane_mode_on 0")
    _shell_raw("am broadcast -a android.intent.action.AIRPLANE_MODE --ez state false")
    return "Airplane mode OFF."


def hotspot_on():
    _shell_raw("svc wifi setsoftap enable")
    return "Hotspot turned on."


def hotspot_off():
    _shell_raw("svc wifi setsoftap disable")
    return "Hotspot turned off."


def dnd_on():
    _shell_raw("settings put global zen_mode 2")
    return "Do Not Disturb ON."


def dnd_off():
    _shell_raw("settings put global zen_mode 0")
    return "Do Not Disturb OFF."


def flashlight_on():
    _shell_raw("cmd statusbar expand-notifications")
    time.sleep(0.5)
    _shell_raw("input tap 540 1200")
    return "Flashlight toggled (via quick settings)."


def flashlight_off():
    return flashlight_on()


def location_on():
    _shell_raw("settings put secure location_mode 3")
    return "Location turned on."


def location_off():
    _shell_raw("settings put secure location_mode 0")
    return "Location turned off."


def nfc_on():
    _shell_raw("svc nfc enable")
    return "NFC turned on."


def nfc_off():
    _shell_raw("svc nfc disable")
    return "NFC turned off."


def auto_rotate_on():
    _shell_raw("settings put system accelerometer_rotation 1")
    return "Auto-rotate on."


def auto_rotate_off():
    _shell_raw("settings put system accelerometer_rotation 0")
    return "Auto-rotate off."


def get_settings(category):
    cmds = {
        "wifi": "settings list global | grep -i wifi",
        "bluetooth": "settings list global | grep -i bluetooth",
        "display": "settings list system | grep -i -E 'screen|bright|font|rotation'",
        "sound": "settings list system | grep -i -E 'volume|ring|notification|alarm'",
        "security": "settings list secure | grep -i -E 'lock|screen|password'",
        "all_global": "settings list global",
        "all_system": "settings list system",
        "all_secure": "settings list secure",
    }
    out = _shell_raw(cmds.get(category, f"settings list {category}"))
    return out[:2000] if out else f"No settings found for '{category}'."


# ──────────────────── NETWORK ────────────────────

def ip_address():
    out = _shell("ip addr show wlan0")
    for line in out.splitlines():
        if "inet " in line:
            return f"Phone IP: {line.strip()}"
    return "Could not get IP address."


def ping(host):
    out = _shell_raw(f"ping -c 3 {host}")
    return out if out else "Ping failed."


def wifi_scan():
    out = _shell_raw("cmd wifi list-scan-results")
    return out[:2000] if out else "No scan results."


def wifi_info():
    out = _shell_raw("dumpsys wifi | grep -A5 'mWifiInfo'")
    return out[:1000] if out else "Could not get WiFi info."


def connected_network():
    out = _shell_raw("dumpsys connectivity | grep 'NetworkAgentInfo'")
    return out[:500] if out else "Could not determine connected network."


# ──────────────────── CALLS & SMS ────────────────────

def make_call(number):
    _shell_raw(f"am start -a android.intent.action.CALL -d tel:{number}")
    return f"Calling {number}..."


def answer_call():
    _shell_raw("input keyevent KEYCODE_CALL")
    return "Call answered."


def reject_call():
    _shell_raw("input keyevent KEYCODE_ENDCALL")
    return "Call rejected."


def end_call():
    _shell_raw("input keyevent KEYCODE_ENDCALL")
    return "Call ended."


def send_sms(number, message):
    _shell_raw(f'am start -a android.intent.action.SENDTO -d "sms:{number}" --es sms_body "{message}" --ez exit_on_sent true')
    return f"SMS to {number}: {message}"


def read_notifications():
    out = _shell_raw("dumpsys notification --noredact | grep -E 'pkg=|title=|text=' | head -30")
    return out if out else "No notifications."


def clear_notifications():
    _shell_raw("service call statusbar 2")
    return "Notifications cleared."


# ──────────────────── CONTACTS & CALENDAR ────────────────────

def list_contacts():
    out = _shell_raw("content query --uri content://com.android.contacts/contacts --projection display_name 2>/dev/null")
    if not out or "error" in out.lower():
        out = _shell_raw("dumpsys contactprovider | grep 'display_name' | head -20")
    return out[:2000] if out else "Could not read contacts."


def search_contacts(name):
    out = _shell_raw(f"content query --uri content://com.android.contacts/contacts --projection display_name --where \"display_name LIKE '%{name}%'\" | head -10")
    return out if out else f"No contacts matching '{name}'."


def list_calendar():
    out = _shell_raw("content query --uri content://com.android.calendar/events --projection title:dtstart | head -10")
    return out[:2000] if out else "No calendar events found."


# ──────────────────── MEDIA ────────────────────

def media_play():
    _shell("input keyevent KEYCODE_MEDIA_PLAY")
    return "Playing."


def media_pause():
    _shell("input keyevent KEYCODE_MEDIA_PAUSE")
    return "Paused."


def media_next():
    _shell("input keyevent KEYCODE_MEDIA_NEXT")
    return "Next track."


def media_previous():
    _shell("input keyevent KEYCODE_MEDIA_PREVIOUS")
    return "Previous track."


def media_stop():
    _shell("input keyevent KEYCODE_MEDIA_STOP")
    return "Media stopped."


# ──────────────────── SYSTEM ────────────────────

def system_info():
    props = {
        "model": "getprop ro.product.model",
        "brand": "getprop ro.product.brand",
        "android": "getprop ro.build.version.release",
        "sdk": "getprop ro.build.version.sdk",
        "security_patch": "getprop ro.build.version.security_patch",
        "serial": "getprop ro.serialno",
        "cpu": "getprop ro.hardware",
        "ram": "cat /proc/meminfo | head -1",
        "uptime": "cat /proc/uptime",
    }
    lines = []
    for key, cmd in props.items():
        val = _shell(cmd) if cmd.startswith("get ") or cmd.startswith("cat ") or cmd.startswith("getprop ") else _shell_raw(cmd)
        lines.append(f"{key}: {val}")
    return "\n".join(lines)


def running_processes():
    out = _shell_raw("ps -A | head -30")
    return out if out else "Could not list processes."


def disk_usage():
    out = _shell("df -h")
    return out if out else "Could not get disk usage."


def logcat(lines=20):
    out = _shell_raw(f"logcat -d -t {lines}")
    return out[:3000] if out else "No log entries."


def get_prop(prop):
    return _shell(f"getprop {prop}")


def set_prop(prop, value):
    _shell_raw(f"setprop {prop} {value}")
    return f"Set {prop} = {value}."


def installed_packages():
    out = _shell("pm list packages -f | head -30")
    return out[:2000] if out else "No packages."


def grant_permission(package, permission):
    _shell_raw(f"pm grant {package} {permission}")
    return f"Granted {permission} to {package}."


def revoke_permission(package, permission):
    _shell_raw(f"pm revoke {package} {permission}")
    return f"Revoked {permission} from {package}."


def notify(title, message):
    _shell_raw(f'am broadcast -a android.intent.action.BOOT_COMPLETED --es title "{title}" --es message "{message}" 2>/dev/null')
    _shell_raw(f'cmd notification post -S bigtext -t "{title}" "jarvis" "{message}" 2>/dev/null')
    return f"Notification sent: {title} — {message}"


def vibrate(ms=500):
    _shell_raw(f"input vibrationtime {ms}")
    return f"Vibrated {ms}ms."


def play_completion_sound():
    try:
        import winsound
        winsound.Beep(800, 150)
        time.sleep(0.1)
        winsound.Beep(1000, 150)
        time.sleep(0.1)
        winsound.Beep(1200, 200)
    except Exception:
        pass


def phone_call(number):
    _shell_raw(f"am start -a android.intent.action.CALL -d tel:{number}")
    return f"Calling {number}."
