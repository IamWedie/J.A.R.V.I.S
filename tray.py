import os
import threading

import pystray
from PIL import Image

_icon = None

PAUSED = {"value": False}
_on_pause_toggle = None


def _load_image():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis.ico")
    return Image.open(path)


def _show():
    try:
        import webview
        if webview.windows:
            webview.windows[0].show()
    except Exception as e:
        print(f"tray show failed: {e}")


def _toggle_pause(icon, item):
    PAUSED["value"] = not PAUSED["value"]
    if _on_pause_toggle:
        _on_pause_toggle(PAUSED["value"])


def is_paused():
    return PAUSED["value"]


def set_pause_callback(fn):
    global _on_pause_toggle
    _on_pause_toggle = fn


def _quit(icon, item):
    global _icon
    try:
        import webview
        if webview.windows:
            webview.windows[0].destroy()
    except Exception:
        pass
    if _icon:
        _icon.stop()
    os._exit(0)


def start_tray(*args):
    global _icon

    menu = pystray.Menu(
        pystray.MenuItem("Open JARVIS", lambda i, t: threading.Thread(target=_show).start(), default=True),
        pystray.MenuItem(
            lambda item: "Resume listening" if PAUSED["value"] else "Pause listening",
            _toggle_pause,
        ),
        pystray.MenuItem("Quit", _quit),
    )
    _icon = pystray.Icon("JARVIS", _load_image(), "JARVIS", menu)
    _icon.run_detached()


def on_window_close_request(window):
    try:
        window.hide()
    except Exception:
        pass
    return False
