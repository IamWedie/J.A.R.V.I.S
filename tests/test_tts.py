"""Tests for core.tts module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_speaker_loads():
    from core.tts import speaker
    assert speaker is not None
    assert hasattr(speaker, "speak")
    assert hasattr(speaker, "stop")
    assert hasattr(speaker, "speaking")


def test_stop_clears_state():
    from core.tts import speaker
    speaker.stop()
    assert speaker.speaking is False
