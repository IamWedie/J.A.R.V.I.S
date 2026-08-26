"""Tests for core.config module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_version():
    from core.config import VERSION
    assert isinstance(VERSION, str)
    assert len(VERSION) > 0


def test_data_dir():
    from core.config import data_dir
    d = data_dir()
    assert os.path.isdir(d)


def test_resolve_path():
    from core.config import resolve_path
    assert isinstance(resolve_path("test"), str)
    assert isinstance(resolve_path(""), str)


def test_zen_config():
    from core.config import ZEN_BASE_URL, HOST, PORT
    assert "opencode.ai" in ZEN_BASE_URL
    assert HOST == "127.0.0.1"
    assert isinstance(PORT, int)


def test_voice_choices():
    from core.config import VOICE_CHOICES
    assert len(VOICE_CHOICES) > 0
    assert any("ar-TN" in v[0] for v in VOICE_CHOICES)


def test_stt_config():
    from core.config import STT_MODEL, STT_LANG
    assert isinstance(STT_MODEL, str)
    assert STT_LANG == "auto" or STT_LANG is None


def test_save_settings():
    from core.config import save_settings, ENV_PATH
    assert callable(save_settings)
