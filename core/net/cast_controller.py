import threading
import time
from urllib.parse import urlparse, parse_qs

import pychromecast
from pychromecast.controllers.youtube import YouTubeController
from pychromecast.controllers.media import MediaController

_casts = {}
_cast_lock = threading.Lock()
_discovered = False
_browser = None


def _discover(timeout=5.0):
    global _casts, _discovered, _browser
    try:
        casts, browser = pychromecast.get_listed_chromecasts(discovery_timeout=timeout)
        _browser = browser
        with _cast_lock:
            _casts = {c.cast_info.friendly_name: c for c in casts}
            _discovered = True
        return list(_casts.keys())
    except Exception as e:
        print(f"[cast] discovery failed: {e}")
        return []


def list_devices(refresh=False):
    global _discovered
    if not _discovered or refresh:
        _discover()
    with _cast_lock:
        names = list(_casts.keys())
    if not names:
        return "No Chromecast or Google TV devices found on the network."
    return "Available cast targets:\n" + "\n".join(f"  - {n}" for n in names)


def _get_cast(name):
    with _cast_lock:
        if name in _casts:
            return _casts[name]
        for cn, c in _casts.items():
            if name.lower() in cn.lower():
                return c
    _discover(timeout=4.0)
    with _cast_lock:
        if name in _casts:
            return _casts[name]
        for cn, c in _casts.items():
            if name.lower() in cn.lower():
                return c
    return None


def _yt_id(url):
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0].split("/")[0]
    if "youtube.com" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]
    if len(url) == 11 and url.isalnum():
        return url
    return None


def cast_youtube(url_or_id, target="TV"):
    cast = _get_cast(target)
    if not cast:
        return f"Device '{target}' not found."
    yt_id = _yt_id(url_or_id)
    if not yt_id:
        return f"Could not extract YouTube video ID from: {url_or_id}"
    try:
        cast.wait(timeout=10)
        yt = YouTubeController()
        cast.register_handler(yt)
        yt.play_video(yt_id)
        return f"Playing YouTube on {cast.cast_info.friendly_name}."
    except Exception as e:
        return f"Cast failed: {e}"


def cast_url(url, target="TV"):
    cast = _get_cast(target)
    if not cast:
        return f"Device '{target}' not found."
    try:
        cast.wait(timeout=10)
        mc = cast.media_controller
        mc.play_media(url, "video/mp4")
        mc.block_until_active(timeout=10)
        return f"Casting to {cast.cast_info.friendly_name}."
    except Exception as e:
        return f"Cast failed: {e}"


def cast_status(target="TV"):
    cast = _get_cast(target)
    if not cast:
        return f"Device '{target}' not found."
    try:
        cast.wait(timeout=5)
        mc = cast.media_controller
        if mc.status and mc.status.player_state:
            state = mc.status.player_state
            title = mc.status.title or "Unknown"
            return f"{cast.cast_info.friendly_name}: {state} - {title}"
        return f"{cast.cast_info.friendly_name}: idle (nothing playing)."
    except Exception as e:
        return f"Status check failed: {e}"


def stop_cast(target="TV"):
    cast = _get_cast(target)
    if not cast:
        return f"Device '{target}' not found."
    try:
        cast.wait(timeout=5)
        mc = cast.media_controller
        mc.stop()
        return f"Stopped playback on {cast.cast_info.friendly_name}."
    except Exception as e:
        return f"Stop failed: {e}"


def pause_cast(target="TV"):
    cast = _get_cast(target)
    if not cast:
        return f"Device '{target}' not found."
    try:
        cast.wait(timeout=5)
        mc = cast.media_controller
        mc.pause()
        return f"Paused on {cast.cast_info.friendly_name}."
    except Exception as e:
        return f"Pause failed: {e}"


def resume_cast(target="TV"):
    cast = _get_cast(target)
    if not cast:
        return f"Device '{target}' not found."
    try:
        cast.wait(timeout=5)
        mc = cast.media_controller
        mc.play()
        return f"Resumed on {cast.cast_info.friendly_name}."
    except Exception as e:
        return f"Resume failed: {e}"
