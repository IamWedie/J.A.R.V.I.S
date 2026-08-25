import asyncio
import contextlib
import json
from datetime import datetime

from openai import AsyncOpenAI

import core.config as config
from core import memory
from core import netdiscovery
from core.net import cast_controller
from core.net import adb_controller
from core.net import agent as phone_agent
from core.tools import pc_tools, web_tools

SYSTEM_PROMPT = (
    "You are JARVIS, a witty, precise AI assistant with a voice, living on the user's Windows PC. "
    "Your replies are SPOKEN aloud to the user: keep them short and conversational (1-3 sentences), "
    "never output lists, markdown, code blocks, or raw data unless explicitly asked. "
    "Address the user respectfully.\n"
    "Tool rules:\n"
    "- For greetings, thanks, farewells, opinions, or general chat, reply directly WITHOUT tools.\n"
    "- Use tools when the request needs a PC action or live data.\n"
    "- After a tool result arrives, answer briefly using it; never invent values that were not returned.\n"
    "- NEVER close an app unless explicitly asked. NEVER repeat a tool call with identical arguments.\n"
    "- You have PERMANENT local memory. When the user asks you to remember something, call remember_fact. "
    "To look up past conversations or personal details, call recall_memories. Never claim you cannot remember.\n"
    "- You can see the user's WiFi network: use list_network_devices when they ask what devices are connected, "
    "and where_is_device to check if a specific device (TV, phone, laptop) is home.\n"
    "- You can cast YouTube videos and web content to Chromecast/Google TV devices: use cast_youtube for YouTube, "
    "cast_url for any web video. Use list_cast_devices to discover available targets.\n"
    "- The user's phone (Honor, Android 14) is connected via wireless ADB. You have FULL control: "
    "unlock with PIN, take photos/selfies with flash, open/close any app, read/send SMS, make calls, "
    "browse files, toggle WiFi/Bluetooth/airplane, control volume/brightness, reboot, read notifications, "
    "share clipboard, and tap/swipe/type anything. Always use phone_unlock_with_pin when user asks to unlock.\n"
    "- For complex multi-step phone tasks (e.g. 'take a selfie and send it', 'open YouTube and play a video', "
    "'set an alarm'), use phone_agent — it autonomously takes screenshots, reads the screen, and executes "
    "the needed taps/swipes/types to accomplish the goal.\n"
    "- Never mention tools, JSON, or result mechanics; speak naturally."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "launch_app",
        "description": "Open ANY application installed on the PC by fuzzy name match.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "close_app",
        "description": "Force-close an application by name. ONLY when the user explicitly asks.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "set_volume",
        "description": "Set master speaker volume (0-100).",
        "parameters": {"type": "object", "properties": {
            "level": {"type": "integer"},
        }, "required": ["level"]},
    }},
    {"type": "function", "function": {
        "name": "get_volume",
        "description": "Get current master volume percentage.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "media_key",
        "description": "Send a media key: play_pause, next, previous, stop, mute.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop", "mute"]},
        }, "required": ["action"]},
    }},
    {"type": "function", "function": {
        "name": "take_screenshot",
        "description": "Capture the screen and save it as an image file.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "system_info",
        "description": "Get CPU %, RAM %, battery status, disk usage.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "top_processes",
        "description": "List apps consuming the most RAM or CPU right now with real numbers.",
        "parameters": {"type": "object", "properties": {
            "metric": {"type": "string", "enum": ["memory", "cpu"]},
            "limit": {"type": "integer"},
        }},
    }},
    {"type": "function", "function": {
        "name": "list_running_apps",
        "description": "List names of running applications.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "search_files",
        "description": "Search files by name in user folders or a specific folder path.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "location": {"type": "string", "description": "'all', 'documents', 'downloads', etc., or an absolute folder path"},
            "limit": {"type": "integer"},
        }, "required": ["pattern"]},
    }},
    {"type": "function", "function": {
        "name": "get_clipboard",
        "description": "Read the current clipboard text.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "set_clipboard",
        "description": "Copy given text to the clipboard.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
        }, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "minimize_all_windows",
        "description": "Minimize every open window.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "focus_window",
        "description": "Bring a window to front by part of its title.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
        }, "required": ["title"]},
    }},
    {"type": "function", "function": {
        "name": "type_text",
        "description": "Type text into the focused window via paste. Requires user approval in the UI.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
        }, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "open_url",
        "description": "Open a website URL in the default browser.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
        }, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "where_is_device",
        "description": "Check whether a specific device (by name, brand or type like 'tv' or 'samsung') is present on the network.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "cast_youtube",
        "description": "Play a YouTube video on a Chromecast or Google TV. Pass the URL or video ID.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "YouTube URL or 11-char video ID"},
            "target": {"type": "string", "description": "Device name (e.g. 'Living Room TV'). Omit to pick first available."},
        }, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "cast_url",
        "description": "Cast any web video or media URL to a Chromecast/Google TV.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
            "target": {"type": "string", "description": "Device name. Omit to pick first available."},
        }, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "stop_cast",
        "description": "Stop whatever is playing on a Chromecast/Google TV.",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string", "description": "Device name. Omit for first available."},
        }},
    }},
    {"type": "function", "function": {
        "name": "pause_cast",
        "description": "Pause playback on a Chromecast/Google TV.",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string", "description": "Device name. Omit for first available."},
        }},
    }},
    {"type": "function", "function": {
        "name": "resume_cast",
        "description": "Resume paused playback on a Chromecast/Google TV.",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string", "description": "Device name. Omit for first available."},
        }},
    }},
    {"type": "function", "function": {
        "name": "cast_status",
        "description": "Check what's currently playing on a Chromecast/Google TV.",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string", "description": "Device name. Omit for first available."},
        }},
    }},
    {"type": "function", "function": {
        "name": "list_cast_devices",
        "description": "Discover Chromecast and Google TV devices on the network.",
        "parameters": {"type": "object", "properties": {
            "refresh": {"type": "boolean", "description": "true to force re-discovery"},
        }},
    }},
    {"type": "function", "function": {
        "name": "list_network_devices",
        "description": "Scan the WiFi network and list every device found (TVs, phones, laptops) with type, name and IP.",
        "parameters": {"type": "object", "properties": {
            "refresh": {"type": "boolean", "description": "true = force a fresh scan (takes ~10s); false = use recent scan"},
        }},
    }},
    {"type": "function", "function": {
        "name": "where_is_device",
        "description": "Check whether a specific device (by name, brand or type like 'tv' or 'samsung') is present on the network.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "phone_info",
        "description": "Get phone model, Android version, battery, resolution.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_battery",
        "description": "Detailed battery info: level, temp, voltage, charging.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_screenshot",
        "description": "Screenshot the phone screen.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_unlock",
        "description": "Wake and swipe to unlock (no PIN).",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_unlock_with_pin",
        "description": "Wake, swipe, enter PIN to unlock.",
        "parameters": {"type": "object", "properties": {
            "pin": {"type": "string"},
        }, "required": ["pin"]},
    }},
    {"type": "function", "function": {
        "name": "phone_brightness",
        "description": "Set screen brightness 0-255.",
        "parameters": {"type": "object", "properties": {
            "level": {"type": "integer"},
        }, "required": ["level"]},
    }},
    {"type": "function", "function": {
        "name": "phone_home",
        "description": "Press home button.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_back",
        "description": "Press back button.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_tap",
        "description": "Tap x,y on phone screen (1080x2412).",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
        }, "required": ["x", "y"]},
    }},
    {"type": "function", "function": {
        "name": "phone_swipe",
        "description": "Swipe from (x1,y1) to (x2,y2).",
        "parameters": {"type": "object", "properties": {
            "x1": {"type": "integer"}, "y1": {"type": "integer"},
            "x2": {"type": "integer"}, "y2": {"type": "integer"},
        }, "required": ["x1", "y1", "x2", "y2"]},
    }},
    {"type": "function", "function": {
        "name": "phone_swipe_up",
        "description": "Swipe up (scroll down).",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_swipe_down",
        "description": "Swipe down (notification shade / scroll up).",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_type",
        "description": "Type text into focused field.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
        }, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "phone_open_app",
        "description": "Open app by package or shortcut: camera, settings, chrome, phone, contacts, messages, gallery, youtube.",
        "parameters": {"type": "object", "properties": {
            "package": {"type": "string"},
        }, "required": ["package"]},
    }},
    {"type": "function", "function": {
        "name": "phone_close_app",
        "description": "Force-stop an app.",
        "parameters": {"type": "object", "properties": {
            "package": {"type": "string"},
        }, "required": ["package"]},
    }},
    {"type": "function", "function": {
        "name": "phone_list_apps",
        "description": "List installed third-party apps.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_current_app",
        "description": "Show currently active app.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_take_photo",
        "description": "Take a photo with rear camera.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_take_selfie",
        "description": "Switch to front camera and take a selfie.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_flash_on",
        "description": "Toggle camera flash.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_switch_camera",
        "description": "Switch front/rear camera.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_get_clipboard",
        "description": "Get phone clipboard text.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_share_text",
        "description": "Open share dialog with text.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
        }, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "phone_list_files",
        "description": "List files on phone. Default /sdcard/. Use /sdcard/Download/, /sdcard/DCIM/Camera/, etc.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }},
    }},
    {"type": "function", "function": {
        "name": "phone_find_files",
        "description": "Search phone files by name.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "phone_delete_file",
        "description": "Delete a file on phone.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "phone_storage",
        "description": "Show phone storage usage.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_wifi_on",
        "description": "Turn on WiFi.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_wifi_off",
        "description": "Turn off WiFi.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_bluetooth_on",
        "description": "Turn on Bluetooth.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_bluetooth_off",
        "description": "Turn off Bluetooth.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_airplane_on",
        "description": "Turn on airplane mode.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_airplane_off",
        "description": "Turn off airplane mode.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_make_call",
        "description": "Call a phone number.",
        "parameters": {"type": "object", "properties": {
            "number": {"type": "string"},
        }, "required": ["number"]},
    }},
    {"type": "function", "function": {
        "name": "phone_end_call",
        "description": "End current call.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_send_sms",
        "description": "Send SMS to a number.",
        "parameters": {"type": "object", "properties": {
            "number": {"type": "string"}, "message": {"type": "string"},
        }, "required": ["number", "message"]},
    }},
    {"type": "function", "function": {
        "name": "phone_read_notifications",
        "description": "Read current phone notifications.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_volume_up",
        "description": "Volume up.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_volume_down",
        "description": "Volume down.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_media_play",
        "description": "Resume media playback.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_media_pause",
        "description": "Pause media playback.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_media_next",
        "description": "Next media track.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_reboot",
        "description": "Reboot the phone.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_shutdown",
        "description": "Shut down the phone.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_contacts",
        "description": "List contacts on the phone.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "phone_agent",
        "description": "Autonomously perform a multi-step task on the phone. JARVIS will take screenshots, read the screen, and execute taps/swipes/types to accomplish the goal. Use for complex tasks like 'take a selfie and send it', 'set an alarm for 7am', 'open Instagram and like the first post'.",
        "parameters": {"type": "object", "properties": {
            "goal": {"type": "string", "description": "The multi-step task to accomplish on the phone"},
        }, "required": ["goal"]},
    }},
    {"type": "function", "function": {
        "name": "remember_fact",
        "description": "Store a permanent fact in local memory (e.g. 'My sister's name is Lina').",
        "parameters": {"type": "object", "properties": {
            "fact": {"type": "string"},
        }, "required": ["fact"]},
    }},
    {"type": "function", "function": {
        "name": "forget_fact",
        "description": "Delete facts matching the given text from memory.",
        "parameters": {"type": "object", "properties": {
            "substring": {"type": "string"},
        }, "required": ["substring"]},
    }},
    {"type": "function", "function": {
        "name": "recall_memories",
        "description": "Search all past conversations and stored facts for the given text.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "lock_screen",
        "description": "Lock the PC screen. Requires user approval.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "sleep_pc",
        "description": "Put the PC to sleep. Requires user approval in the UI.",
        "parameters": {"type": "object", "properties": {}},
    }},
]

TOOL_FUNCTIONS = {
    "launch_app": pc_tools.launch_app,
    "close_app": pc_tools.close_app,
    "set_volume": pc_tools.set_volume,
    "get_volume": pc_tools.get_volume,
    "media_key": pc_tools.media_key,
    "take_screenshot": pc_tools.take_screenshot,
    "system_info": pc_tools.system_info,
    "top_processes": pc_tools.top_processes,
    "list_running_apps": pc_tools.list_running_apps,
    "search_files": pc_tools.search_files,
    "get_clipboard": pc_tools.get_clipboard,
    "set_clipboard": pc_tools.set_clipboard,
    "minimize_all_windows": pc_tools.minimize_all_windows,
    "focus_window": pc_tools.focus_window,
    "type_text": pc_tools.type_text,
    "open_url": pc_tools.open_url,
    "web_search": web_tools.web_search,
    "remember_fact": memory.add_fact,
    "forget_fact": memory.remove_fact,
    "recall_memories": lambda query: _recall_memories(query),
    "list_network_devices": lambda refresh=False: netdiscovery.format_devices(
        netdiscovery.get_devices(force=bool(refresh))
    ),
    "where_is_device": lambda name: netdiscovery.format_devices(netdiscovery.find_device(name)),
    "cast_youtube": cast_controller.cast_youtube,
    "cast_url": cast_controller.cast_url,
    "stop_cast": cast_controller.stop_cast,
    "pause_cast": cast_controller.pause_cast,
    "resume_cast": cast_controller.resume_cast,
    "cast_status": cast_controller.cast_status,
    "list_cast_devices": lambda refresh=False: cast_controller.list_devices(refresh=bool(refresh)),
    "phone_info": adb_controller.device_info,
    "phone_battery": adb_controller.battery,
    "phone_screenshot": adb_controller.screenshot,
    "phone_unlock": adb_controller.unlock,
    "phone_unlock_with_pin": adb_controller.unlock_with_pin,
    "phone_brightness": adb_controller.screen_brightness,
    "phone_home": adb_controller.home,
    "phone_back": adb_controller.back,
    "phone_tap": adb_controller.tap,
    "phone_swipe": adb_controller.swipe,
    "phone_swipe_up": adb_controller.swipe_up,
    "phone_swipe_down": adb_controller.swipe_down,
    "phone_type": adb_controller.type_text,
    "phone_open_app": adb_controller.open_app,
    "phone_close_app": adb_controller.close_app,
    "phone_list_apps": adb_controller.list_apps,
    "phone_current_app": adb_controller.current_activity,
    "phone_take_photo": adb_controller.take_photo,
    "phone_take_selfie": adb_controller.take_selfie,
    "phone_flash_on": adb_controller.flash_on,
    "phone_switch_camera": adb_controller.switch_camera,
    "phone_get_clipboard": adb_controller.get_clipboard,
    "phone_share_text": adb_controller.share_text,
    "phone_list_files": adb_controller.list_files,
    "phone_find_files": adb_controller.find_files,
    "phone_delete_file": adb_controller.delete_file,
    "phone_storage": adb_controller.storage_info,
    "phone_wifi_on": adb_controller.wifi_on,
    "phone_wifi_off": adb_controller.wifi_off,
    "phone_bluetooth_on": adb_controller.bluetooth_on,
    "phone_bluetooth_off": adb_controller.bluetooth_off,
    "phone_airplane_on": adb_controller.airplane_on,
    "phone_airplane_off": adb_controller.airplane_off,
    "phone_make_call": adb_controller.make_call,
    "phone_end_call": adb_controller.end_call,
    "phone_send_sms": adb_controller.send_sms,
    "phone_read_notifications": adb_controller.read_notifications,
    "phone_volume_up": adb_controller.volume_up,
    "phone_volume_down": adb_controller.volume_down,
    "phone_media_play": adb_controller.media_play,
    "phone_media_pause": adb_controller.media_pause,
    "phone_media_next": adb_controller.media_next,
    "phone_reboot": adb_controller.reboot,
    "phone_shutdown": adb_controller.shutdown,
    "phone_contacts": adb_controller.list_contacts,
    "phone_agent": lambda goal: _run_agent(goal),
    "lock_screen": pc_tools.lock_screen,
    "sleep_pc": pc_tools.sleep_pc,
}


def _recall_memories(query):
    convs = memory.search_conversations(query, limit=5)
    ql = str(query).lower()
    facts = [f for f in memory.list_facts() if ql in f.lower()]
    parts = []
    if facts:
        parts.append("Facts:\n" + "\n".join(f"- {f}" for f in facts))
    if convs:
        parts.append("Past conversation:\n" + "\n".join(
            f"[{c['ts']}] {c['role']}: {c['text'][:200]}" for c in convs
        ))
    if not parts:
        return "Nothing found in memory about that."
    return "\n".join(parts)


async def _run_agent_async(goal):
    steps = await phone_agent.agent_task(goal, max_steps=12)
    actions = [s for s in steps if s["status"] == "action"]
    done = next((s for s in steps if s["status"] == "done"), None)
    fail = next((s for s in steps if s["status"] in ("fail", "error")), None)
    if done:
        return f"Agent completed the task in {len(actions)} steps: {done['message']}"
    if fail:
        return f"Agent failed after {len(actions)} steps: {fail['message']}"
    return f"Agent performed {len(actions)} steps but did not complete the task."


def _run_agent(goal):
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(asyncio.run, _run_agent_async(goal))
        return future.result(timeout=180)


def _memory_context(user_text):
    try:
        facts = memory.relevant_facts(user_text)
        recent = memory.recent_conversations(14)
        parts = []
        if facts:
            parts.append("Known facts:\n" + "\n".join(f"- {f}" for f in facts))
        if recent:
            parts.append("Recent conversation:\n" + "\n".join(
                f"{r['role']}: {r['text'][:160]}" for r in recent
            ))
        if not parts:
            return ""
        return "\n\nLOCAL MEMORY (stored on the user's PC):\n" + "\n\n".join(parts)
    except Exception as e:
        print(f"memory context failed: {e}")
        return ""

APPROVAL_REQUIRED = {"type_text", "lock_screen", "sleep_pc"}

APPROVAL_DESCRIPTIONS = {
    "type_text": lambda a: f'Type "{a.get("text", "")[:80]}" into the focused window',
    "lock_screen": lambda a: "Lock the screen",
    "sleep_pc": lambda a: "Put the PC to sleep",
}

MAX_TOOL_ROUNDS = 6
FORCE_ANSWER_AT_ROUND = 4

FRIENDLY_NAMES = {
    "x-preview-f-free": "Ox Alpha Free",
    "big-pickle": "Big Pickle",
    "claude-fable-5": "Claude Fable 5",
    "claude-opus-5": "Claude Opus 5",
    "claude-opus-4-8": "Claude Opus 4.8",
    "claude-opus-4-7": "Claude Opus 4.7",
    "claude-opus-4-6": "Claude Opus 4.6",
    "claude-opus-4-5": "Claude Opus 4.5",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-sonnet-4-5": "Claude Sonnet 4.5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    "gemini-3-flash": "Gemini 3 Flash",
    "gemini-3.7-flash": "Gemini 3.7 Flash",
    "gemini-3.6-flash": "Gemini 3.6 Flash",
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-3.1-pro": "Gemini 3.1 Pro",
    "glm-5.2": "GLM 5.2",
    "glm-5.1": "GLM 5.1",
    "gpt-5": "GPT 5",
    "gpt-5-nano": "GPT 5 Nano",
    "gpt-5.5": "GPT 5.5",
    "gpt-5.6-luna": "GPT 5.6 Luna",
    "gpt-5.6-sol": "GPT 5.6 Sol",
    "gpt-5.6-terra": "GPT 5.6 Terra",
    "grok-4.5": "Grok 4.5",
    "grok-4.6": "Grok 4.6",
    "kimi-k2.5": "Kimi K2.5",
    "kimi-k2.6": "Kimi K2.6",
    "kimi-k3": "Kimi K3",
    "minimax-m2.5": "MiniMax M2.5",
    "minimax-m3": "MiniMax M3",
    "qwen3.5-plus": "Qwen 3.5 Plus",
    "qwen3.6-plus": "Qwen 3.6 Plus",
}

FREE_MODELS = {
    "x-preview-f-free", "big-pickle", "mimo-v2.5-free", "hy3-free",
    "nemotron-3-ultra-free", "nemotron-3.5-lightning-free",
    "muse-spark-1.2-contributor-free", "laguna-s-2.1-free",
    "deepseek-v4-flash-free",
}


def is_free_model(mid):
    return mid in FREE_MODELS or mid.endswith("-free")


class Brain:
    def __init__(self):
        self.client = None
        self.model = config.DEFAULT_MODEL
        self.history = []
        self.approval_future = None

    def reset_client(self):
        self.client = None
        self.model = config.DEFAULT_MODEL
        self.reset_history()

    def _ensure_client(self):
        if self.client is None:
            if not config.ZEN_API_KEY:
                raise RuntimeError("ZEN_API_KEY is not set. Add your Zen key to jarvis\\.env")
            self.client = AsyncOpenAI(
                base_url=config.ZEN_BASE_URL,
                api_key=config.ZEN_API_KEY,
                timeout=60,
            )
        return self.client

    async def _stream_round(self, messages, tools, on_chunk=None):
        client = self._ensure_client()
        candidates = [self.model] + [m for m in FALLBACK_MODELS if m != self.model]
        last_error = None
        for i, model in enumerate(candidates):
            try:
                stream = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    temperature=0.4,
                    stream=True,
                )
            except Exception as e:
                last_error = e
                continue
            if i > 0:
                print(f"(switched brain to {model})")
                self.model = model
            content_parts = []
            tool_acc = {}
            try:
                async with stream:
                    async for chunk in stream:
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        if delta is None:
                            continue
                        if delta.content:
                            content_parts.append(delta.content)
                            if on_chunk:
                                try:
                                    await on_chunk(delta.content)
                                except Exception:
                                    pass
                        if delta.tool_calls:
                            for tc in delta.tool_calls:
                                idx = tc.index if tc.index is not None else 0
                                acc = tool_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                                if tc.id:
                                    acc["id"] = tc.id
                                if tc.function:
                                    if tc.function.name:
                                        acc["name"] += tc.function.name
                                    if tc.function.arguments:
                                        acc["args"] += tc.function.arguments
            except Exception as e:
                print(f"stream interrupted on {model}: {e}")
                with contextlib.suppress(Exception):
                    await stream.close()
                raise
            content = "".join(content_parts)
            if tool_acc:
                calls = []
                for idx in sorted(tool_acc):
                    acc = tool_acc[idx]
                    calls.append({
                        "id": acc["id"] or f"call_{idx}",
                        "type": "function",
                        "function": {"name": acc["name"], "arguments": acc["args"] or "{}"},
                    })
                return content, calls
            return content, None
        raise last_error

    async def fetch_models(self):
        client = self._ensure_client()
        response = await client.models.list()
        result = []
        for m in response.data:
            mid = m.id
            label = FRIENDLY_NAMES.get(mid) or " ".join(
                w.upper() if w in ("ai", "llm") else w.capitalize()
                for w in mid.replace("-", " ").replace("_", " ").split()
            )
            free = is_free_model(mid)
            if free:
                label += "  •FREE"
            result.append({"id": mid, "label": label, "free": free})
        result.sort(key=lambda x: (not x["free"], x["label"].lower()))
        return result

    def reset_history(self):
        self.history = []

    async def ask(self, user_text, on_chunk=None):
        system_message = {
            "role": "system",
            "content": SYSTEM_PROMPT + f"\nCurrent date and time: {datetime.now():%A %d %B %Y, %H:%M}." + _memory_context(user_text),
        }
        messages = [system_message] + self.history + [
            {"role": "user", "content": user_text}
        ]
        last_call_key = None

        for round_index in range(MAX_TOOL_ROUNDS):
            use_tools = TOOLS if round_index < FORCE_ANSWER_AT_ROUND else None
            content, tool_calls = await self._stream_round(messages, use_tools, on_chunk)

            if not tool_calls:
                reply = content or ""
                self.history.append({"role": "user", "content": user_text})
                self.history.append({"role": "assistant", "content": reply})
                try:
                    memory.log("user", user_text)
                    memory.log("assistant", reply)
                except Exception as e:
                    print(f"memory log failed: {e}")
                if len(self.history) > 40:
                    self.history = self.history[-40:]
                return reply

            messages.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": tool_calls,
            })

            async def execute_one(call):
                fn = TOOL_FUNCTIONS.get(call["function"]["name"])
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                name = call["function"]["name"]
                if fn is None:
                    return call, f"Unknown tool: {name}"
                if name in APPROVAL_REQUIRED:
                    approved = await self.request_approval(name, args)
                    if not approved:
                        return call, "The user DENIED this action. Do not retry it."
                try:
                    return call, await asyncio.to_thread(fn, **args)
                except Exception as e:
                    return call, f"Tool error: {e}"

            normal = [c for c in tool_calls if c["function"]["name"] not in APPROVAL_REQUIRED]
            gated = [c for c in tool_calls if c["function"]["name"] in APPROVAL_REQUIRED]

            results = []
            if normal:
                results.extend(await asyncio.gather(*(execute_one(c) for c in normal)))
            for c in gated:
                results.append(await execute_one(c))

            for call, result in results:
                call_key = (call["function"]["name"], call["function"]["arguments"])
                if call_key == last_call_key:
                    result = f"{result}\n(Already provided. Answer now.)"
                last_call_key = call_key
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": str(result),
                })

        return "I apologize sir, I could not complete that request."

    async def request_approval(self, name, args):
        desc = APPROVAL_DESCRIPTIONS.get(name, lambda a: name)(args)
        from server import send_event
        future = asyncio.get_running_loop().create_future()
        self.approval_future = future
        await send_event({
            "type": "approval_request",
            "tool": name,
            "description": desc,
        })
        try:
            return await asyncio.wait_for(future, timeout=120)
        except asyncio.TimeoutError:
            return False
        finally:
            self.approval_future = None

    def resolve_approval(self, approved):
        if self.approval_future and not self.approval_future.done():
            self.approval_future.set_result(bool(approved))


FALLBACK_MODELS = ["laguna-s-2.1-free", "big-pickle", "nemotron-3.5-lightning-free"]


async def ask_vision(goal, screenshot_b64, context=""):
    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        base_url=config.ZEN_BASE_URL,
        api_key=config.ZEN_API_KEY,
        timeout=60,
    )
    model = config.DEFAULT_MODEL or "mimo-v2.5-free"
    models = [model] + [m for m in FALLBACK_MODELS if m != model]
    messages = [
        {"role": "system", "content": (
            "You are an Android phone agent controlling an Honor LLY-LX2 (1080x2412). "
            "You see a screenshot of the phone screen. The user wants you to accomplish a goal. "
            "Respond with EXACTLY ONE action in this format:\n\n"
            "ACTION: <action_name> <args>\nREASON: <brief reason>\n\n"
            "Available actions:\n"
            "- tap <x> <y> — tap at screen coordinates\n"
            "- swipe <x1> <y1> <x2> <y2> — swipe gesture\n"
            "- type <text> — type text into focused field\n"
            "- press_home — go to home screen\n"
            "- press_back — go back\n"
            "- done <message> — task is complete, include what happened\n"
            "- fail <reason> — cannot complete\n\n"
            "RULES:\n"
            "- Only output ONE action\n"
            "- Be precise with coordinates for 1080x2412 screen\n"
            "- Shutter button is at ~(540, 2200)\n"
            "- Flash toggle is at ~(150, 150)\n"
            "- Camera switch is at ~(950, 150)\n"
            "- Status bar is ~0-100px from top\n"
            "- Navigation bar is ~2300-2412px from top\n"
            "- Common keyboard OK/Enter is at ~(900, 2100)\n"
        )},
        {"role": "user", "content": [
            {"type": "text", "text": f"Goal: {goal}\n{context}"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
        ]},
    ]
    for m in models:
        try:
            resp = await client.chat.completions.create(model=m, messages=messages, max_tokens=200)
            return resp.choices[0].message.content.strip()
        except Exception:
            continue
    return "fail: all vision models unavailable"
