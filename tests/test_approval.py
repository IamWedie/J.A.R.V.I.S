"""Tests for core.approval module."""
import sys
import os
import asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_approval_import():
    from core.approval import create_request, resolve_by_pin, cancel_all, get_pending
    assert callable(create_request)
    assert callable(resolve_by_pin)


def test_create_and_resolve():
    import core.config as config
    from core.approval import create_request, get_pending, cancel_all
    cancel_all()
    req = create_request("test_tool", "Test description", "ui")
    assert req.tool_name == "test_tool"
    assert req.source == "ui"
    assert "test_tool" in get_pending()
    req.resolve(True)
    assert req.resolved
    assert req.future.result() is True
    cancel_all()


def test_pin_resolve():
    import core.config as config
    from core.approval import create_request, resolve_by_pin, cancel_all
    cancel_all()
    create_request("delete_file", "Delete test", "telegram")
    approved, desc = resolve_by_pin("wrong_pin")
    assert approved is False
    cancel_all()


def test_pin_correct():
    import core.config as config
    from core.approval import create_request, resolve_by_pin, cancel_all
    cancel_all()
    create_request("shutdown_pc", "Shutdown", "telegram")
    approved, desc = resolve_by_pin(config.JARVIS_PIN)
    assert approved is True
    assert "Shutdown" in desc
    cancel_all()


def test_cancel_all():
    from core.approval import create_request, cancel_all, get_pending
    cancel_all()
    create_request("tool1", "desc1")
    create_request("tool2", "desc2")
    assert len(get_pending()) == 2
    cancel_all()
    assert len(get_pending()) == 0


def test_wrong_pin_no_pending():
    from core.approval import resolve_by_pin, cancel_all
    cancel_all()
    approved, desc = resolve_by_pin("1234")
    assert approved is False
    assert desc is None
