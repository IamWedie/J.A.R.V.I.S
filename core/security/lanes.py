"""Restricted lane: job-limited worker process managed per call.

Stable error tokens include lane_closed, invalid_handler,
invalid_args, job_assign_failed, worker_ready_timeout,
worker_nonce_mismatch, message_too_large, protocol_error,
worker_error, handler_error, invalid_request, and
unserializable_result. LaneTimeout ("lane_timeout") subclasses
LaneError for timeouts.
"""

import asyncio
import hmac
import json
import math
import multiprocessing
import os
import secrets
import time

from core.security.winjob import JobLimits, JobObject
from core.security.worker import MAX_MESSAGE_BYTES, worker_main


class LaneError(Exception):
    pass


class LaneTimeout(LaneError):
    pass


class _TimeoutSignal(Exception):
    pass


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


def _validate_timeout_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid timeout")
    f = float(value)
    if not math.isfinite(f):
        raise ValueError("invalid timeout")
    if not (0 < f <= 300):
        raise ValueError("invalid timeout")
    return f


class RestrictedLane:
    def __init__(self, sandbox_dir: str, timeout_default: float = 10.0) -> None:
        if os.name != "nt":
            raise OSError("RestrictedLane requires Windows Job Objects")
        if not isinstance(sandbox_dir, str) or not sandbox_dir:
            raise ValueError("sandbox_dir must be a non-empty str")
        resolved = os.path.abspath(sandbox_dir)
        if not os.path.isdir(resolved):
            raise ValueError("sandbox_dir does not exist")
        self._sandbox_dir = resolved
        self._timeout_default = _validate_timeout_value(timeout_default)
        self._proc = None
        self._conn = None
        self._job = None
        self._nonce: str | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def call(self, handler: str, args: dict, timeout: float | None = None) -> object:
        if self._closed:
            raise LaneError("lane_closed")
        if not _is_valid_handler(handler):
            raise LaneError("invalid_handler")
        if not isinstance(args, dict):
            raise LaneError("invalid_args")
        for k in args.keys():
            if not isinstance(k, str):
                raise LaneError("invalid_args")
        try:
            json.dumps(args, ensure_ascii=True, allow_nan=False)
        except Exception:
            raise LaneError("invalid_args") from None

        if timeout is None:
            effective_timeout = self._timeout_default
        else:
            effective_timeout = _validate_timeout_value(timeout)

        async with self._lock:
            if self._closed:
                raise LaneError("lane_closed")
            if self._proc is None or self._conn is None or not self._proc.is_alive():
                # ensure no stale dead worker left
                if self._proc is not None or self._conn is not None or self._job is not None:
                    await asyncio.to_thread(self._reset_worker_sync)
                await asyncio.to_thread(self._ensure_worker_sync)
            try:
                return await asyncio.to_thread(
                    self._exchange_sync, handler, args, effective_timeout
                )
            except _TimeoutSignal:
                await asyncio.to_thread(self._reset_worker_sync)
                raise LaneTimeout("lane_timeout") from None
            except LaneError:
                await asyncio.to_thread(self._reset_worker_sync)
                raise
            except (EOFError, OSError):
                await asyncio.to_thread(self._reset_worker_sync)
                raise LaneError("protocol_error") from None

    def _ensure_worker_sync(self) -> None:
        nonce = secrets.token_hex(16)
        ctx = multiprocessing.get_context("spawn")
        parent_conn = None
        child_conn = None
        proc = None
        job = None
        try:
            parent_conn, child_conn = ctx.Pipe(duplex=True)
            proc = ctx.Process(target=worker_main, args=(child_conn, nonce, self._sandbox_dir), daemon=True)
            try:
                proc.start()
            finally:
                try:
                    child_conn.close()
                except Exception:
                    pass
                child_conn = None

            if os.name == "nt":
                job = JobObject(JobLimits(memory_bytes=256 * 1024 * 1024, active_processes=1, die_on_unhandled_exception=True))
                try:
                    job.assign(proc.pid)
                except Exception:
                    # cleanup job and process
                    try:
                        if proc.is_alive():
                            proc.terminate()
                            proc.join(timeout=5)
                    except Exception:
                        pass
                    try:
                        job.close()
                    except Exception:
                        pass
                    try:
                        parent_conn.close()
                    except Exception:
                        pass
                    parent_conn = None
                    raise LaneError("job_assign_failed") from None

            # wait for ready with 5s deadline via poll
            if not parent_conn.poll(5):
                try:
                    if proc.is_alive():
                        proc.terminate()
                        proc.join(timeout=5)
                except Exception:
                    pass
                if job is not None:
                    try:
                        job.close()
                    except Exception:
                        pass
                try:
                    parent_conn.close()
                except Exception:
                    pass
                raise LaneError("worker_ready_timeout") from None

            try:
                data = parent_conn.recv_bytes(MAX_MESSAGE_BYTES)
            except ValueError:
                try:
                    if proc.is_alive():
                        proc.terminate()
                        proc.join(timeout=5)
                except Exception:
                    pass
                if job is not None:
                    try:
                        job.close()
                    except Exception:
                        pass
                try:
                    parent_conn.close()
                except Exception:
                    pass
                raise LaneError("message_too_large") from None
            except (EOFError, OSError):
                try:
                    if proc.is_alive():
                        proc.terminate()
                        proc.join(timeout=5)
                except Exception:
                    pass
                if job is not None:
                    try:
                        job.close()
                    except Exception:
                        pass
                try:
                    parent_conn.close()
                except Exception:
                    pass
                raise LaneError("protocol_error") from None

            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                try:
                    if proc.is_alive():
                        proc.terminate()
                        proc.join(timeout=5)
                except Exception:
                    pass
                if job is not None:
                    try:
                        job.close()
                    except Exception:
                        pass
                try:
                    parent_conn.close()
                except Exception:
                    pass
                raise LaneError("protocol_error") from None

            if not isinstance(msg, dict):
                try:
                    if proc.is_alive():
                        proc.terminate()
                        proc.join(timeout=5)
                except Exception:
                    pass
                if job is not None:
                    try:
                        job.close()
                    except Exception:
                        pass
                try:
                    parent_conn.close()
                except Exception:
                    pass
                raise LaneError("protocol_error") from None

            incoming_nonce = msg.get("nonce", "")
            if not isinstance(incoming_nonce, str) or not hmac.compare_digest(incoming_nonce, nonce):
                try:
                    if proc.is_alive():
                        proc.terminate()
                        proc.join(timeout=5)
                except Exception:
                    pass
                if job is not None:
                    try:
                        job.close()
                    except Exception:
                        pass
                try:
                    parent_conn.close()
                except Exception:
                    pass
                raise LaneError("worker_nonce_mismatch") from None

            if msg.get("op") != "ready":
                try:
                    if proc.is_alive():
                        proc.terminate()
                        proc.join(timeout=5)
                except Exception:
                    pass
                if job is not None:
                    try:
                        job.close()
                    except Exception:
                        pass
                try:
                    parent_conn.close()
                except Exception:
                    pass
                raise LaneError("protocol_error") from None

            self._proc = proc
            self._conn = parent_conn
            self._job = job
            self._nonce = nonce
        except LaneError:
            raise
        except Exception as exc:
            # Cleanup on unexpected failure during spawn
            try:
                if proc is not None and proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=5)
            except Exception:
                pass
            if job is not None:
                try:
                    job.close()
                except Exception:
                    pass
            if parent_conn is not None:
                try:
                    parent_conn.close()
                except Exception:
                    pass
            if child_conn is not None:
                try:
                    child_conn.close()
                except Exception:
                    pass
            # If already a LaneError, preserve; otherwise protocol error
            if isinstance(exc, LaneError):
                raise
            raise LaneError("protocol_error") from None

    def _exchange_sync(self, handler: str, args: dict, timeout_value: float) -> object:
        conn = self._conn
        nonce = self._nonce
        if conn is None or nonce is None:
            raise LaneError("protocol_error")
        payload = {"nonce": nonce, "op": "call", "handler": handler, "args": args}
        try:
            encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except Exception:
            raise LaneError("invalid_args") from None
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise LaneError("message_too_large") from None
        try:
            conn.send_bytes(encoded)
        except (EOFError, OSError, ValueError):
            raise LaneError("protocol_error") from None

        deadline = time.monotonic() + timeout_value
        data = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _TimeoutSignal()
            if not conn.poll(remaining):
                raise _TimeoutSignal()
            try:
                data = conn.recv_bytes(MAX_MESSAGE_BYTES)
            except (EOFError, OSError) as exc:
                raise LaneError("protocol_error") from exc
            except ValueError:
                raise LaneError("message_too_large") from None
            break

        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception:
            raise LaneError("protocol_error") from None

        if not isinstance(msg, dict):
            raise LaneError("protocol_error")

        incoming_nonce = msg.get("nonce", "")
        if not isinstance(incoming_nonce, str) or not hmac.compare_digest(incoming_nonce, nonce):
            raise LaneError("worker_nonce_mismatch")

        if msg.get("op") != "result":
            raise LaneError("protocol_error")

        ok = msg.get("ok")
        if ok is True:
            if "value" not in msg:
                raise LaneError("protocol_error")
            return msg["value"]
        if ok is False:
            err = msg.get("error")
            if err in ("handler_error", "invalid_request", "unserializable_result"):
                raise LaneError(err)
            raise LaneError("worker_error")
        raise LaneError("protocol_error")

    def _reset_worker_sync(self) -> None:
        proc = self._proc
        conn = self._conn
        job = self._job
        self._proc = None
        self._conn = None
        self._job = None
        self._nonce = None
        if proc is not None:
            try:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=5)
                    if proc.is_alive():
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        proc.join(timeout=5)
                else:
                    proc.join(timeout=1)
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if job is not None:
            try:
                job.close()
            except Exception:
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._reset_worker_sync()
        except Exception:
            pass
