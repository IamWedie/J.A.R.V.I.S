"""Tests for core.brain module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_brain_instantiation():
    from core.brain import Brain
    b = Brain()
    assert b is not None
    assert hasattr(b, "ask")
    assert hasattr(b, "model")


def test_tools_defined():
    from core.brain import TOOLS, TOOL_FUNCTIONS
    assert isinstance(TOOLS, list)
    assert len(TOOLS) > 50
    assert isinstance(TOOL_FUNCTIONS, dict)
    assert len(TOOL_FUNCTIONS) > 50


def test_morning_briefing_tool():
    from core.brain import TOOL_FUNCTIONS
    assert "morning_briefing" in TOOL_FUNCTIONS


def test_fallback_models():
    from core.brain import FALLBACK_MODELS
    assert isinstance(FALLBACK_MODELS, list)
    assert len(FALLBACK_MODELS) > 0


def test_free_model_detection():
    from core.brain import is_free_model
    assert is_free_model("mimo-v2.5-free") is True
    assert is_free_model("gpt-4") is False
