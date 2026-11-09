"""Security tests: reminders persist in SQLite (Task 21)."""


def test_reminder_survives_reinit(tmp_path):
    from core import scheduler

    db = tmp_path / "scheduler.db"
    scheduler.init(None, None, db_path=str(db))
    scheduler.set_reminder(600, "study chapter 4")
    scheduler.init(None, None, db_path=str(db))
    assert "study chapter 4" in scheduler.list_reminders()


def test_due_reminder_fires_once(tmp_path):
    from core import scheduler

    fired = []
    scheduler.init(None, fired.append, db_path=str(tmp_path / "scheduler.db"))
    scheduler.set_reminder(0, "stretch")
    scheduler._check_reminders()
    scheduler._check_reminders()
    assert fired == ["stretch"]


def test_cancel_reminder(tmp_path):
    from core import scheduler

    scheduler.init(None, None, db_path=str(tmp_path / "scheduler.db"))
    scheduler.set_reminder(600, "study chapter 4")
    assert "Cancelled 1" in scheduler.cancel_reminder("chapter 4")
    assert "No pending" in scheduler.list_reminders()


def test_pending_snapshot_is_read_only(tmp_path):
    from core import scheduler

    scheduler.init(None, None, db_path=str(tmp_path / "scheduler.db"))
    scheduler.set_reminder(600, "lunch")
    snapshot = scheduler.pending_snapshot()
    assert snapshot[0]["message"] == "lunch"
    assert snapshot[0]["type"] == "timer"
    snapshot.clear()
    assert "lunch" in scheduler.list_reminders()


def test_morning_briefing_adapter_works_with_sqlite(tmp_path, monkeypatch):
    from core import scheduler
    from core.tools import assistant_tools

    scheduler.init(None, None, db_path=str(tmp_path / "scheduler.db"))
    scheduler.set_reminder(600, "study chapter 4")
    monkeypatch.setattr(assistant_tools.briefing, "get_weather", lambda city="Tunis": "Sunny, 25 degrees.")
    monkeypatch.setattr(assistant_tools.briefing, "get_news", lambda count=3: "Local news headlines.")
    result = assistant_tools.morning_briefing()
    assert isinstance(result, str)
    assert "study chapter 4" in result
