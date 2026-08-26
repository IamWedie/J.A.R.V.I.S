import asyncio
import threading
import time
from datetime import datetime, timedelta

from core.logging_setup import get_logger

log = get_logger("scheduler")

_reminders = []
_lock = threading.Lock()
_main_loop = None
_fired_callback = None


def init(main_loop, callback):
    global _main_loop, _fired_callback
    _main_loop = main_loop
    _fired_callback = callback


def set_reminder(seconds, message):
    fire_at = time.time() + seconds
    with _lock:
        _reminders.append({"fire_at": fire_at, "message": message, "type": "timer"})
    return f"Reminder set: '{message}' in {seconds // 60}m {seconds % 60}s"


def set_alarm(time_str, message=""):
    try:
        now = datetime.now()
        for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
            try:
                t = datetime.strptime(time_str.strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return f"Could not understand time: {time_str}. Use HH:MM format."
        target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        seconds = (target - now).total_seconds()
        msg = message or f"Alarm at {time_str}"
        with _lock:
            _reminders.append({"fire_at": time.time() + seconds, "message": msg, "type": "alarm"})
        return f"Alarm set for {time_str}: '{msg}'"
    except Exception as e:
        return f"Failed to set alarm: {e}"


def list_reminders():
    with _lock:
        now = time.time()
        active = [r for r in _reminders if r["fire_at"] > now]
    if not active:
        return "No pending reminders or alarms."
    lines = []
    for r in active:
        remaining = r["fire_at"] - time.time()
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        lines.append(f"- \"{r['message']}\" in {mins}m {secs}s")
    return "Pending:\n" + "\n".join(lines)


def cancel_reminder(message_fragment=""):
    with _lock:
        before = len(_reminders)
        if message_fragment:
            _reminders[:] = [r for r in _reminders
                             if r["fire_at"] <= time.time()
                             or message_fragment.lower() not in r["message"].lower()]
        else:
            _reminders.clear()
    removed = before - len(_reminders)
    return f"Cancelled {removed} reminder(s)." if removed else "Nothing to cancel."


def _check_reminders():
    now = time.time()
    due = []
    with _lock:
        remaining = []
        for r in _reminders:
            if r["fire_at"] <= now:
                due.append(r)
            else:
                remaining.append(r)
        _reminders[:] = remaining
    for r in due:
        _fire(r)


def _fire(reminder):
    msg = reminder["message"]
    log.info("firing: %s", msg)
    if _main_loop and _fired_callback:
        try:
            _main_loop.call_soon_threadsafe(
                lambda m=msg: asyncio.ensure_future(_fired_callback(m))
            )
        except Exception:
            pass


def start_checker():
    def loop():
        while True:
            time.sleep(1)
            _check_reminders()
    t = threading.Thread(target=loop, daemon=True)
    t.start()
