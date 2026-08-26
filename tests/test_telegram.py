"""Tests for core.net.telegram_bot module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_telegram_import():
    from core.net import telegram_bot as tb
    assert tb is not None
    assert callable(tb.start)
    assert callable(tb.telegram_notify)
    assert callable(tb.status)


def test_telegram_status():
    from core.net import telegram_bot as tb
    s = tb.status()
    assert isinstance(s, dict)
    assert "token_set" in s
