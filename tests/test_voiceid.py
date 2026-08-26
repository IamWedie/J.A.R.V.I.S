"""Tests for core.voiceid module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_voiceid_loads():
    from core.voiceid import voiceid
    assert voiceid is not None
    assert hasattr(voiceid, "enrolled")
    assert hasattr(voiceid, "identify")
    assert hasattr(voiceid, "verify")
    assert hasattr(voiceid, "enroll")


def test_profiles_dir():
    from core.voiceid import voiceid
    assert os.path.isdir(voiceid.profiles_dir)
