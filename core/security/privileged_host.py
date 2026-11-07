"""Privileged worker entry point.

Mirrors the restricted worker pipe protocol except that the handler
runs in a daemon thread so the main loop stays responsive to
``cancel`` while a handler is still running.
"""

import hmac
import importlib
import json
import os
import threading

from core.security.worker import MAX_MESSAGE_BYTES, _encode, _is_valid_handler, _send, sanitized_env


def privileged_main(conn, nonce: str, working_dir: str) -> None:
    clean_env = sanitized_env()
    os.environ.clear()
    os.environ.update(clean_env)
    os.chdir(working_dir)

    try:
        _send(conn, {"nonce": nonce, "op": "ready"})
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return

    send_lock = threading.Lock()
    state_lock = threading.Lock()
    state = {"busy": False}

    def _send_locked(payload: dict) -> None:
        with send_lock:
            _send(conn, payload)

    def _send_error(error: str) -> None:
        try:
            _send_locked({"nonce": nonce, "op": "result", "ok": False, "error": error})
        except Exception:
            pass

    def _handler_thread(handler: str, args: dict) -> None:
        try:
            module_name, func_name = handler.split(":", 1)
            try:
                module = importlib.import_module(module_name)
                fn = getattr(module, func_name)
                if not callable(fn):
                    raise TypeError("not callable")
            except Exception:
                _send_error("invalid_request")
                return
            try:
                value = fn(**args)
            except Exception:
                _send_error("handler_error")
                return
            payload = {"nonce": nonce, "op": "result", "ok": True, "value": value}
            try:
                encoded = _encode(payload)
            except Exception:
                _send_error("unserializable_result")
                return
            if len(encoded) > MAX_MESSAGE_BYTES:
                _send_error("unserializable_result")
                return
            try:
                with send_lock:
                    conn.send_bytes(encoded)
            except Exception:
                pass
        finally:
            with state_lock:
                state["busy"] = False

    while True:
        try:
            data = conn.recv_bytes(MAX_MESSAGE_BYTES)
        except (EOFError, OSError, ValueError):
            try:
                conn.close()
            except Exception:
                pass
            return

        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception:
            _send_error("invalid_request")
            try:
                conn.close()
            except Exception:
                pass
            return

        if not isinstance(msg, dict):
            _send_error("invalid_request")
            try:
                conn.close()
            except Exception:
                pass
            return

        incoming_nonce = msg.get("nonce", "")
        if not isinstance(incoming_nonce, str) or not isinstance(nonce, str):
            try:
                conn.close()
            except Exception:
                pass
            return
        if not hmac.compare_digest(incoming_nonce, nonce):
            try:
                conn.close()
            except Exception:
                pass
            return

        op = msg.get("op")

        if op == "cancel":
            try:
                _send_locked({"nonce": nonce, "op": "cancelled"})
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            return

        if op != "call":
            _send_error("invalid_request")
            try:
                conn.close()
            except Exception:
                pass
            return

        handler = msg.get("handler")
        args = msg.get("args")

        if not _is_valid_handler(handler) or not isinstance(args, dict):
            _send_error("invalid_request")
            try:
                conn.close()
            except Exception:
                pass
            return

        for k in args.keys():
            if not isinstance(k, str):
                _send_error("invalid_request")
                try:
                    conn.close()
                except Exception:
                    pass
                return

        try:
            json.dumps(args, ensure_ascii=True, allow_nan=False)
        except Exception:
            _send_error("invalid_request")
            try:
                conn.close()
            except Exception:
                pass
            return

        with state_lock:
            if state["busy"]:
                _send_error("invalid_request")
                try:
                    conn.close()
                except Exception:
                    pass
                return
            state["busy"] = True

        thread = threading.Thread(target=_handler_thread, args=(handler, args), daemon=True)
        thread.start()
