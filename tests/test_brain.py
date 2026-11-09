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
    assert b.registry is None
    assert b.broker is None


def test_brain_configure():
    from core.brain import Brain
    registry = object()
    broker = object()
    b = Brain()
    b.configure(registry, broker)
    assert b.registry is registry
    assert b.broker is broker


def test_fallback_models():
    from core.brain import FALLBACK_MODELS
    assert isinstance(FALLBACK_MODELS, list)
    assert len(FALLBACK_MODELS) > 0


def test_free_model_detection():
    from core.brain import is_free_model
    assert is_free_model("mimo-v2.5-free") is True
    assert is_free_model("gpt-4") is False


def test_friendly_error_401():
    from core.brain import Brain
    b = Brain()
    msg = b._friendly_error(Exception("Error code: 401 - Incorrect API key provided."))
    assert "401" in msg
    assert "opencode.ai" in msg
    assert "setup wizard" in msg


def test_friendly_error_429():
    from core.brain import Brain
    b = Brain()
    msg = b._friendly_error(Exception("Error code: 429 - rate limit"))
    assert "rate limit" in msg.lower()


def test_build_system_prompt_lists_tools():
    from core.brain import build_system_prompt

    prompt = build_system_prompt(["set_volume", "get_volume", "get_volume", "", None, 42])
    assert "get_volume" in prompt
    assert "set_volume" in prompt
    lowered = prompt.lower()
    for forbidden in ("full laptop control", "phone", "chromecast", "net_send_message", "update"):
        assert forbidden not in lowered
    assert "Only use the tools provided." in prompt
    empty = build_system_prompt([])
    assert "No tools are currently available." in empty
    assert build_system_prompt(None).endswith("No tools are currently available.")
