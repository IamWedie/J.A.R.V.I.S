import pytest

from core.security.auth import (
    ALLOWED_ORIGINS, LaunchToken, Session, SessionManager, origin_allowed,
)

def test_token_is_long_and_random():
    a, b = LaunchToken.generate(), LaunchToken.generate()
    assert len(a.value) >= 32
    assert a.value != b.value

def test_token_matches_exact_value_only():
    token = LaunchToken.generate()
    assert token.matches(token.value) is True
    assert token.matches(token.value + "x") is False
    assert token.matches("") is False
    assert token.matches(None) is False
    assert token.matches(token.value + "ö") is False

def test_origin_allows_only_local_ui():
    assert origin_allowed("http://127.0.0.1:8741") is True
    assert origin_allowed("http://localhost:8741") is True
    for bad in (None, "", "null", "http://evil.test",
                "https://127.0.0.1:8741", "http://127.0.0.1:9999"):
        assert origin_allowed(bad) is False
    assert "http://127.0.0.1:8741" in ALLOWED_ORIGINS

def test_activate_revokes_previous_session():
    manager = SessionManager()
    first = manager.create(object())
    assert manager.activate(first) is None
    assert manager.get_active() is first
    assert manager.activate(first) is None
    second = manager.create(object())
    replaced = manager.activate(second)
    assert replaced is first
    assert first.revoked_at is not None
    assert first.active is False
    assert second.active is True
    assert manager.get_active() is second
    assert manager.is_active(first.session_id) is False

def test_closed_session_is_not_active():
    manager = SessionManager()
    session = manager.create(object())
    manager.activate(session)
    manager.close(session.session_id)
    assert manager.get_active() is None
    assert manager.is_active(session.session_id) is False
    with pytest.raises(ValueError):
        manager.activate(session)
    with pytest.raises(LookupError):
        manager.activate(
            Session(session_id="missing", websocket=object(), created_at=0.0)
        )

def test_session_ids_are_unique():
    manager = SessionManager()
    ids = {manager.create(object()).session_id for _ in range(50)}
    assert len(ids) == 50
