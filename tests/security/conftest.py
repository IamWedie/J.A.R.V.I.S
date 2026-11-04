import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client():
    import server
    with TestClient(server.app) as test_client:
        yield test_client

@pytest.fixture
def token():
    import server
    return server.LAUNCH_TOKEN.value

@pytest.fixture
def auth_headers(token):
    return {"X-JARVIS-Token": token}
