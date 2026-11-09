"""Restricted worker entry point.

This module must not import core.config at import time: the worker sets
JARVIS_WORKER=1 in its environment before any handler (which may import
core.config) is imported.
"""

import hmac
import importlib
import json
import os

MAX_MESSAGE_BYTES = 4 * 1024 * 1024

_ALLOWED_ENV_KEYS = (
    "SystemRoot",
    "WINDIR",
    "PATH",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "COMSPEC",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)


def sanitized_env(extra: dict | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _ALLOWED_ENV_KEYS:
        value = os.environ.get(key)
        if isinstance(value, str):
            env[key] = value
    if extra:
        for key, value in extra.items():
            if key == "JARVIS_WORKER":
                continue
            if key not in _ALLOWED_ENV_KEYS:
                continue
            if not isinstance(value, str):
                continue
            env[key] = value
    env["JARVIS_WORKER"] = "1"
    return env


def _encode(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _is_valid_handler(handler: object) -> bool:
    if not isinstance(handler, str):
        return False
    if handler.count(":") != 1:
        return False
    module, func = handler.split(":", 1)
    if not module or not func:
        return False
    if not func.isidentifier():
        return False
    for part in module.split("."):
        if not part or not part.isidentifier():
            return False
    return True


def _send(conn, payload: dict) -> None:
    encoded = _encode(payload)
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError("message_too_large")
    conn.send_bytes(encoded)


def worker_main(conn, nonce: str, sandbox_dir: str) -> None:
    clean_env = sanitized_env()
    os.environ.clear()
    os.environ.update(clean_env)
    os.chdir(sandbox_dir)

    try:
        _send(conn, {"nonce": nonce, "op": "ready"})
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return

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
            try:
                conn.close()
            except Exception:
                pass
            return

        if not isinstance(msg, dict):
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

        if msg.get("op") != "call":
            try:
                _send(conn, {"nonce": nonce, "op": "result", "ok": False, "error": "invalid_request"})
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            return

        handler = msg.get("handler")
        args = msg.get("args")

        if not _is_valid_handler(handler) or not isinstance(args, dict):
            try:
                _send(conn, {"nonce": nonce, "op": "result", "ok": False, "error": "invalid_request"})
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            return

        for k in args.keys():
            if not isinstance(k, str):
                try:
                    _send(conn, {"nonce": nonce, "op": "result", "ok": False, "error": "invalid_request"})
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                return

        try:
            json.dumps(args, ensure_ascii=True, allow_nan=False)
        except Exception:
            try:
                _send(conn, {"nonce": nonce, "op": "result", "ok": False, "error": "invalid_request"})
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            return

        module_name, func_name = handler.split(":", 1)
        try:
            module = importlib.import_module(module_name)
            fn = getattr(module, func_name)
            if not callable(fn):
                raise TypeError("not callable")
        except Exception:
            try:
                _send(conn, {"nonce": nonce, "op": "result", "ok": False, "error": "invalid_request"})
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            return

        try:
            value = fn(**args)
        except Exception:
            try:
                _send(conn, {"nonce": nonce, "op": "result", "ok": False, "error": "handler_error"})
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            return

        try:
            encoded = _encode({"nonce": nonce, "op": "result", "ok": True, "value": value})
        except Exception:
            try:
                _send(conn, {"nonce": nonce, "op": "result", "ok": False, "error": "unserializable_result"})
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            return

        if len(encoded) > MAX_MESSAGE_BYTES:
            try:
                _send(conn, {"nonce": nonce, "op": "result", "ok": False, "error": "unserializable_result"})
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            return

        try:
            conn.send_bytes(encoded)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            return
