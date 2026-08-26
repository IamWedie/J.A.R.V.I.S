import json
import os
import threading
import time
import urllib.parse
import urllib.request

import core.config as config

API = "https://api.telegram.org/bot{token}/{method}"
_owner_chat_id = None
_thread = None
_brain = None
_lock = threading.Lock()


def _config_token():
    return getattr(config, "TELEGRAM_BOT_TOKEN", "") or ""


def _config_chat():
    return str(getattr(config, "TELEGRAM_CHAT_ID", "") or "")


def _call(method, payload=None, timeout=35):
    token = _config_token()
    if not token:
        raise RuntimeError("no telegram token")
    url = API.format(token=token, method=method)
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        result = json.loads(r.read().decode())
    if not result.get("ok"):
        raise RuntimeError(f"telegram {method}: {result.get('description')}")
    return result["result"]


def _send(chat_id, text):
    text = str(text).strip() or "(empty)"
    while text:
        piece, text = text[:4000], text[4000:]
        _call("sendMessage", {"chat_id": chat_id, "text": piece})


def _get_brain():
    global _brain
    with _lock:
        if _brain is None:
            from core.brain import Brain
            _brain = Brain()
    return _brain


def telegram_notify(message, chat_id=None):
    target = chat_id or _owner_chat_id or _config_chat()
    if not target:
        return "Telegram not linked yet — send /start to the bot from your account once."
    try:
        _send(target, f"🤖 JARVIS: {message}")
        return f"Sent to Telegram: {message}"
    except Exception as e:
        return f"Telegram send failed: {e}"


def _handle_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    sender = msg.get("from", {}).get("username") or msg.get("from", {}).get("first_name", "?")

    global _owner_chat_id
    expected = _owner_chat_id or _config_chat()

    if not expected:
        if text == "/start":
            _owner_chat_id = chat_id
            try:
                config.save_settings({"TELEGRAM_CHAT_ID": chat_id})
            except Exception:
                pass
            _send(chat_id, f"Linked. You are now the owner, {sender}. Talk to me like you talk to JARVIS.")
        else:
            _send(chat_id, "Unauthorized device. The owner must send /start first.")
        return

    if chat_id != expected:
        _send(chat_id, "Not your assistant. Goodbye.")
        return

    if not text:
        return

    import asyncio
    brain = _get_brain()
    t0 = time.time()
    try:
        reply = asyncio.run(brain.ask(f"[via Telegram from {sender}] {text}"))
    except Exception as e:
        reply = f"Brain error: {e}"
    print(f"[tg] {sender}: {text[:60]} -> ({time.time()-t0:.1f}s)")
    _send(chat_id, reply)


def _poll_loop():
    offset = 0
    while True:
        try:
            updates = _call("getUpdates", {"offset": offset + 1, "timeout": 30}, timeout=40)
            for u in updates:
                offset = max(offset, u.get("update_id", 0))
                try:
                    _handle_update(u)
                except Exception as e:
                    print(f"[tg] handler error: {e}")
        except Exception as e:
            print(f"[tg] poll error: {e}")
            time.sleep(5)


def start():
    global _thread
    if not _config_token():
        return False
    if _thread and _thread.is_alive():
        return True
    _thread = threading.Thread(target=_poll_loop, daemon=True, name="telegram-bot")
    _thread.start()
    print("[tg] bot started (long polling)")
    return True


def status():
    tok = bool(_config_token())
    chat = _owner_chat_id or _config_chat()
    running = bool(_thread and _thread.is_alive())
    return {"token_set": tok, "linked": bool(chat), "running": running}
