import json
import sqlite3

import pytest

from core.security.audit import AuditLog, EVENT_TYPES
from core.study.store import StudyStore, StudyStoreError, study_db_path

CARDS = [{"front": "Q1", "back": "A1"}]
QUIZ = [
    {
        "question": "Pick one",
        "options": ["a", "b", "c", "d"],
        "answer_index": 2,
        "explanation": "Because c.",
    }
]


def make_store(tmp_path):
    return StudyStore(tmp_path / "study.db")


def save(store, **overrides):
    values = {
        "title": "Biology",
        "source_path": "Study\\Biology",
        "source_summary": {"files": 2, "total_chars": 120, "truncated": False},
        "mode": "both",
        "cards": CARDS,
        "quiz": QUIZ,
    }
    values.update(overrides)
    return store.save_deck(**values)


def test_save_get_list_and_delete(tmp_path):
    store = make_store(tmp_path)
    deck_id = save(store)
    assert len(deck_id) == 32

    listed = store.list_decks()
    assert listed[0]["id"] == deck_id
    assert listed[0]["card_count"] == 1
    assert listed[0]["question_count"] == 1
    assert "cards" not in listed[0]
    assert "quiz" not in listed[0]

    full = store.get_deck(deck_id)
    assert full["title"] == "Biology"
    assert full["source_path"] == "Study\\Biology"
    assert full["source_summary"]["files"] == 2
    assert full["mode"] == "both"
    assert full["cards"] == CARDS
    assert full["quiz"] == QUIZ

    assert store.delete_deck(deck_id) is True
    assert store.get_deck(deck_id) is None
    assert store.delete_deck(deck_id) is False
    store.close()


def test_delete_cascades_cards_and_questions(tmp_path):
    store = make_store(tmp_path)
    deck_id = save(store)
    assert store.delete_deck(deck_id) is True
    conn = sqlite3.connect(tmp_path / "study.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM quiz_questions").fetchone()[0] == 0
    finally:
        conn.close()
    store.close()


def test_save_rolls_back_when_audit_callback_fails(tmp_path):
    store = make_store(tmp_path)

    def fail(deck_id):
        raise RuntimeError("audit down")

    with pytest.raises(StudyStoreError) as exc:
        save(store, before_commit=fail)
    assert exc.value.code == "study_audit_unavailable"
    assert store.list_decks() == []
    conn = sqlite3.connect(tmp_path / "study.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
    finally:
        conn.close()
    store.close()


def test_delete_rolls_back_when_audit_callback_fails(tmp_path):
    store = make_store(tmp_path)
    deck_id = save(store)

    def fail(value):
        raise RuntimeError("audit down")

    with pytest.raises(StudyStoreError) as exc:
        store.delete_deck(deck_id, before_commit=fail)
    assert exc.value.code == "study_audit_unavailable"
    assert store.get_deck(deck_id) is not None
    store.close()


def test_audit_callback_runs_before_commit_without_source_text(tmp_path):
    assert "study.deck_saved" in EVENT_TYPES
    assert "study.deck_deleted" in EVENT_TYPES
    audit = AuditLog(tmp_path / "audit.db", hmac_key=b"0" * 32)
    store = make_store(tmp_path)

    def record(deck_id):
        audit.append(
            "study.deck_saved",
            source="ws",
            result_status="ok",
            args={"deck_id": deck_id, "cards": 1, "questions": 1},
        )

    deck_id = save(store, before_commit=record)
    rows = audit.recent(10)
    assert rows[-1]["event_type"] == "study.deck_saved"
    raw = json.dumps(rows)
    assert deck_id in raw
    assert "Q1" not in raw and "A1" not in raw
    audit.close()
    store.close()


@pytest.mark.parametrize(
    "bad",
    [
        {"title": ""},
        {"title": "x" * 121},
        {"mode": "cards"},
        {"cards": []},
        {"cards": [{"front": "q", "back": "a" * 2001}]},
        {"cards": [{"front": "q", "back": "a"}] * 41},
        {"quiz": []},
        {"quiz": [{"question": "q", "options": ["a", "b", "c"], "answer_index": 0, "explanation": "e"}]},
        {"quiz": [{"question": "q", "options": ["a", "b", "c", "d"], "answer_index": 4, "explanation": "e"}]},
        {"quiz": [{"question": "q", "options": ["a", "b", "c", "d"], "answer_index": 0, "explanation": "e"}] * 21},
        {"source_summary": "not a dict"},
        {"source_path": ""},
    ],
)
def test_invalid_deck_is_rejected(tmp_path, bad):
    store = make_store(tmp_path)
    with pytest.raises(StudyStoreError) as exc:
        save(store, **bad)
    assert exc.value.code == "study_invalid_request"
    assert store.list_decks() == []
    store.close()


def test_corrupt_database_raises_clear_error(tmp_path):
    path = tmp_path / "study.db"
    path.write_bytes(b"not a sqlite database")
    with pytest.raises(StudyStoreError) as exc:
        StudyStore(path)
    assert exc.value.code == "study_db_error"


def test_study_db_path_uses_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert study_db_path() == str(tmp_path / "study.db")
