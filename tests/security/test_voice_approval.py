"""Task 13: denial-first voice approval parser and provider.

Parser tests exercise ``parse_voice_decision`` directly; provider tests
drive ``VoiceApprovalProvider.decide`` with async fakes and a real
``AuditLog`` in ``tmp_path`` (no mocks of the audit chain).  No network,
no sleeps, no real microphone/speaker/ONNX model: ``voiceid.verify`` runs
through ``asyncio.to_thread`` against a fake object, and ``transcribe`` /
``capture_audio`` / ``speak`` are async fakes that record their calls.

Exactly 7 tests, per the brief; every extra ruling clause is pinned as an
assertion inside one of them.
"""
import pytest

from core.security.approvals import ApprovalRequest
from core.security.audit import AuditLog
from core.security.voice_approval import (
    VoiceApprovalProvider,
    VoiceResult,
    parse_voice_decision,
)


@pytest.fixture
def audit(tmp_path):
    log = AuditLog(tmp_path / "audit.db", hmac_key=b"0" * 32)
    yield log
    log.close()


def make_request(risk="guarded", **overrides):
    """A real, frozen ``ApprovalRequest`` (source=voice, as the broker builds)."""
    fields = {
        "request_id": "req-voice-1",
        "session_id": "sess-voice-1",
        "source": "voice",
        "tool": "read_file",
        "capabilities": ("filesystem.read",),
        "risk": risk,
        "description": "read a file for the user",
        "args_redacted": "{}",
        "created_at": 1000.0,
        "expires_at": 1060.0,
        "can_grant": False,
        "grant_scope": None,
    }
    fields.update(overrides)
    return ApprovalRequest(**fields)


def voice_rows(log):
    return [row for row in log.recent(50) if row["event_type"] == "voice.rejected"]


class FakeVoiceID:
    """Duck-typed ``voiceid``: boolean ``enrolled``, sync ``verify(audio)``."""

    def __init__(self, enrolled=True, result=True):
        self.enrolled = enrolled
        self.result = result
        self.verified = []

    def verify(self, audio):
        self.verified.append(audio)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeSpeak:
    def __init__(self):
        self.calls = []
        self.raise_error = False

    async def __call__(self, text):
        self.calls.append(text)
        if self.raise_error:
            raise RuntimeError("speaker down")


class FakeCapture:
    def __init__(self, audio):
        self.audio = audio
        self.calls = 0
        self.raise_error = False

    async def __call__(self):
        self.calls += 1
        if self.raise_error:
            raise RuntimeError("mic down")
        return self.audio


class FakeTranscribe:
    def __init__(self, text="approve once"):
        self.text = text
        self.audios = []
        self.raise_error = False

    async def __call__(self, audio):
        self.audios.append(audio)
        if self.raise_error:
            raise RuntimeError("stt down")
        return self.text


def test_exact_allow_phrases():
    allowed = (
        "approve once",
        "yes, approve",
        "YES, APPROVE.",
        "  approve once!  ",
        "yes,approve",
        "yes approve",
    )
    for text in allowed:
        assert parse_voice_decision(text) is VoiceResult.ALLOW, text
    # A longer sentence is not an anchored allow phrase.
    assert parse_voice_decision("please approve once") is VoiceResult.DEFER


def test_denial_words_win_over_approval():
    denied = (
        "I don't approve",
        "no, don't do it",
        "not ok",
        "don't confirm",
        "deny",
        "cancel",
        "stop",
        "never",
        "reject",
        "approve once but no",
        "yes, approve but stop",
        "I don\u2019t approve",  # curly apostrophe normalized
    )
    for text in denied:
        assert parse_voice_decision(text) is VoiceResult.DENY, text
    # \bno\b must not match inside "not"/"nope"/"cannot": those stay DEFER.
    for text in ("not", "nope", "cannot"):
        assert parse_voice_decision(text) is VoiceResult.DEFER, text


def test_ambiguous_phrases_do_not_allow():
    ambiguous = ("approve", "yes", "ok", "sure", "go", "do it", "", None)
    for text in ambiguous:
        assert parse_voice_decision(text) is VoiceResult.DEFER, repr(text)
    # Non-str input must never raise.
    assert parse_voice_decision(123) is VoiceResult.DEFER
    assert parse_voice_decision(b"approve once") is VoiceResult.DEFER
    assert parse_voice_decision(object()) is VoiceResult.DEFER


@pytest.mark.asyncio
async def test_sensitive_and_critical_are_deferred_to_ui(audit):
    voice = FakeVoiceID(enrolled=True, result=True)
    speak = FakeSpeak()
    capture = FakeCapture(b"audio")
    transcribe = FakeTranscribe("approve once")
    provider = VoiceApprovalProvider(
        voice, transcribe, capture, speak, audit=audit
    )

    for risk in ("sensitive", "critical"):
        result = await provider.decide(make_request(risk=risk))
        assert result is VoiceResult.DEFER, risk

    # No speaking, capture, transcription, or verification before capture.
    assert speak.calls == []
    assert capture.calls == 0
    assert transcribe.audios == []
    assert voice.verified == []
    assert voice_rows(audit) == []


@pytest.mark.asyncio
async def test_unenrolled_voice_defers_to_ui(audit):
    voice = FakeVoiceID(enrolled=False, result=True)
    speak = FakeSpeak()
    capture = FakeCapture(b"audio")
    transcribe = FakeTranscribe("approve once")
    provider = VoiceApprovalProvider(
        voice, transcribe, capture, speak, audit=audit
    )

    result = await provider.decide(make_request(risk="guarded"))

    assert result is VoiceResult.DEFER
    assert speak.calls == []
    assert capture.calls == 0
    assert transcribe.audios == []
    assert voice.verified == []
    assert voice_rows(audit) == []


@pytest.mark.asyncio
async def test_voice_mismatch_denies(audit):
    voice = FakeVoiceID(enrolled=True, result=False)
    audio = b"captured-audio"
    speak = FakeSpeak()
    capture = FakeCapture(audio)
    transcribe = FakeTranscribe("approve once")
    provider = VoiceApprovalProvider(
        voice, transcribe, capture, speak, audit=audit
    )
    request = make_request()

    result = await provider.decide(request)

    # The transcript's allow phrase must not win: mismatch denies first.
    assert result is VoiceResult.DENY
    assert voice.verified == [audio]
    assert transcribe.audios == []  # words are never read for a stranger
    rows = voice_rows(audit)
    assert len(rows) == 1
    row = rows[0]
    assert row["error_code"] == "voice_mismatch"
    assert row["source"] == "voice"
    assert row["request_id"] == request.request_id
    assert row["session_id"] == request.session_id
    assert row["tool"] == request.tool
    assert row["risk"] == request.risk
    # No transcript, description, audio, or raw args anywhere in the row.
    blob = " ".join(str(value) for value in row.values() if value is not None)
    assert "approve once" not in blob
    assert request.description not in blob
    assert row["args_redacted"] is None
    assert row["args_hash"] is None


@pytest.mark.asyncio
async def test_enrolled_matching_voice_and_allow_phrase_allows(audit):
    voice = FakeVoiceID(enrolled=True, result=True)
    audio = object()  # identity-checked: same object to verify and transcribe
    speak = FakeSpeak()
    capture = FakeCapture(audio)
    transcribe = FakeTranscribe("approve once")
    provider = VoiceApprovalProvider(
        voice, transcribe, capture, speak, audit=audit
    )
    request = make_request()

    result = await provider.decide(request)

    assert result is VoiceResult.ALLOW
    assert speak.calls == [
        f"I need approval: {request.description}. Say approve once or deny."
    ]
    assert capture.calls == 1
    assert voice.verified == [audio]
    assert transcribe.audios == [audio]

    # After capture, ambiguity and explicit denial are both DENY.
    transcribe.text = "yes"
    assert await provider.decide(request) is VoiceResult.DENY
    transcribe.text = "no"
    assert await provider.decide(request) is VoiceResult.DENY
    transcribe.text = ""
    assert await provider.decide(request) is VoiceResult.DENY

    # A speak failure (before capture) defers to the authenticated UI.
    captures_before = capture.calls
    speak.raise_error = True
    assert await provider.decide(request) is VoiceResult.DEFER
    assert capture.calls == captures_before

    assert [row["error_code"] for row in voice_rows(audit)] == [
        "ambiguous",
        "empty_transcript",
        "speak_error",
    ]
