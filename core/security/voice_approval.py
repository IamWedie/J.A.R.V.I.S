"""Denial-first voice approval parsing and provider.

``parse_voice_decision`` is a pure, never-raising parser that only allows
the exact anchored phrases ``approve once`` and ``yes, approve`` (comma
optional). Denial markers are checked first with word-boundary matching so
a phrase like ``approve once but no`` can never allow. Everything else is
``DEFER`` so bare ``approve``, ``yes``, ``ok``/``sure``/``go``/``do it``,
longer sentences such as ``please approve once``, and non-string input
never grant anything.

``VoiceApprovalProvider`` binds that parser to live voice I/O: sensitive /
critical requests and unenrolled voices defer to the authenticated UI
before any audio work; otherwise it prompts, captures, verifies the
enrolled voice (before parsing), and only then parses. After capture any
ambiguity or failure is ``DENY``; only a speak failure (before capture)
defers to the UI. ``decide`` never raises and never audits transcripts,
audio, descriptions, or raw args.
"""
from __future__ import annotations

import asyncio
import re
import string
from enum import Enum


class VoiceResult(str, Enum):
    """Outcome of a voice approval attempt."""

    ALLOW = "allow"
    DENY = "deny"
    DEFER = "defer"


#: Exact, anchored allow phrases after normalization (comma optional).
_ALLOW_PHRASES = frozenset({"approve once", "yes, approve", "yes approve"})

#: Substring presence used only to pin the both-markers rule; denial wins.
_ALLOW_SUBSTRINGS = ("approve once", "yes, approve", "yes approve")

#: Denial markers, checked first with word-boundary matching.  ``\bno\b``
#: must not match ``not``/``nope``/``cannot``; ``not ok`` allows any
#: whitespace between the two words.  ``don't``/``dont`` are distinct tokens.
_DENIAL_RE = re.compile(
    r"\bdeny\b|\bdenied\b|\bcancel\b|\bstop\b|\bno\b|\bnever\b|\breject\b"
    r"|\bdon't\b|\bdont\b|\bnot\s+ok\b"
)

#: Curly quotes/apostrophes normalized before matching.
_CURLY_MAP = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace, normalize commas, strip edge punctuation."""
    normalized = text.translate(_CURLY_MAP).lower()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"\s*,\s*", ", ", normalized)
    normalized = normalized.strip(string.punctuation + " ")
    return normalized


def parse_voice_decision(text: str) -> VoiceResult:
    """Parse one transcribed utterance; never raises, never logs it.

    Non-``str`` input (including ``None``) is ``DEFER``. Denial is checked
    first: any denial marker yields ``DENY`` even when an allow phrase is
    also present. Only exact anchored equality with an allow phrase yields
    ``ALLOW``; everything else is ``DEFER``.
    """
    try:
        if not isinstance(text, str):
            return VoiceResult.DEFER
        normalized = _normalize(text)
        if not normalized:
            return VoiceResult.DEFER
        if _DENIAL_RE.search(normalized):
            return VoiceResult.DENY
        if normalized in _ALLOW_PHRASES:
            return VoiceResult.ALLOW
        return VoiceResult.DEFER
    except Exception:
        return VoiceResult.DEFER


class VoiceApprovalProvider:
    """Voice approval via prompt, enrolled-voice verify, then exact parsing.

    ``voiceid`` exposes a boolean ``enrolled`` property and a synchronous
    ``verify(audio) -> bool`` (invoked with ``asyncio.to_thread``).
    ``transcribe``, ``capture_audio``, and ``speak`` are async callables.
    ``audit`` is optional and used only for best-effort ``voice.rejected``
    rows that never carry the transcript, audio, description, or raw args.
    """

    def __init__(self, voiceid, transcribe, capture_audio, speak, audit=None):
        self._voiceid = voiceid
        self._transcribe = transcribe
        self._capture_audio = capture_audio
        self._speak = speak
        self._audit = audit

    def _audit_rejected(self, request, reason: str) -> None:
        """Best-effort ``voice.rejected`` row; never raises."""
        audit = self._audit
        if audit is None:
            return
        try:
            audit.append(
                "voice.rejected",
                session_id=getattr(request, "session_id", None),
                source="voice",
                tool=getattr(request, "tool", None),
                capabilities=getattr(request, "capabilities", ()),
                risk=getattr(request, "risk", None),
                request_id=getattr(request, "request_id", None),
                error_code=reason,
            )
        except Exception:
            pass

    async def decide(self, request) -> VoiceResult:
        """Return ``ALLOW``/``DENY``/``DEFER`` for *request*; never raises.

        Sensitive/critical risks and unenrolled voices defer to the UI
        before any audio work. Otherwise the provider prompts, captures,
        verifies the enrolled voice before parsing, and only allows an
        exact allow phrase from that verified voice. After capture any
        ambiguity or failure denies; a speak failure before capture defers
        to the UI.
        """
        try:
            if getattr(request, "risk", None) in ("sensitive", "critical"):
                return VoiceResult.DEFER

            try:
                enrolled = bool(self._voiceid.enrolled)
            except Exception:
                self._audit_rejected(request, "provider_error")
                return VoiceResult.DENY
            if not enrolled:
                return VoiceResult.DEFER

            prompt = f"I need approval: {request.description}. Say approve once or deny."
            try:
                await self._speak(prompt)
            except Exception:
                self._audit_rejected(request, "speak_error")
                return VoiceResult.DEFER

            try:
                audio = await self._capture_audio()
            except Exception:
                self._audit_rejected(request, "capture_error")
                return VoiceResult.DENY
            if audio is None:
                self._audit_rejected(request, "no_audio")
                return VoiceResult.DENY
            try:
                if len(audio) == 0:
                    self._audit_rejected(request, "no_audio")
                    return VoiceResult.DENY
            except Exception:
                pass  # Objects without ``len`` (e.g. identity markers) are present.

            try:
                verified = await asyncio.to_thread(self._voiceid.verify, audio)
            except Exception:
                self._audit_rejected(request, "verify_error")
                return VoiceResult.DENY
            if not verified:
                self._audit_rejected(request, "voice_mismatch")
                return VoiceResult.DENY

            try:
                transcript = await self._transcribe(audio)
            except Exception:
                self._audit_rejected(request, "transcribe_error")
                return VoiceResult.DENY
            if not isinstance(transcript, str) or not transcript.strip():
                self._audit_rejected(request, "empty_transcript")
                return VoiceResult.DENY

            parsed = parse_voice_decision(transcript)
            if parsed is VoiceResult.ALLOW:
                return VoiceResult.ALLOW
            if parsed is VoiceResult.DENY:
                return VoiceResult.DENY
            self._audit_rejected(request, "ambiguous")
            return VoiceResult.DENY
        except Exception:
            try:
                self._audit_rejected(request, "provider_error")
            except Exception:
                pass
            return VoiceResult.DENY
