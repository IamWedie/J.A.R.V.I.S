"""Tests for core.memory module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import memory

TEST_USER = "test_pytest_user"


def cleanup():
    with memory._lock:
        conn = memory._get_conn()
        conn.execute("DELETE FROM conversations WHERE user=?", (TEST_USER,))
        conn.execute("DELETE FROM facts WHERE user=?", (TEST_USER,))
        conn.commit()


def setup_function():
    cleanup()


def teardown_function():
    cleanup()


def test_log_and_recent():
    memory.log("user", "pytest test message", user=TEST_USER)
    convs = memory.recent_conversations(5)
    assert len(convs) > 0
    assert convs[-1]["user"] == TEST_USER
    assert "pytest" in convs[-1]["text"]


def test_recent_has_user_field():
    memory.log("user", "user field test", user=TEST_USER)
    convs = memory.recent_conversations(1)
    assert "user" in convs[-1]


def test_search_conversations():
    memory.log("user", "unique search term xyzzy", user=TEST_USER)
    results = memory.search_conversations("xyzzy")
    assert len(results) > 0
    assert "xyzzy" in results[0]["text"]


def test_add_and_list_facts():
    ok = memory.add_fact("pytest fact test", user=TEST_USER)
    assert ok is True
    facts = memory.list_facts()
    assert any("pytest fact test" in f for f in facts)


def test_upsert_fact():
    memory.upsert_fact("pytest_topic", "version1", user=TEST_USER)
    memory.upsert_fact("pytest_topic", "version2", user=TEST_USER)
    facts = memory.find_facts_by_topic("pytest_topic", user=TEST_USER)
    assert len(facts) == 1
    assert facts[0] == "version2"


def test_remove_fact():
    memory.add_fact("fact to remove", user=TEST_USER)
    n = memory.remove_fact("fact to remove")
    assert n >= 1


def test_stats():
    s = memory.stats()
    assert "conversations" in s
    assert "facts" in s
    assert isinstance(s["conversations"], int)


def test_extract_facts():
    facts = memory.extract_facts("My name is TestBot and I work at PyTest Corp.")
    assert len(facts) > 0


def test_auto_learn():
    n = memory.auto_learn("My name is AutoLearn.", "I will remember.", user=TEST_USER)
    assert isinstance(n, int)


def test_schema_version():
    with memory._lock:
        conn = memory._get_conn()
        row = conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
        assert row is not None
        assert int(row["value"]) >= 1
