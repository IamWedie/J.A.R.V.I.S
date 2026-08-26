"""Tests for core.greeter module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_time_greeting():
    from core.greeter import _time_greeting
    g = _time_greeting()
    assert isinstance(g, str)
    assert len(g) > 0


def test_start_phrase():
    from core.greeter import start_phrase
    p = start_phrase()
    assert isinstance(p, str)
    assert len(p) > 0


def test_welcome_back_phrase():
    from core.greeter import welcome_back_phrase
    p = welcome_back_phrase(5)
    assert isinstance(p, str)


def test_get_idle_seconds():
    from core.greeter import get_idle_seconds
    idle = get_idle_seconds()
    assert isinstance(idle, int)
    assert idle >= 0
