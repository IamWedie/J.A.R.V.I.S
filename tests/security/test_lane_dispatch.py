import asyncio
import json
import multiprocessing
import secrets
from types import SimpleNamespace

import pytest

from core.security.privileged_host import privileged_main
from core.security.lanes import LaneDispatcher, LaneError, PrivilegedLane, UnknownLane
from core.security.worker import MAX_MESSAGE_BYTES


class FakeLane:
    def __init__(self, label):
        self.label = label
        self.calls = []

    async def call(self, handler, args, timeout=None):
        self.calls.append((handler, args, timeout))
        return f"{self.label}:{handler}"

    def close(self):
        pass


def _make_lanes():
    return {
        "core": FakeLane("core"),
        "restricted": FakeLane("restricted"),
        "privileged": FakeLane("privileged"),
        "egress": FakeLane("egress"),
    }


def _spec(name, handler, lane):
    return SimpleNamespace(name=name, handler=handler, lane=lane, timeout_seconds=5.0)


@pytest.mark.asyncio
async def test_dispatcher_routes_core_lane():
    lanes = _make_lanes()
    dispatcher = LaneDispatcher(None, "ignored", lanes=lanes)
    spec = _spec("my_tool", "some.module:fn", "core")
    args = {"a": 1}
    result = await dispatcher.dispatch(spec, args)
    assert result == "core:my_tool"
    assert lanes["core"].calls == [("my_tool", args, 5.0)]
    for name, fake in lanes.items():
        if name != "core":
            assert fake.calls == []
    dispatcher.close()


@pytest.mark.asyncio
async def test_dispatcher_routes_restricted_lane():
    lanes = _make_lanes()
    dispatcher = LaneDispatcher(None, "ignored", lanes=lanes)
    spec = _spec("tool_a", "tests.security.fixtures.worker_tools:echo", "restricted")
    args = {"value": 1}
    result = await dispatcher.dispatch(spec, args)
    assert result == "restricted:tests.security.fixtures.worker_tools:echo"
    assert lanes["restricted"].calls == [("tests.security.fixtures.worker_tools:echo", args, 5.0)]
    for name, fake in lanes.items():
        if name != "restricted":
            assert fake.calls == []
    dispatcher.close()


@pytest.mark.asyncio
async def test_dispatcher_routes_privileged_lane():
    lanes = _make_lanes()
    dispatcher = LaneDispatcher(None, "ignored", lanes=lanes)
    spec = _spec("tool_b", "tests.security.fixtures.worker_tools:echo", "privileged")
    args = {"value": 2}
    result = await dispatcher.dispatch(spec, args)
    assert result == "privileged:tests.security.fixtures.worker_tools:echo"
    assert lanes["privileged"].calls == [("tests.security.fixtures.worker_tools:echo", args, 5.0)]
    for name, fake in lanes.items():
        if name != "privileged":
            assert fake.calls == []
    dispatcher.close()


@pytest.mark.asyncio
async def test_dispatcher_routes_egress_lane():
    lanes = _make_lanes()
    dispatcher = LaneDispatcher(None, "ignored", lanes=lanes)
    spec = _spec("tool_c", "tests.security.fixtures.worker_tools:echo", "egress")
    args = {"value": 3}
    result = await dispatcher.dispatch(spec, args)
    assert result == "egress:tests.security.fixtures.worker_tools:echo"
    assert lanes["egress"].calls == [("tests.security.fixtures.worker_tools:echo", args, 5.0)]
    for name, fake in lanes.items():
        if name != "egress":
            assert fake.calls == []
    dispatcher.close()


@pytest.mark.asyncio
async def test_unknown_lane_is_rejected():
    lanes = _make_lanes()
    dispatcher = LaneDispatcher(None, "ignored", lanes=lanes)
    with pytest.raises(UnknownLane):
        await dispatcher.dispatch(_spec("tool_d", "tests.security.fixtures.worker_tools:echo", "disabled"), {})
    with pytest.raises(UnknownLane):
        await dispatcher.dispatch(_spec("tool_e", "tests.security.fixtures.worker_tools:echo", "bogus_lane"), {})
    for fake in lanes.values():
        assert fake.calls == []
    dispatcher.close()


@pytest.mark.asyncio
async def test_privileged_lane_rejects_wrong_nonce(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=True)
    nonce = secrets.token_hex(16)
    proc = ctx.Process(target=privileged_main, args=(child_conn, nonce, str(tmp_path)), daemon=True)
    marker = tmp_path / "marker.txt"
    try:
        proc.start()
        child_conn.close()
        assert parent_conn.poll(5)
        ready_data = parent_conn.recv_bytes(MAX_MESSAGE_BYTES)
        ready = json.loads(ready_data.decode("utf-8"))
        assert ready.get("op") == "ready"
        assert ready.get("nonce") == nonce
        wrong_payload = {
            "nonce": "0" * 32,
            "op": "call",
            "handler": "tests.security.fixtures.worker_tools:write_marker",
            "args": {"path": str(marker)},
        }
        encoded = json.dumps(wrong_payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
        parent_conn.send_bytes(encoded)
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


@pytest.mark.asyncio
async def test_privileged_cancel_terminates_unresponsive_host():
    lane = PrivilegedLane()
    try:
        task = asyncio.create_task(
            lane.call("tests.security.fixtures.worker_tools:sleep", {"seconds": 30}, timeout=60)
        )
        for _ in range(500):
            if lane._busy:
                break
            await asyncio.sleep(0.01)
        assert lane._busy
        assert await lane.cancel_current() is True
        with pytest.raises(LaneError):
            await asyncio.wait_for(task, 10)
        result = await lane.call("tests.security.fixtures.worker_tools:echo", {"value": 3}, timeout=10)
        assert result == 3
    finally:
        lane.close()
