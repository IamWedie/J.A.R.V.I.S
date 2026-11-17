import asyncio
import json

import pytest

from core.security.audit import AuditLog
from core.security.broker import ToolResult
from core.study.generation import StudyGenerationError
from core.study.service import StudyService
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
    def __init__(self, result=None):
        self.result = result if result is not None else ToolResult(
            True, json.dumps(extraction_payload()), None
        )
        self.calls = []

    async def invoke(self, invocation, *, result_limit=8000):
        self.calls.append((invocation, result_limit))
        return self.result


class FakeBrain:
    def __init__(self, result=None, error=None, gate=None):
        self.result = result if result is not None else {
            "title": "Biology",
            "cards": [{"front": "F1", "back": "B1"}],
            "quiz": [],
        }
        self.error = error
        self.gate = gate
        self.calls = []

    async def generate_study_material(self, material, mode, card_count, question_count):
        self.calls.append((material, mode, card_count, question_count))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.result


class FailingAudit:
    def __init__(self, event_type):
        self.event_type = event_type

    def append(self, event_type, **kwargs):
        if event_type == self.event_type:
            raise RuntimeError("audit down")


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value


@pytest.fixture
def docs(tmp_path):
    root = tmp_path / "Documents"
    root.mkdir()
    (root / "Study").mkdir()
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


def make_service(docs, store, emit, brain=None, audit=None, clock=None, broker=None):
    return StudyService(
        broker=broker or FakeBroker(),
        store=store,
        emit=emit,
        brain=brain,
        audit=audit,
        documents_root=str(docs),
        clock=clock or (lambda: 1000.0),
    )


async def do_scan(service, events, op_id="scan"):
    await service.start_scan("s1", {"path": "Study", "op_id": op_id})
    await service.wait_for_idle("s1")
    return [event for event in events if event["type"] == "study_scan_result"][-1]["scan_id"]


@pytest.mark.asyncio
async def test_generate_saves_and_audits_without_source_text(docs, store, emitted, tmp_path):
    emit, events = emitted
    audit = AuditLog(tmp_path / "audit.db", hmac_key=b"0" * 32)
    brain = FakeBrain()
    service = make_service(docs, store, emit, brain=brain, audit=audit)
    scan_id = await do_scan(service, events)
    events.clear()

    await service.start_generate(
        "s1",
        {
            "scan_id": scan_id,
            "mode": "flashcards",
            "cards": 5,
            "questions": 3,
            "op_id": "gen-1",
        },
    )
    await service.wait_for_idle("s1")

    assert [event["type"] for event in events] == ["study_status", "study_generate_result"]
    assert events[0] == {"type": "study_status", "op_id": "gen-1", "stage": "generating"}
    deck = events[1]["deck"]
    assert deck["title"] == "Biology"
    assert deck["source_path"] == "Study"
    assert deck["mode"] == "flashcards"
    assert deck["card_count"] == 1
    assert deck["question_count"] == 0
    assert deck["truncated"] is False
    assert brain.calls[0] == (SENTINEL, "flashcards", 5, 3)

    stored = store.get_deck(deck["id"])
    assert stored["cards"] == [{"front": "F1", "back": "B1"}]
    assert SENTINEL not in json.dumps(events)
    assert SENTINEL not in json.dumps(stored["source_summary"])
    assert SENTINEL not in json.dumps(audit.recent(50))
    assert audit.recent(50)[-1]["event_type"] == "study.deck_saved"
    audit.close()


@pytest.mark.asyncio
async def test_unknown_and_expired_scans_are_rejected(docs, store, emitted):
    emit, events = emitted
    clock = Clock()
    service = make_service(docs, store, emit, brain=FakeBrain(), clock=clock)

    await service.start_generate("s1", {"scan_id": "missing", "mode": "flashcards"})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_scan_unknown"

    scan_id = await do_scan(service, events)
    clock.value += 901
    await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards"})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_scan_expired"
    assert service.cached_scan_id("s1") is None


@pytest.mark.asyncio
async def test_new_scan_replaces_the_old_scan_id(docs, store, emitted):
    emit, events = emitted
    service = make_service(docs, store, emit, brain=FakeBrain())
    first = await do_scan(service, events, "first")
    second = await do_scan(service, events, "second")
    assert first != second
    await service.start_generate("s1", {"scan_id": first, "mode": "flashcards"})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_scan_unknown"


@pytest.mark.asyncio
async def test_scan_ids_are_session_owned(docs, store, emitted):
    emit, events = emitted
    service = make_service(docs, store, emit, brain=FakeBrain())
    scan_id = await do_scan(service, events)
    await service.start_generate("s2", {"scan_id": scan_id, "mode": "flashcards"})
    await service.wait_for_idle("s2")
    assert events[-1]["code"] == "study_scan_unknown"


@pytest.mark.asyncio
async def test_model_failures_do_not_save_a_deck(docs, store, emitted):
    emit, events = emitted
    for error, expected in (
        (StudyGenerationError("study_model_error", "boom"), "study_model_error"),
        (StudyGenerationError("study_invalid_output", "bad"), "study_invalid_output"),
        (StudyGenerationError("study_invalid_request", "bad"), "study_invalid_request"),
    ):
        service = make_service(docs, store, emit, brain=FakeBrain(error=error))
        scan_id = await do_scan(service, events)
        await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards"})
        await service.wait_for_idle("s1")
        assert events[-1]["code"] == expected
        assert store.list_decks() == []


@pytest.mark.asyncio
async def test_joined_material_is_capped_to_the_generation_budget(
    docs, store, emitted, tmp_path
):
    emit, events = emitted
    texts = ["A" * 18_000 for _ in range(5)]
    payload = {
        "root": "Study\\Biology",
        "documents": [
            {
                "path": "part-%d.txt" % index,
                "format": "text",
                "chars": len(text),
                "pages": None,
                "text": text,
            }
            for index, text in enumerate(texts)
        ],
        "skipped": [],
        "total_chars": sum(len(text) for text in texts),
        "truncated": False,
        "limits": {"max_files": 20, "max_total_chars": 90000},
    }
    audit = AuditLog(tmp_path / "audit.db", hmac_key=b"0" * 32)
    brain = FakeBrain()
    service = make_service(
        docs,
        store,
        emit,
        brain=brain,
        audit=audit,
        broker=FakeBroker(ToolResult(True, json.dumps(payload), None)),
    )
    scan_id = await do_scan(service, events)
    await service.start_generate(
        "s1", {"scan_id": scan_id, "mode": "flashcards"}
    )
    await service.wait_for_idle("s1")
    assert events[-1]["type"] == "study_generate_result"
    assert len(brain.calls[0][0]) == 90_000
    deck = store.get_deck(events[-1]["deck"]["id"])
    assert deck["source_summary"]["truncated"] is True
    audit.close()


@pytest.mark.asyncio
async def test_missing_brain_and_invalid_result_are_reported(docs, store, emitted):
    emit, events = emitted
    service = make_service(docs, store, emit, brain=None)
    scan_id = await do_scan(service, events)
    await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards"})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_model_unavailable"

    brain = FakeBrain(result={"title": "T", "cards": [], "quiz": []})
    service = make_service(docs, store, emit, brain=brain)
    scan_id = await do_scan(service, events, "scan-2")
    await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards"})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_invalid_output"
    assert store.list_decks() == []


@pytest.mark.asyncio
async def test_generate_validates_mode_counts_and_title(docs, store, emitted):
    emit, events = emitted
    service = make_service(docs, store, emit, brain=FakeBrain())
    scan_id = await do_scan(service, events)

    await service.start_generate("s1", {"scan_id": scan_id, "mode": "cards"})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_invalid_request"

    await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards", "cards": 4})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_invalid_request"

    await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards", "title": 42})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_invalid_request"
    assert store.list_decks() == []


@pytest.mark.asyncio
async def test_title_precedence(docs, store, emitted, tmp_path):
    emit, events = emitted
    audit = AuditLog(tmp_path / "audit.db", hmac_key=b"0" * 32)
    brain = FakeBrain(result={"title": "Model title", "cards": [{"front": "F", "back": "B"}], "quiz": []})
    service = make_service(docs, store, emit, brain=brain, audit=audit)
    scan_id = await do_scan(service, events)

    await service.start_generate(
        "s1", {"scan_id": scan_id, "mode": "flashcards", "title": "  My Deck  "}
    )
    await service.wait_for_idle("s1")
    assert events[-1]["deck"]["title"] == "My Deck"

    await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards"})
    await service.wait_for_idle("s1")
    assert events[-1]["deck"]["title"] == "Model title"

    generic = FakeBrain(result={"title": "Study deck", "cards": [{"front": "F", "back": "B"}], "quiz": []})
    service = make_service(docs, store, emit, brain=generic, audit=audit)
    scan_id = await do_scan(service, events, "scan-2")
    await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards"})
    await service.wait_for_idle("s1")
    assert events[-1]["deck"]["title"] == "Study\\Biology"
    audit.close()


@pytest.mark.asyncio
async def test_save_audit_failure_rolls_back(docs, store, emitted):
    emit, events = emitted
    service = make_service(
        docs, store, emit, brain=FakeBrain(), audit=FailingAudit("study.deck_saved")
    )
    scan_id = await do_scan(service, events)
    await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards"})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_audit_unavailable"
    assert store.list_decks() == []


@pytest.mark.asyncio
async def test_save_without_audit_fails_closed(docs, store, emitted):
    emit, events = emitted
    service = make_service(docs, store, emit, brain=FakeBrain(), audit=None)
    scan_id = await do_scan(service, events)
    await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards"})
    await service.wait_for_idle("s1")
    assert events[-1]["code"] == "study_audit_unavailable"
    assert store.list_decks() == []


@pytest.mark.asyncio
async def test_list_get_delete_and_confirm(docs, store, emitted, tmp_path):
    emit, events = emitted
    audit = AuditLog(tmp_path / "audit.db", hmac_key=b"0" * 32)
    service = make_service(docs, store, emit, brain=FakeBrain(), audit=audit)
    scan_id = await do_scan(service, events)
    await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards"})
    await service.wait_for_idle("s1")
    deck_id = events[-1]["deck"]["id"]

    events.clear()
    await service.list_decks("s1", {"op_id": "list-1"})
    assert events[-1]["type"] == "study_list_result"
    assert events[-1]["decks"][0]["id"] == deck_id

    await service.get_deck("s1", {"deck_id": deck_id, "op_id": "get-1"})
    assert events[-1]["type"] == "study_deck"
    assert events[-1]["deck"]["cards"] == [{"front": "F1", "back": "B1"}]

    await service.delete_deck("s1", {"deck_id": deck_id, "op_id": "del-1"})
    assert events[-1]["code"] == "study_confirm_required"
    assert store.get_deck(deck_id) is not None

    await service.delete_deck(
        "s1", {"deck_id": deck_id, "confirm": True, "op_id": "del-2"}
    )
    assert events[-1] == {"type": "study_deleted", "op_id": "del-2", "deck_id": deck_id}
    assert store.get_deck(deck_id) is None
    assert audit.recent(50)[-1]["event_type"] == "study.deck_deleted"

    await service.get_deck("s1", {"deck_id": deck_id, "op_id": "get-2"})
    assert events[-1]["code"] == "study_not_found"
    audit.close()


@pytest.mark.asyncio
async def test_delete_audit_failure_keeps_the_deck(docs, store, emitted):
    emit, events = emitted
    audit = FailingAudit("study.deck_deleted")
    service = make_service(docs, store, emit, brain=FakeBrain(), audit=audit)
    scan_id = await do_scan(service, events)
    await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards"})
    await service.wait_for_idle("s1")
    deck_id = events[-1]["deck"]["id"]

    await service.delete_deck("s1", {"deck_id": deck_id, "confirm": True})
    assert events[-1]["code"] == "study_audit_unavailable"
    assert store.get_deck(deck_id) is not None


@pytest.mark.asyncio
async def test_cancel_during_generation_leaves_no_deck(docs, store, emitted):
    emit, events = emitted
    gate = asyncio.Event()
    brain = FakeBrain(gate=gate)
    service = make_service(docs, store, emit, brain=brain)
    scan_id = await do_scan(service, events)
    events.clear()

    await service.start_generate("s1", {"scan_id": scan_id, "mode": "flashcards"})
    await asyncio.sleep(0)
    await service.cancel("s1")
    gate.set()

    assert store.list_decks() == []
    assert service.cached_scan_id("s1") is None
    assert not any(event["type"] == "study_generate_result" for event in events)
