import asyncio
import json

import pytest

from core.security.broker import ToolResult
from core.study.service import STUDY_RESULT_LIMIT, StudyService
from core.study.store import StudyStore

SENTINEL = "PRIVATE SOURCE TEXT"


def extraction_payload(text=SENTINEL):
    return {
        "root": "Study\\Biology",
        "documents": [
            {
                "path": "chapter.txt",
                "format": "text",
                "chars": len(text),
                "pages": None,
                "text": text,
            }
        ],
        "skipped": [],
        "total_chars": len(text),
        "truncated": False,
        "limits": {"max_files": 20, "max_total_chars": 90000},
    }


class FakeBroker:
    def __init__(self, result=None, error=None, gate=None):
        self.result = result if result is not None else ToolResult(
            True, json.dumps(extraction_payload()), None
        )
        self.error = error
        self.gate = gate
        self.calls = []

    async def invoke(self, invocation, *, result_limit=8000):
        self.calls.append((invocation, result_limit))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def docs(tmp_path):
    root = tmp_path / "Documents"
    root.mkdir()
    return root


@pytest.fixture
def store(tmp_path):
    value = StudyStore(tmp_path / "study.db")
    yield value
    value.close()


@pytest.fixture
def emitted():
    events = []

    async def emit(event):
        events.append(event)

    return emit, events


def make_service(docs, store, emit, broker=None):
    return StudyService(
        broker=broker or FakeBroker(),
        store=store,
        emit=emit,
        documents_root=str(docs),
        clock=lambda: 1000.0,
    )


@pytest.mark.asyncio
async def test_scan_emits_preview_without_source_text(docs, store, emitted):
    emit, events = emitted
    target = docs / "Study" / "Biology"
    target.mkdir(parents=True)
    broker = FakeBroker()
    service = make_service(docs, store, emit, broker)

    await service.start_scan("s1", {"path": "Study\\Biology", "op_id": "op-1"})
    await service.wait_for_idle("s1")

    assert [event["type"] for event in events] == ["study_status", "study_scan_result"]
    assert events[0] == {"type": "study_status", "op_id": "op-1", "stage": "extracting"}
    result = events[1]
    assert result["op_id"] == "op-1"
    assert len(result["scan_id"]) == 32
    assert result["root"] == "Study\\Biology"
    assert result["documents"] == [
        {"path": "chapter.txt", "format": "text", "chars": len(SENTINEL), "pages": None}
    ]
    assert result["total_chars"] == len(SENTINEL)
    assert result["truncated"] is False
    assert SENTINEL not in json.dumps(events)
    assert service.cached_scan_id("s1") == result["scan_id"]

    invocation, limit = broker.calls[0]
    assert invocation.tool == "study_extract"
    assert invocation.args == {
        "path": str(target),
        "recursive": True,
        "max_files": 20,
    }
    assert invocation.session_id == "s1"
    assert invocation.source == "ui"
    assert limit == STUDY_RESULT_LIMIT


@pytest.mark.asyncio
async def test_scan_can_be_non_recursive(docs, store, emitted):
    emit, events = emitted
    (docs / "Study").mkdir()
    broker = FakeBroker()
    service = make_service(docs, store, emit, broker)
    await service.start_scan("s1", {"path": "Study", "recursive": False})
    await service.wait_for_idle("s1")
    assert broker.calls[0][0].args["recursive"] is False


@pytest.mark.asyncio
async def test_outside_path_is_rejected_before_the_broker(docs, store, emitted):
    emit, events = emitted
    outside = docs.parent / "Secret"
    outside.mkdir()
    broker = FakeBroker()
    service = make_service(docs, store, emit, broker)
    await service.start_scan("s1", {"path": str(outside), "op_id": "op-1"})
    assert broker.calls == []
    assert events[-1]["type"] == "study_error"
    assert events[-1]["code"] == "study_path_outside"
    assert events[-1]["stage"] == "scan"


@pytest.mark.asyncio
async def test_missing_path_is_rejected(docs, store, emitted):
    emit, events = emitted
    service = make_service(docs, store, emit)
    await service.start_scan("s1", {"path": "   ", "op_id": "op-1"})
    assert events[-1]["code"] == "study_path_invalid"


@pytest.mark.asyncio
async def test_broker_audit_failure_maps_to_study_audit_unavailable(docs, store, emitted):
    emit, events = emitted
    (docs / "Study").mkdir()
    broker = FakeBroker(result=ToolResult(False, "blocked", "audit_unavailable"))
    service = make_service(docs, store, emit, broker)
    await service.start_scan("s1", {"path": "Study", "op_id": "op-1"})
    await service.wait_for_idle("s1")
    assert events[-1]["type"] == "study_error"
    assert events[-1]["code"] == "study_audit_unavailable"


@pytest.mark.asyncio
async def test_broker_exception_maps_to_study_extract_failed(docs, store, emitted):
    emit, events = emitted
    (docs / "Study").mkdir()
    broker = FakeBroker(error=RuntimeError("lane exploded"))
    service = make_service(docs, store, emit, broker)
    await service.start_scan("s1", {"path": "Study", "op_id": "op-1"})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_extract_failed"


@pytest.mark.asyncio
async def test_empty_and_unsupported_payloads_get_distinct_codes(docs, store, emitted):
    emit, events = emitted
    (docs / "Study").mkdir()

    empty = extraction_payload()
    empty["documents"] = []
    empty["total_chars"] = 0
    broker = FakeBroker(result=ToolResult(True, json.dumps(empty), None))
    service = make_service(docs, store, emit, broker)
    await service.start_scan("s1", {"path": "Study", "op_id": "empty"})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_empty_text"

    unsupported = extraction_payload()
    unsupported["documents"] = []
    unsupported["total_chars"] = 0
    unsupported["skipped"] = [{"path": "app.exe", "reason": "unsupported"}]
    broker = FakeBroker(result=ToolResult(True, json.dumps(unsupported), None))
    service = make_service(docs, store, emit, broker)
    await service.start_scan("s1", {"path": "Study", "op_id": "unsupported"})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_no_supported_files"


@pytest.mark.asyncio
async def test_malformed_payload_is_rejected(docs, store, emitted):
    emit, events = emitted
    (docs / "Study").mkdir()
    broker = FakeBroker(result=ToolResult(True, "not json", None))
    service = make_service(docs, store, emit, broker)
    await service.start_scan("s1", {"path": "Study", "op_id": "op-1"})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_extract_failed"
    assert service.cached_scan_id("s1") is None


@pytest.mark.asyncio
async def test_second_scan_while_busy_returns_study_busy(docs, store, emitted):
    emit, events = emitted
    (docs / "Study").mkdir()
    gate = asyncio.Event()
    broker = FakeBroker(gate=gate)
    service = make_service(docs, store, emit, broker)

    await service.start_scan("s1", {"path": "Study", "op_id": "first"})
    await service.start_scan("s1", {"path": "Study", "op_id": "second"})
    busy = [event for event in events if event["type"] == "study_error"]
    assert busy[-1]["code"] == "study_busy"
    assert busy[-1]["op_id"] == "second"

    gate.set()
    await service.wait_for_idle("s1")
    assert any(event["type"] == "study_scan_result" for event in events)


@pytest.mark.asyncio
async def test_cancel_drops_the_cache_and_result(docs, store, emitted):
    emit, events = emitted
    (docs / "Study").mkdir()
    gate = asyncio.Event()
    broker = FakeBroker(gate=gate)
    service = make_service(docs, store, emit, broker)

    await service.start_scan("s1", {"path": "Study", "op_id": "op-1"})
    await asyncio.sleep(0)
    await service.cancel("s1")

    assert service.busy("s1") is False
    assert service.cached_scan_id("s1") is None
    assert not any(event["type"] == "study_scan_result" for event in events)
    gate.set()


@pytest.mark.asyncio
async def test_op_id_is_generated_when_missing(docs, store, emitted):
    emit, events = emitted
    (docs / "Study").mkdir()
    service = make_service(docs, store, emit)
    await service.start_scan("s1", {"path": "Study"})
    await service.wait_for_idle("s1")
    assert len(events[-1]["op_id"]) == 32
