import asyncio
import ctypes
import os
import sys
import threading
import time
import urllib.request

import uvicorn
import webview

import core.config as config
from server import app, brain

MUTEX_NAME = "Wadia.JARVIS.SingleInstance"
AUMID = "Wadia.JARVIS.App"
LOCK_PORT = config.PORT + 1

_lock_socket = None

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "app.log")
    log_file = open(log_path, "a", buffering=1, encoding="utf-8")
    sys.stdout = log_file
    sys.stderr = log_file
    print(f"\n===== JARVIS session {time.strftime('%Y-%m-%d %H:%M:%S')} =====")


def guard_single_instance():
    global _lock_socket
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
        sock.listen(1)
    except OSError:
        sock.close()
        return False
    _lock_socket = sock
    return True


def set_app_user_model_id():
    try:
        ctypes.windll.ole32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    except Exception:
        pass


def run_server():
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


def wait_for_server(timeout=25):
    url = f"http://{config.HOST}:{config.PORT}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def preload():
    try:
        asyncio.run(brain.fetch_models())
    except Exception as e:
        print(f"(startup notice: {e})")
    try:
        from core.stt import transcriber
        transcriber.preload()
        print("whisper preloaded")
    except Exception as e:
        print(f"(whisper preload notice: {e})")


def set_taskbar_icon():
    def apply():
        try:
            import win32con  # not installed; fallback below
        except ImportError:
            pass
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, "JARVIS")
            if not hwnd:
                return
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x10
            hicon = ctypes.windll.user32.LoadImageW(
                None, os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis.ico"),
                IMAGE_ICON, 32, 32, LR_LOADFROMFILE,
            )
            WM_SETICON = 0x80
            ICON_SMALL = 0
            ICON_BIG = 1
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
            print("taskbar icon applied")
        except Exception as e:
            print(f"taskbar icon failed: {e}")

    threading.Timer(3.0, apply).start()


if __name__ == "__main__":
    setup_logging()
    if not guard_single_instance():
        print("JARVIS is already running.")
        sys.exit(0)

    set_app_user_model_id()
    start_hidden = "--hidden" in sys.argv
    check_ok = bool(config.ZEN_API_KEY)

    if check_ok:
        threading.Thread(target=preload, daemon=True).start()
    threading.Thread(target=run_server, daemon=True).start()
    wait_for_server()

    window = webview.create_window(
        config.ASSISTANT_NAME,
        f"http://{config.HOST}:{config.PORT}",
        width=420,
        height=760,
        resizable=True,
        background_color="#070b14",
    )

    from tray import start_tray, on_window_close_request, set_pause_callback
    window.events.closing += on_window_close_request

    if start_hidden:
        def hide_when_ready():
            try:
                window.hide()
                print("boot: started hidden in tray")
            except Exception as e:
                print(f"hide failed: {e}")
        window.events.loaded += hide_when_ready

    def on_pause(paused):
        import server
        server.listening_paused = paused
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.call_soon_threadsafe(server.update_wake_arm)
            else:
                server.update_wake_arm()
        except RuntimeError:
            pass
        print(f"listening paused: {paused}")

    set_pause_callback(on_pause)

    set_taskbar_icon()
    webview.start(start_tray)
