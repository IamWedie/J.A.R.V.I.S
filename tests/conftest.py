"""Shared pytest fixtures. Resets approval/PIN lockout state between tests so
module-level state doesn't leak across test functions."""
import os
import tempfile

os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-tests-"))
os.environ.setdefault("JARVIS_TEST_MODE", "1")
os.environ.setdefault("JARVIS_DEV", "1")

import pytest


@pytest.fixture(autouse=True)
def _reset_pin_security():
    from core import approval
    approval._pin_attempts.clear()
    approval.cancel_all()
    yield
    approval._pin_attempts.clear()
    approval.cancel_all()
