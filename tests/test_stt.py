"""Tests for core.stt module."""
import sys
import os
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_transcriber_loads():
    from core.stt import transcriber
    assert transcriber is not None
    assert hasattr(transcriber, "transcribe_array")


def test_short_audio_rejection():
    from core.stt import transcriber
    short = np.zeros(5000, dtype=np.float32)
    result = transcriber.transcribe_array(short)
    assert result == ""


def test_allowed_langs():
    from core.stt import ALLOWED_LANGS
    assert "en" in ALLOWED_LANGS
    assert "ar" in ALLOWED_LANGS
    assert "ja" not in ALLOWED_LANGS
