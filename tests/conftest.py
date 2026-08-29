"""Shared pytest fixtures. Resets approval/PIN lockout state between tests so
module-level state doesn't leak across test functions."""
import pytest


@pytest.fixture(autouse=True)
def _reset_pin_security():
    from core import approval
    approval._pin_attempts.clear()
    approval.cancel_all()
    yield
    approval._pin_attempts.clear()
    approval.cancel_all()
