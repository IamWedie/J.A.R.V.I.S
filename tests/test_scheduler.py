"""Tests for core.scheduler module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_scheduler_import():
    from core import scheduler
    assert callable(scheduler.init)
    assert callable(scheduler.set_reminder)
    assert callable(scheduler.list_reminders)
    assert callable(scheduler.cancel_reminder)


def test_set_and_list():
    from core import scheduler
    scheduler.set_reminder(300, "test_reminder_pytest")
    result = scheduler.list_reminders()
    assert "test_reminder_pytest" in result
    scheduler.cancel_reminder("test_reminder_pytest")
