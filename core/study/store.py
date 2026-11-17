"""SQLite storage for generated study decks.

The study database is separate from the audit database: deleting every deck
never touches the audit trail.  Only metadata is stored here -- deck titles,
relative source paths, ``source_summary`` (JSON metadata, never source text),
cards, and quiz questions.

Every operation opens its own connection with ``PRAGMA foreign_keys = ON``
and owns its transaction boundaries explicitly (``isolation_level=None`` plus
``BEGIN IMMEDIATE``).
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

import core.config as config

SCHEMA_VERSION = 1

MODES: tuple[str, ...] = ("flashcards", "quiz", "both")

MAX_TITLE = 120
MAX_BODY = 2000
MAX_OPTION = 300
MAX_SOURCE_PATH = 500
MAX_CARDS = 40
MAX_QUESTIONS = 20
OPTION_COUNT = 4

_SCHEMA: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS decks (
        id             TEXT PRIMARY KEY,
        title          TEXT NOT NULL,
        source_path    TEXT NOT NULL,
        source_summary TEXT NOT NULL,
        mode           TEXT NOT NULL,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS cards (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        deck_id  TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        front    TEXT NOT NULL,
        back     TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS quiz_questions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        deck_id      TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
        position     INTEGER NOT NULL,
        question     TEXT NOT NULL,
        options      TEXT NOT NULL,
        answer_index INTEGER NOT NULL,
        explanation  TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cards_deck ON cards(deck_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_quiz_deck ON quiz_questions(deck_id, position)",
)

_DECK_COLUMNS = (
    "d.id",
    "d.title",
    "d.source_path",
    "d.source_summary",
    "d.mode",
    "d.created_at",
    "d.updated_at",
)


def study_db_path() -> str:
    """Location of the study deck database inside the data directory."""
    return os.path.join(config.data_dir(), "study.db")


class StudyStoreError(Exception):
    """Raised for every study store failure; ``code`` is a stable identifier."""

    def __init__(self, code: str = "study_db_error", message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


# -- validation ------------------------------------------------------------


def _invalid() -> StudyStoreError:
    return StudyStoreError("study_invalid_request", "Invalid study deck request.")


def _text(value, limit: int) -> str:
    """Return *value* stripped, or raise when it is not a bounded non-empty string."""
    if not isinstance(value, str):
        raise _invalid()
    text = value.strip()
    if not text or len(text) > limit:
        raise _invalid()
    return text


def _summary_json(value) -> str:
    """Validate that *value* is a JSON-serializable dict and return its JSON form."""
    if not isinstance(value, dict):
        raise _invalid()
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        raise _invalid() from None


def _normalize_cards(cards) -> list[tuple[str, str]]:
    if not isinstance(cards, list) or len(cards) > MAX_CARDS:
        raise _invalid()
    normalized = []
    for card in cards:
        if not isinstance(card, dict):
            raise _invalid()
        normalized.append(
            (
                _text(card.get("front"), MAX_BODY),
                _text(card.get("back"), MAX_BODY),
            )
        )
    return normalized


def _normalize_quiz(quiz) -> list[dict]:
    if not isinstance(quiz, list) or len(quiz) > MAX_QUESTIONS:
        raise _invalid()
    normalized = []
    for item in quiz:
        if not isinstance(item, dict):
            raise _invalid()
        options = item.get("options")
        if not isinstance(options, list) or len(options) != OPTION_COUNT:
            raise _invalid()
        answer_index = item.get("answer_index")
        if (
            isinstance(answer_index, bool)
            or not isinstance(answer_index, int)
            or not 0 <= answer_index < OPTION_COUNT
        ):
            raise _invalid()
        normalized.append(
            {
                "question": _text(item.get("question"), MAX_BODY),
                "options": [_text(option, MAX_OPTION) for option in options],
                "answer_index": answer_index,
                "explanation": _text(item.get("explanation"), MAX_BODY),
            }
        )
    return normalized


def _normalize_request(
    *,
    title,
    source_path,
    source_summary,
    mode,
    cards,
    quiz,
) -> dict:
    """Validate and normalize a full deck request before any database write."""
    if not isinstance(mode, str) or mode not in MODES:
        raise _invalid()
    normalized_cards = _normalize_cards(cards)
    normalized_quiz = _normalize_quiz(quiz)
    wants_cards = mode in ("flashcards", "both")
    wants_quiz = mode in ("quiz", "both")
    if wants_cards != bool(normalized_cards) or wants_quiz != bool(normalized_quiz):
        raise _invalid()
    return {
        "title": _text(title, MAX_TITLE),
        "source_path": _text(source_path, MAX_SOURCE_PATH),
        "source_summary": _summary_json(source_summary),
        "mode": mode,
        "cards": normalized_cards,
        "quiz": normalized_quiz,
    }


# -- json reading ----------------------------------------------------------


def _corrupt() -> StudyStoreError:
    return StudyStoreError("study_db_error", "Corrupt study deck data.")


def _parse_summary(raw) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise _corrupt() from None
    if not isinstance(value, dict):
        raise _corrupt()
    return value


def _parse_options(raw) -> list:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise _corrupt() from None
    if not isinstance(value, list):
        raise _corrupt()
    return value


# -- connection helpers ----------------------------------------------------


def _rollback(conn) -> None:
    if conn is None:
        return
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _close(conn) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except sqlite3.Error:
        pass


def _utc_now() -> str:
    """Current UTC time as ISO-8601 with second precision and a ``Z`` suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StudyStore:
    """Persist, list, load, and delete study decks."""

    def __init__(self, db_path: str | os.PathLike) -> None:
        self._path = os.fspath(db_path)
        self._init_schema()

    # -- setup -------------------------------------------------------------

    def _init_schema(self) -> None:
        conn = None
        try:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA:
                conn.execute(statement)
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif row["version"] != SCHEMA_VERSION:
                raise StudyStoreError(
                    "study_db_error", "Unsupported study database version."
                )
            conn.execute("COMMIT")
        except BaseException as exc:
            _rollback(conn)
            if isinstance(exc, StudyStoreError):
                raise
            if isinstance(exc, Exception):
                raise StudyStoreError(
                    "study_db_error", "Study database unavailable."
                ) from None
            raise
        finally:
            _close(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
        except BaseException:
            _close(conn)
            raise
        return conn

    # -- writing -----------------------------------------------------------

    def save_deck(
        self,
        *,
        title: str,
        source_path: str,
        source_summary: dict,
        mode: str,
        cards: list[dict],
        quiz: list[dict],
        before_commit=None,
    ) -> str:
        """Insert one deck with all cards and questions; return the new deck id.

        Validation happens before the write transaction opens, so an invalid
        deck never touches the database.  ``before_commit(deck_id)`` runs
        inside the transaction after every write and before ``COMMIT``; if it
        raises, the change is rolled back and ``study_audit_unavailable`` is
        raised.
        """
        request = _normalize_request(
            title=title,
            source_path=source_path,
            source_summary=source_summary,
            mode=mode,
            cards=cards,
            quiz=quiz,
        )
        deck_id = uuid.uuid4().hex
        now = _utc_now()
        conn = None
        try:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO decks"
                " (id, title, source_path, source_summary, mode, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    deck_id,
                    request["title"],
                    request["source_path"],
                    request["source_summary"],
                    request["mode"],
                    now,
                    now,
                ),
            )
            if request["cards"]:
                conn.executemany(
                    "INSERT INTO cards (deck_id, position, front, back)"
                    " VALUES (?, ?, ?, ?)",
                    [
                        (deck_id, position, front, back)
                        for position, (front, back) in enumerate(request["cards"])
                    ],
                )
            if request["quiz"]:
                conn.executemany(
                    "INSERT INTO quiz_questions"
                    " (deck_id, position, question, options, answer_index, explanation)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            deck_id,
                            position,
                            item["question"],
                            json.dumps(item["options"]),
                            item["answer_index"],
                            item["explanation"],
                        )
                        for position, item in enumerate(request["quiz"])
                    ],
                )
            self._audit(before_commit, deck_id)
            conn.execute("COMMIT")
            return deck_id
        except BaseException as exc:
            _rollback(conn)
            if isinstance(exc, StudyStoreError):
                raise
            if isinstance(exc, Exception):
                raise StudyStoreError(
                    "study_db_error", "Could not save the study deck."
                ) from None
            raise
        finally:
            _close(conn)

    def delete_deck(self, deck_id: str, before_commit=None) -> bool:
        """Delete a deck and its children; True when a deck was removed.

        ``before_commit(deck_id)`` runs inside the transaction after the delete
        and before ``COMMIT``; if it raises, the delete is rolled back and
        ``study_audit_unavailable`` is raised.
        """
        if not isinstance(deck_id, str) or not deck_id:
            return False
        conn = None
        try:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM decks WHERE id = ?", (deck_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return False
            conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
            self._audit(before_commit, deck_id)
            conn.execute("COMMIT")
            return True
        except BaseException as exc:
            _rollback(conn)
            if isinstance(exc, StudyStoreError):
                raise
            if isinstance(exc, Exception):
                raise StudyStoreError(
                    "study_db_error", "Could not delete the study deck."
                ) from None
            raise
        finally:
            _close(conn)

    def _audit(self, before_commit, deck_id: str) -> None:
        """Run the audit callback inside the open transaction, fail closed."""
        if before_commit is None:
            return
        try:
            before_commit(deck_id)
        except Exception:
            raise StudyStoreError(
                "study_audit_unavailable", "The deck change could not be audited."
            ) from None

    # -- reading -----------------------------------------------------------

    def list_decks(self) -> list[dict]:
        """Newest first; metadata plus counts, never card or question bodies."""
        conn = None
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT " + ", ".join(_DECK_COLUMNS) + ","
                " (SELECT COUNT(*) FROM cards AS c WHERE c.deck_id = d.id)"
                " AS card_count,"
                " (SELECT COUNT(*) FROM quiz_questions AS q WHERE q.deck_id = d.id)"
                " AS question_count"
                " FROM decks AS d"
                " ORDER BY d.created_at DESC, d.rowid DESC"
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "source_path": row["source_path"],
                    "source_summary": _parse_summary(row["source_summary"]),
                    "mode": row["mode"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "card_count": row["card_count"],
                    "question_count": row["question_count"],
                }
                for row in rows
            ]
        except BaseException as exc:
            if isinstance(exc, StudyStoreError):
                raise
            if isinstance(exc, Exception):
                raise StudyStoreError(
                    "study_db_error", "Could not read study decks."
                ) from None
            raise
        finally:
            _close(conn)

    def get_deck(self, deck_id: str) -> dict | None:
        """Return one deck with its cards and quiz, or ``None`` when unknown."""
        if not isinstance(deck_id, str) or not deck_id:
            return None
        conn = None
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT " + ", ".join(_DECK_COLUMNS) + " FROM decks AS d WHERE d.id = ?",
                (deck_id,),
            ).fetchone()
            if row is None:
                return None
            cards = conn.execute(
                "SELECT front, back FROM cards WHERE deck_id = ? ORDER BY position",
                (deck_id,),
            ).fetchall()
            questions = conn.execute(
                "SELECT question, options, answer_index, explanation"
                " FROM quiz_questions WHERE deck_id = ? ORDER BY position",
                (deck_id,),
            ).fetchall()
            return {
                "id": row["id"],
                "title": row["title"],
                "source_path": row["source_path"],
                "source_summary": _parse_summary(row["source_summary"]),
                "mode": row["mode"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "cards": [
                    {"front": card["front"], "back": card["back"]} for card in cards
                ],
                "quiz": [
                    {
                        "question": item["question"],
                        "options": _parse_options(item["options"]),
                        "answer_index": item["answer_index"],
                        "explanation": item["explanation"],
                    }
                    for item in questions
                ],
            }
        except BaseException as exc:
            if isinstance(exc, StudyStoreError):
                raise
            if isinstance(exc, Exception):
                raise StudyStoreError(
                    "study_db_error", "Could not read the study deck."
                ) from None
            raise
        finally:
            _close(conn)

    def close(self) -> None:
        """No-op: every operation owns and closes its own connection."""
