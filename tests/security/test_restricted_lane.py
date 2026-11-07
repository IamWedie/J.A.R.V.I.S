import json
import multiprocessing
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from core.security.winjob import (
    JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION,
    JobLimits,
    JobObject,
    JobObjectExtendedLimitInformation,
)
from core.security.lanes import LaneError, LaneTimeout, RestrictedLane
from core.security.worker import MAX_MESSAGE_BYTES, sanitized_env, worker_main


def test_sanitized_env_excludes_secrets(monkeypatch):
    monkeypatch.setenv("ZEN_API_KEY", "secret-zen")
    monkeypatch.setenv("JARVIS_PIN", "secret-pin")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-tg")
    monkeypatch.setenv("LICENSE_KEY", "secret-lic")
    monkeypatch.setenv("JARVIS_DATA_DIR", "C:\\secret-data")

    env = sanitized_env()
    assert env.get("JARVIS_WORKER") == "1"
    assert "SystemRoot" in env
    assert "PATH" in env
    assert "TEMP" in env
    assert "TMP" in env
    for key in ("ZEN_API_KEY", "JARVIS_PIN", "TELEGRAM_BOT_TOKEN", "LICENSE_KEY", "JARVIS_DATA_DIR"):
        assert key not in env

    env2 = sanitized_env(extra={"ZEN_API_KEY": "x"})
    assert "ZEN_API_KEY" not in env2

    env3 = sanitized_env(extra={"TEMP": "x"})
    assert env3["TEMP"] == "x"

    env4 = sanitized_env(extra={"JARVIS_WORKER": "0"})
    assert env4["JARVIS_WORKER"] == "1"

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", "import core.config; print(core.config.WORKER_MODE)"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "True"
    assert result.stderr.strip() == ""


@pytest.mark.asyncio
async def test_restricted_lane_runs_tool(tmp_path):
    lane = RestrictedLane(str(tmp_path))
    try:
        result = await lane.call("tests.security.fixtures.worker_tools:echo", {"value": 7})
        assert result == 7
    finally:
        lane.close()


@pytest.mark.asyncio
async def test_restricted_lane_timeout_kills_and_restarts(tmp_path):
    lane = RestrictedLane(str(tmp_path))
    try:
        with pytest.raises(LaneTimeout):
            await lane.call("tests.security.fixtures.worker_tools:sleep", {"seconds": 2}, timeout=0.2)
        result = await lane.call("tests.security.fixtures.worker_tools:echo", {"value": 5})
        assert result == 5
    finally:
        lane.close()


@pytest.mark.asyncio
async def test_restricted_lane_crash_returns_error(tmp_path):
    lane = RestrictedLane(str(tmp_path))
    try:
        with pytest.raises(LaneError):
            await lane.call("tests.security.fixtures.worker_tools:boom", {})
        result = await lane.call("tests.security.fixtures.worker_tools:echo", {"value": 9})
        assert result == 9
    finally:
        lane.close()


@pytest.mark.asyncio
async def test_worker_nonce_mismatch_is_rejected(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=True)
    nonce = secrets.token_hex(16)
    proc = ctx.Process(target=worker_main, args=(child_conn, nonce, str(tmp_path)), daemon=True)
    marker = tmp_path / "marker.txt"
    try:
        proc.start()
        # close parent's copy of child conn
        child_conn.close()
        # read ready
        assert parent_conn.poll(5)
        ready_data = parent_conn.recv_bytes(MAX_MESSAGE_BYTES)
        ready = json.loads(ready_data.decode("utf-8"))
        assert ready.get("op") == "ready"
        assert ready.get("nonce") == nonce
        # send wrong-nonce call for write_marker
        wrong_payload = {
            "nonce": "0" * 32,
            "op": "call",
            "handler": "tests.security.fixtures.worker_tools:write_marker",
            "args": {"path": str(marker)},
        }
        encoded = json.dumps(wrong_payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
        parent_conn.send_bytes(encoded)
        # worker closes pipe without executing handler -> EOF/OSError
        assert parent_conn.poll(5)
        with pytest.raises((EOFError, OSError)):
            parent_conn.recv_bytes(MAX_MESSAGE_BYTES)
        assert not marker.exists()
        proc.join(timeout=5)
        assert proc.exitcode == 0
    finally:
        try:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)
        except Exception:
            pass
        try:
            parent_conn.close()
        except Exception:
            pass
        try:
            child_conn.close()
        except Exception:
            pass


@pytest.mark.windows_only
def test_job_object_sets_memory_limit():
    if os.name != "nt":
        pytest.skip("Windows only")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    job = None
    try:
        job = JobObject(JobLimits(memory_bytes=256 * 1024 * 1024, active_processes=1, die_on_unhandled_exception=True))
        job.assign(child.pid)
        import ctypes

        kernel32 = ctypes.windll.kernel32
        info = JobObjectExtendedLimitInformation()
        result = kernel32.QueryInformationJobObject(
            job.handle,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        )
        assert result != 0
        assert info.ProcessMemoryLimit == 256 * 1024 * 1024
        assert info.BasicLimitInformation.ActiveProcessLimit == 1
        assert info.BasicLimitInformation.LimitFlags & JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    finally:
        if job is not None:
            job.close()
        try:
            child.terminate()
            child.wait(timeout=5)
        except Exception:
            try:
                child.kill()
            except Exception:
                pass
