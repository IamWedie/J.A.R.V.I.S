import asyncio
import base64
import os
import re
import time

from core.net.adb_controller import (
    _shell, _shell_raw, _is_connected, connect_phone,
    home, back, tap, swipe, swipe_up, swipe_down,
    type_text, open_app, current_activity,
    open_camera, take_photo, take_selfie, switch_camera, toggle_flash,
    notify, vibrate, play_completion_sound, get_camera_facing,
    is_locked, is_in_use, is_screen_on,
)


KNOWN_LAYOUTS = {
    "camera": {
        "shutter": (540, 2200),
        "flash": (150, 150),
        "switch": (950, 2100),
        "video_mode": (540, 2100),
        "gallery": (150, 2200),
    },
    "whatsapp": {
        "new_chat": (950, 2200),
        "search": (540, 180),
        "back": (80, 180),
    },
    "settings": {
        "search": (540, 180),
        "wifi": (540, 400),
        "bluetooth": (540, 550),
    },
    "chrome": {
        "address_bar": (540, 180),
        "new_tab": (950, 180),
    },
}


def _get_current_package():
    out = _shell_raw("dumpsys activity activities | grep -E 'ResumedActivity:'")
    if not out:
        out = _shell_raw("dumpsys activity activities | grep mResumedActivity")
    m = re.search(r"u0 (\S+?)/", out)
    return m.group(1) if m else ""


def _take_screenshot_b64():
    from core.net.adb_controller import screenshot
    local = screenshot()
    if not local or not os.path.exists(local):
        return None
    with open(local, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


async def _try_vision(goal, b64, context=""):
    try:
        from core.brain import ask_vision
        resp = await asyncio.wait_for(ask_vision(goal, b64, context), timeout=100)
        if resp and not resp.startswith("fail"):
            return resp
    except Exception:
        pass
    return None


def _parse_action(response):
    response = response.strip()
    m = re.search(r"ACTION:\s*(.+?)(?:\n|$)", response, re.I)
    if not m:
        m = re.search(r"(tap|swipe|type|press_home|press_back|done|fail)\s*(.*)", response, re.I)
    if not m:
        return None, response
    action_line = m.group(1).strip() if not m.group(0).startswith("ACTION") else m.group(1).strip()
    parts = action_line.split(None, 1)
    action = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    return action, args


def _execute_action(action, args):
    if action == "tap":
        nums = re.findall(r"\d+", args)
        if len(nums) >= 2:
            return tap(int(nums[0]), int(nums[1]))
        return "Could not parse tap coordinates."
    elif action == "swipe":
        nums = re.findall(r"\d+", args)
        if len(nums) >= 4:
            return swipe(int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3]))
        return "Could not parse swipe coordinates."
    elif action == "type":
        return type_text(args.strip())
    elif action in ("press_home", "home"):
        return home()
    elif action in ("press_back", "back"):
        return back()
    else:
        return None


def _smart_action(goal, pkg):
    goal_lower = goal.lower()

    if "selfie" in goal_lower or ("camera" in goal_lower and "selfie" in goal_lower):
        if "camera" not in pkg:
            open_camera()
            time.sleep(2)
        facing = get_camera_facing()
        if facing != "front":
            switch_camera()
            time.sleep(1.0)
        if "flash" in goal_lower:
            toggle_flash()
            time.sleep(0.5)
        take_selfie()
        return "done", "Took selfie" + (" with flash" if "flash" in goal_lower else "") + f" (was {facing} camera)"

    if "camera" in goal_lower and ("photo" in goal_lower or "picture" in goal_lower or "take" in goal_lower):
        if "camera" not in pkg:
            open_camera()
            time.sleep(2)
        if "flash" in goal_lower:
            toggle_flash()
            time.sleep(0.3)
        take_photo()
        return "done", "Photo taken" + (" with flash" if "flash" in goal_lower else "")

    if "camera" in goal_lower and "open" in goal_lower:
        open_camera()
        return "done", "Camera opened"

    if "unlock" in goal_lower:
        if not is_screen_on():
            from core.net.adb_controller import screen_on
            screen_on()
            time.sleep(0.5)
        if not is_locked():
            return "done", "Phone is already unlocked."
        pin = re.search(r"\d{4,6}", goal)
        if pin:
            from core.net.adb_controller import unlock_with_pin
            result = unlock_with_pin(pin.group())
            return "done", result
        from core.net.adb_controller import unlock
        result = unlock()
        return "done", result

    if "whatsapp" in goal_lower:
        if "whatsapp" not in pkg:
            open_app("com.whatsapp")
            time.sleep(2)
        return "done", "WhatsApp opened"

    if "youtube" in goal_lower:
        if "youtube" not in pkg:
            open_app("com.google.android.youtube")
            time.sleep(2)
        return "done", "YouTube opened"

    if "settings" in goal_lower:
        if "settings" not in pkg and "hihonor" not in pkg:
            open_app("settings")
            time.sleep(2)
        return "done", "Settings opened"

    if "home" in goal_lower:
        home()
        return "done", "Went to home screen"

    if "back" in goal_lower:
        back()
        return "done", "Pressed back"

    return None, None


async def run_agent(goal, max_steps=8):
    if not connect_phone():
        yield {"step": 0, "status": "error", "message": "Phone not connected."}
        return

    if is_in_use() and "unlock" not in goal.lower():
        yield {"step": 0, "status": "waiting", "message": "Phone is in use — waiting for you to finish..."}
        while is_in_use():
            await asyncio.sleep(3)
        yield {"step": 0, "status": "resumed", "message": "Phone is free. Proceeding."}

    yield {"step": 0, "status": "started", "message": f"Starting: {goal}"}

    pkg = _get_current_package()
    action, result = _smart_action(goal, pkg)
    if action == "done":
        yield {"step": 1, "status": "done", "message": result}
        return

    context = ""
    for step in range(1, max_steps + 1):
        yield {"step": step, "status": "thinking", "message": f"Step {step}/{max_steps}: Taking screenshot..."}

        b64 = _take_screenshot_b64()
        if not b64:
            yield {"step": step, "status": "error", "message": "Failed to take screenshot."}
            return

        yield {"step": step, "status": "thinking", "message": f"Step {step}/{max_steps}: Analyzing screen..."}

        response = await _try_vision(goal, b64, context)

        if response:
            action, args = _parse_action(response)
            if action and action in ("done", "fail"):
                msg = args or ("Completed" if action == "done" else "Failed")
                yield {"step": step, "status": "done" if action == "done" else "fail", "message": msg}
                return
            if action:
                result = _execute_action(action, args)
                if result:
                    yield {"step": step, "status": "action", "message": f"{action} {args}", "result": result}
                    context += f"Step {step}: {action} {args} → {result}\n"
                    await asyncio.sleep(1.0)
                    continue

        pkg = _get_current_package()
        yield {"step": step, "status": "action", "message": f"(smart fallback) current app: {pkg}"}

        action, result = _smart_action(goal, pkg)
        if action == "done":
            yield {"step": step, "status": "done", "message": result}
            return

        yield {"step": step, "status": "thinking", "message": "Cannot determine next action."}
        break

    yield {"step": max_steps, "status": "limit", "message": "Could not complete task automatically."}


async def agent_task(goal, max_steps=8):
    steps = []
    async for event in run_agent(goal, max_steps):
        steps.append(event)
        if event["status"] in ("done", "fail", "error", "limit"):
            break
    try:
        play_completion_sound()
        status = steps[-1]["status"] if steps else "unknown"
        msg = steps[-1]["message"] if steps else "Task finished"
        emoji = "Done" if status == "done" else "Failed"
        notify(f"JARVIS: {emoji}", f"{goal[:50]} — {msg[:80]}")
        vibrate(300)
    except Exception:
        pass
    return steps
