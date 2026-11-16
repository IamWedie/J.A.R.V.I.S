"""Per-session study service: scan documents and cache the extraction snapshot.

This module is the security boundary between the UI and the signed
``study_extract`` tool.  Extracted source text lives only inside the cached
:class:`ScanSnapshot`: events emitted from here carry metadata (paths relative
to Documents, character counts, skip reasons) and never
``payload["documents"][i]["text"]``.  Error events carry a stable code and a
short generic message, never exception text or broker content.

Generation consumes :attr:`ScanSnapshot.material` under
:data:`SCAN_TTL_SECONDS` and persists decks through :class:`StudyStore`;
every deck change is audited inside the store transaction, so a deck is
never stored without its audit entry.  Error events carry a stable code
and a short generic message, never exception text or model output.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import dataclass

from core.security.broker import ToolInvocation, ToolResult
from core.study.generation import (
    DEFAULT_CARDS,
    DEFAULT_QUESTIONS,
    StudyGenerationError,
    merge_results,
    sanitize_title,
    validate_request,
)
from core.study.paths import StudyPathError, documents_relative, normalize_study_path
from core.study.store import StudyStoreError

STUDY_RESULT_LIMIT = 200_000
SCAN_TTL_SECONDS = 15 * 60
SCAN_MAX_FILES = 20

_OP_ID_MAX_CHARS = 64
_GENERIC_PATH_FAILURE = "Invalid study path."
_GENERIC_REQUEST_FAILURE = "Invalid study request."
_GENERIC_SCAN_FAILURE = "Study scan failed."
_GENERIC_GENERATE_FAILURE = "Study generation failed."
_GENERIC_STORE_FAILURE = "Study storage failed."
_BUSY_MESSAGE = "Study is busy."
_SCAN_UNKNOWN = "Study scan not found."
_SCAN_EXPIRED = "Study scan expired."
_DECK_NOT_FOUND = "Study deck not found."
_CONFIRM_REQUIRED = "Study deck deletion requires confirmation."
_MODEL_UNAVAILABLE = "The study model is unavailable."
_MODEL_ERROR = "The study model failed."
_MODEL_OUTPUT = "The model returned unusable study material."
_AUDIT_UNAVAILABLE = "The study audit log is unavailable."

#: Codes :meth:`Brain.generate_study_material` is allowed to raise; anything
#: else from it is reported as ``study_model_error``.
_BRAIN_CODES = frozenset(
    {"study_model_unavailable", "study_model_error", "study_invalid_output"}
)
_BRAIN_MESSAGES = {
    "study_model_unavailable": _MODEL_UNAVAILABLE,
    "study_model_error": _MODEL_ERROR,
    "study_invalid_output": _MODEL_OUTPUT,
}
_STORE_MESSAGES = {
    "study_db_error": _GENERIC_STORE_FAILURE,
    "study_audit_unavailable": _AUDIT_UNAVAILABLE,
    "study_invalid_request": _GENERIC_REQUEST_FAILURE,
    "study_not_found": _DECK_NOT_FOUND,
}


class StudyServiceError(Exception):
    """Raised for every service failure; ``code`` is stable."""

    code: str

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class ScanSnapshot:
    """One cached extraction result.  ``payload`` holds the source text."""

    scan_id: str
    session_id: str
    path: str
    payload: dict
    scanned_at: float

    @property
    def material(self) -> str:
        return "\n\n".join(document["text"] for document in self.payload["documents"])


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_document(document) -> bool:
    if not isinstance(document, dict):
        return False
    if not isinstance(document.get("path"), str):
        return False
    if not isinstance(document.get("format"), str):
        return False
    chars = document.get("chars")
    if not _is_int(chars) or chars < 0:
        return False
    if not isinstance(document.get("text"), str):
        return False
    pages = document.get("pages")
    return pages is None or _is_int(pages)


def _valid_payload(payload) -> bool:
    """Shape check for the signed ``study_extract`` payload.

    Unknown keys are ignored; only the documented fields are required.
    """
    if not isinstance(payload, dict):
        return False
    if not isinstance(payload.get("root"), str):
        return False
    documents = payload.get("documents")
    if not isinstance(documents, list):
        return False
    if not isinstance(payload.get("skipped"), list):
        return False
    total_chars = payload.get("total_chars")
    if not _is_int(total_chars) or total_chars < 0:
        return False
    if not isinstance(payload.get("truncated"), bool):
        return False
    if not isinstance(payload.get("limits"), dict):
        return False
    return all(_valid_document(document) for document in documents)


class StudyService:
    """Owns scan tasks and the per-session extraction cache."""

    def __init__(
        self,
        *,
        broker,
        store,
        emit,
        brain=None,
        audit=None,
        documents_root: str | None = None,
        clock=time.monotonic,
    ) -> None:
        self._broker = broker
        self._store = store
        self._emit = emit
        self._brain = brain
        self._audit = audit
        self._documents_root = documents_root
        self._clock = clock
        self._tasks: dict[str, asyncio.Task] = {}
        self._scans: dict[str, ScanSnapshot] = {}

    # -- public surface ----------------------------------------------------

    def busy(self, session_id: str) -> bool:
        if not isinstance(session_id, str):
            return False
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

    def cached_scan_id(self, session_id: str) -> str | None:
        if not isinstance(session_id, str):
            return None
        snapshot = self._scans.get(session_id)
        return snapshot.scan_id if snapshot is not None else None

    async def start_scan(self, session_id: str, msg: dict) -> None:
        message = msg if isinstance(msg, dict) else {}
        op_id = self._op_id(message)
        if self.busy(session_id):
            await self._send(self._error(op_id, "scan", "study_busy", _BUSY_MESSAGE))
            return

        if not isinstance(session_id, str) or not session_id:
            await self._send(
                self._error(op_id, "scan", "study_invalid_request", _GENERIC_REQUEST_FAILURE)
            )
            return

        raw_path = message.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            await self._send(
                self._error(op_id, "scan", "study_path_invalid", _GENERIC_PATH_FAILURE)
            )
            return

        recursive = message.get("recursive", True)
        if not isinstance(recursive, bool):
            await self._send(
                self._error(op_id, "scan", "study_invalid_request", _GENERIC_REQUEST_FAILURE)
            )
            return

        try:
            absolute = normalize_study_path(raw_path, documents_root=self._documents_root)
        except StudyPathError as exc:
            await self._send(self._error(op_id, "scan", exc.code, _GENERIC_PATH_FAILURE))
            return

        self._track(session_id, self._run_scan(session_id, op_id, absolute, recursive))

    async def start_generate(self, session_id: str, msg: dict) -> None:
        message = msg if isinstance(msg, dict) else {}
        op_id = self._op_id(message)
        if self.busy(session_id):
            await self._send(
                self._error(op_id, "generate", "study_busy", _BUSY_MESSAGE)
            )
            return

        if not isinstance(session_id, str) or not session_id:
            await self._send(
                self._error(
                    op_id, "generate", "study_invalid_request", _GENERIC_REQUEST_FAILURE
                )
            )
            return

        # The scan id is resolved inside the tracked task: the event loop only
        # validates the session and acknowledges the request.
        self._track(session_id, self._run_generate(session_id, op_id, dict(message)))

    async def list_decks(self, session_id: str, msg: dict) -> None:
        message = msg if isinstance(msg, dict) else {}
        op_id = self._op_id(message)
        try:
            decks = self._store.list_decks()
        except StudyStoreError as exc:
            failure = self._store_failure(exc)
            await self._send(self._error(op_id, "list", failure.code, str(failure)))
            return
        await self._send({"type": "study_list_result", "op_id": op_id, "decks": decks})

    async def get_deck(self, session_id: str, msg: dict) -> None:
        message = msg if isinstance(msg, dict) else {}
        op_id = self._op_id(message)
        deck_id = message.get("deck_id")
        deck = None
        if isinstance(deck_id, str) and deck_id:
            try:
                deck = self._store.get_deck(deck_id)
            except StudyStoreError as exc:
                failure = self._store_failure(exc)
                await self._send(self._error(op_id, "get", failure.code, str(failure)))
                return
        if deck is None:
            await self._send(
                self._error(op_id, "get", "study_not_found", _DECK_NOT_FOUND)
            )
            return
        await self._send({"type": "study_deck", "op_id": op_id, "deck": deck})

    async def delete_deck(self, session_id: str, msg: dict) -> None:
        message = msg if isinstance(msg, dict) else {}
        op_id = self._op_id(message)
        if message.get("confirm") is not True:
            await self._send(
                self._error(
                    op_id, "delete", "study_confirm_required", _CONFIRM_REQUIRED
                )
            )
            return

        deck_id = message.get("deck_id")
        if not isinstance(deck_id, str) or not deck_id:
            await self._send(
                self._error(op_id, "delete", "study_not_found", _DECK_NOT_FOUND)
            )
            return

        if self._audit is None:
            await self._send(
                self._error(
                    op_id, "delete", "study_audit_unavailable", _AUDIT_UNAVAILABLE
                )
            )
            return

        def record(deck_id: str) -> None:
            self._audit.append(
                "study.deck_deleted",
                session_id=session_id,
                source="ws",
                result_status="ok",
                args={"deck_id": deck_id},
            )

        try:
            deleted = self._store.delete_deck(deck_id, before_commit=record)
        except StudyStoreError as exc:
            failure = self._store_failure(exc)
            await self._send(self._error(op_id, "delete", failure.code, str(failure)))
            return
        if not deleted:
            await self._send(
                self._error(op_id, "delete", "study_not_found", _DECK_NOT_FOUND)
            )
            return
        await self._send({"type": "study_deleted", "op_id": op_id, "deck_id": deck_id})

    async def cancel_all(self) -> None:
        for session_id in list(self._tasks):
            await self.cancel(session_id)

    async def cancel(self, session_id: str) -> None:
        self._scans.pop(session_id, None)
        task = self._tasks.pop(session_id, None)
        if task is None:
            return
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def wait_for_idle(self, session_id: str) -> None:
        task = self._tasks.get(session_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # -- scan task ---------------------------------------------------------

    async def _run_scan(
        self, session_id: str, op_id: str, absolute: str, recursive: bool
    ) -> None:
        try:
            await self._send({"type": "study_status", "op_id": op_id, "stage": "extracting"})
            result = await self._broker.invoke(
                ToolInvocation(
                    tool="study_extract",
                    args={
                        "path": absolute,
                        "recursive": recursive,
                        "max_files": SCAN_MAX_FILES,
                    },
                    session_id=session_id,
                    source="ui",
                ),
                result_limit=STUDY_RESULT_LIMIT,
            )
            payload = self._checked_payload(result)
            if not payload["documents"]:
                code = "study_no_supported_files" if payload["skipped"] else "study_empty_text"
                raise StudyServiceError(code, "No study text was found.")
            snapshot = ScanSnapshot(
                scan_id=uuid.uuid4().hex,
                session_id=session_id,
                path=absolute,
                payload=payload,
                scanned_at=self._clock(),
            )
            self._scans[session_id] = snapshot
            await self._send(self._preview(op_id, snapshot))
        except asyncio.CancelledError:
            raise
        except StudyServiceError as exc:
            await self._send(self._error(op_id, "scan", exc.code, str(exc)))
        except Exception:
            await self._send(
                self._error(op_id, "scan", "study_extract_failed", _GENERIC_SCAN_FAILURE)
            )

    def _checked_payload(self, result) -> dict:
        if not isinstance(result, ToolResult) or not result.ok:
            error_code = getattr(result, "error_code", None)
            code = (
                "study_audit_unavailable"
                if error_code == "audit_unavailable"
                else "study_extract_failed"
            )
            raise StudyServiceError(code, "Study extraction failed.")
        if not isinstance(result.content, str):
            raise StudyServiceError("study_extract_failed", "Study extraction failed.")
        try:
            payload = json.loads(result.content)
        except (TypeError, ValueError):
            raise StudyServiceError("study_extract_failed", "Study extraction failed.") from None
        if not _valid_payload(payload):
            raise StudyServiceError("study_extract_failed", "Study extraction failed.")
        return payload

    def _preview(self, op_id: str, snapshot: ScanSnapshot) -> dict:
        payload = snapshot.payload
        return {
            "type": "study_scan_result",
            "op_id": op_id,
            "scan_id": snapshot.scan_id,
            "root": documents_relative(snapshot.path, documents_root=self._documents_root),
            "documents": [
                {
                    "path": document["path"],
                    "format": document["format"],
                    "chars": document["chars"],
                    "pages": document["pages"],
                }
                for document in payload["documents"]
            ],
            "skipped": payload["skipped"],
            "total_chars": payload["total_chars"],
            "truncated": payload["truncated"],
            "limits": payload["limits"],
            "scanned_at": snapshot.scanned_at,
        }

    # -- generate task -----------------------------------------------------

    async def _run_generate(
        self, session_id: str, op_id: str, msg: dict
    ) -> None:
        try:
            await self._send(
                {"type": "study_status", "op_id": op_id, "stage": "generating"}
            )
            snapshot = self._require_snapshot(session_id, msg.get("scan_id"))
            mode, cards, questions = self._generate_request(msg)
            brain = self._brain
            if brain is None:
                raise StudyServiceError("study_model_unavailable", _MODEL_UNAVAILABLE)
            # The brain re-validates and zeroes the sections its mode does not
            # need, so it receives the user's raw counts; the service only
            # uses validate_request to reject an invalid request up front.
            raw = await self._brain_result(
                brain, snapshot.material, mode, cards, questions
            )
            root = snapshot.payload.get("root")
            try:
                merged = merge_results([raw], mode, cards, questions, root)
            except StudyGenerationError as exc:
                code = (
                    exc.code if exc.code == "study_invalid_request"
                    else "study_invalid_output"
                )
                message = (
                    _GENERIC_REQUEST_FAILURE
                    if code == "study_invalid_request"
                    else _MODEL_OUTPUT
                )
                raise StudyServiceError(code, message) from None

            title = self._deck_title(merged, msg.get("title"), root)
            payload = snapshot.payload
            source_path = documents_relative(
                snapshot.path, documents_root=self._documents_root
            )
            summary = {
                "files": len(payload["documents"]),
                "formats": sorted(
                    {document["format"] for document in payload["documents"]}
                ),
                "total_chars": payload["total_chars"],
                "truncated": payload["truncated"],
                "skipped_count": len(payload["skipped"]),
                "scanned_at": snapshot.scanned_at,
            }

            deck_cards = merged["cards"]
            quiz = merged["quiz"]

            def record(deck_id: str) -> None:
                self._audit.append(
                    "study.deck_saved",
                    session_id=session_id,
                    source="ws",
                    result_status="ok",
                    args={
                        "deck_id": deck_id,
                        "cards": len(deck_cards),
                        "questions": len(quiz),
                    },
                )

            try:
                deck_id = self._store.save_deck(
                    title=title,
                    source_path=source_path,
                    source_summary=summary,
                    mode=mode,
                    cards=deck_cards,
                    quiz=quiz,
                    # The store runs the callback inside its transaction and
                    # converts any failure there into a rolled-back
                    # ``study_audit_unavailable``; without an audit log no
                    # callback runs (the brief's own deck tests exercise the
                    # audit-less path).
                    before_commit=record if self._audit is not None else None,
                )
            except StudyStoreError as exc:
                raise self._store_failure(exc) from None

            await self._send(
                {
                    "type": "study_generate_result",
                    "op_id": op_id,
                    "scan_id": snapshot.scan_id,
                    "deck": {
                        "id": deck_id,
                        "title": title,
                        "source_path": source_path,
                        "mode": mode,
                        "card_count": len(deck_cards),
                        "question_count": len(quiz),
                        "truncated": summary["truncated"],
                    },
                }
            )
        except asyncio.CancelledError:
            raise
        except StudyServiceError as exc:
            await self._send(self._error(op_id, "generate", exc.code, str(exc)))
        except Exception:
            await self._send(
                self._error(
                    op_id, "generate", "study_db_error", _GENERIC_GENERATE_FAILURE
                )
            )

    def _require_snapshot(self, session_id: str, scan_id) -> ScanSnapshot:
        """The session's cached scan, when it exists, is owned, and is fresh."""
        snapshot = self._scans.get(session_id) if isinstance(session_id, str) else None
        if (
            snapshot is None
            or not isinstance(scan_id, str)
            or snapshot.scan_id != scan_id
        ):
            raise StudyServiceError("study_scan_unknown", _SCAN_UNKNOWN)
        if self._clock() - snapshot.scanned_at > SCAN_TTL_SECONDS:
            self._scans.pop(session_id, None)
            raise StudyServiceError("study_scan_expired", _SCAN_EXPIRED)
        return snapshot

    @staticmethod
    def _generate_request(msg: dict) -> tuple[str, int, int]:
        title = msg.get("title")
        if title is not None and not isinstance(title, str):
            raise StudyServiceError("study_invalid_request", _GENERIC_REQUEST_FAILURE)
        try:
            validate_request(
                msg.get("mode", "flashcards"),
                msg.get("cards", DEFAULT_CARDS),
                msg.get("questions", DEFAULT_QUESTIONS),
            )
        except StudyGenerationError:
            raise StudyServiceError(
                "study_invalid_request", _GENERIC_REQUEST_FAILURE
            ) from None
        return (
            msg.get("mode", "flashcards"),
            msg.get("cards", DEFAULT_CARDS),
            msg.get("questions", DEFAULT_QUESTIONS),
        )

    @staticmethod
    async def _brain_result(brain, material, mode, cards, questions):
        try:
            return await brain.generate_study_material(
                material, mode, cards, questions
            )
        except asyncio.CancelledError:
            raise
        except StudyGenerationError as exc:
            code = exc.code if exc.code in _BRAIN_CODES else "study_model_error"
            raise StudyServiceError(
                code, _BRAIN_MESSAGES.get(code, _MODEL_ERROR)
            ) from None
        except Exception:
            raise StudyServiceError("study_model_error", _MODEL_ERROR) from None

    @staticmethod
    def _deck_title(merged: dict, requested, root) -> str:
        title = ""
        if isinstance(requested, str) and requested.strip():
            title = sanitize_title(requested, "")
        if title:
            return title
        if merged["title"] != "Study deck":
            return merged["title"]
        return sanitize_title(root, "Study deck")

    @staticmethod
    def _store_failure(exc: StudyStoreError) -> StudyServiceError:
        """Map a store error to the service error, keeping its stable code."""
        return StudyServiceError(
            exc.code, _STORE_MESSAGES.get(exc.code, _GENERIC_STORE_FAILURE)
        )

    # -- helpers -----------------------------------------------------------

    def _op_id(self, msg: dict) -> str:
        value = msg.get("op_id") if isinstance(msg, dict) else None
        if isinstance(value, str) and 0 < len(value) <= _OP_ID_MAX_CHARS:
            return value
        return uuid.uuid4().hex

    def _track(self, session_id: str, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._tasks[session_id] = task
        task.add_done_callback(lambda done: self._forget(session_id, done))
        return task

    def _forget(self, session_id: str, task: asyncio.Task) -> None:
        if self._tasks.get(session_id) is task:
            del self._tasks[session_id]

    async def _send(self, event: dict) -> None:
        try:
            await self._emit(event)
        except Exception:
            pass

    @staticmethod
    def _error(op_id: str, stage: str, code: str, message: str) -> dict:
        return {
            "type": "study_error",
            "op_id": op_id,
            "stage": stage,
            "code": code,
            "message": message,
        }
