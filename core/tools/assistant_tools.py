"""Thin adapters exposing scheduler, memory, and briefing as manifest tools.

Each adapter is a one-call passthrough so the signed tool manifest can point
at a single small module.  ``recall_memories`` is the unchanged body of
``core.brain._recall_memories``; the secure runtime calls this adapter
directly and ``brain.py`` has no such call.
Imports stay module-level so tests can monkeypatch; nothing runs at import
time and ``core.brain`` is never imported here (avoids a cycle).
"""
from core import briefing, memory, scheduler


def set_reminder(minutes, message):
    """Schedule a relative reminder after ``minutes`` (a positive number)."""
    try:
        mins = float(minutes)
    except (TypeError, ValueError):
        raise ValueError("minutes must be a positive number") from None
    if mins <= 0:
        raise ValueError("minutes must be a positive number")
    return scheduler.set_reminder(int(round(mins * 60)), message)


def set_alarm(time, message=""):
    """Schedule an alarm at an ``HH:MM`` clock time."""
    return scheduler.set_alarm(time, message)


def list_reminders():
    """List pending reminders and alarms."""
    return scheduler.list_reminders()


def cancel_reminder(keyword=""):
    """Cancel reminders whose message contains ``keyword`` (all if empty)."""
    return scheduler.cancel_reminder(keyword)


def remember_fact(fact):
    """Store a fact in long-term memory."""
    memory.add_fact(fact)
    return "Remembered that fact."


def forget_fact(substring):
    """Forget every fact containing ``substring``."""
    removed = memory.remove_fact(substring)
    return f"Removed {removed} matching fact(s)."


def recall_memories(query):
    """Recall stored facts and past conversation matching ``query``."""
    convs = memory.search_conversations(query, limit=5)
    ql = str(query).lower()
    facts = [f for f in memory.list_facts() if ql in f.lower()]
    parts = []
    if facts:
        parts.append("Facts:\n" + "\n".join(f"- {f}" for f in facts))
    if convs:
        parts.append("Past conversation:\n" + "\n".join(
            f"[{c['ts']}] {c['role']}: {c['text'][:200]}" for c in convs
        ))
    if not parts:
        return "Nothing found in memory about that."
    return "\n".join(parts)


def morning_briefing():
    """Build the morning briefing (weather, news, reminders)."""
    return briefing.build_briefing()
