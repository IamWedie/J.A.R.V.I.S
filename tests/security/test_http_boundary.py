def test_index_injects_token_meta(client, token):
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="jarvis-token"' in response.text
    assert token in response.text

def test_index_has_security_headers(client):
    response = client.get("/")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"

def test_api_requires_token(client):
    assert client.get("/api/models").status_code == 401

def test_api_rejects_wrong_token(client):
    response = client.get("/api/models", headers={"X-JARVIS-Token": "wrong"})
    assert response.status_code == 401

def test_api_accepts_token(client, auth_headers):
    response = client.get("/api/setup_status", headers=auth_headers)
    assert response.status_code in (200, 500)

def test_health_is_public_and_minimal(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}

def test_approve_endpoint_is_removed(client, auth_headers):
    response = client.post("/api/approve", json={"approved": True}, headers=auth_headers)
    assert response.status_code == 404

def test_cors_does_not_allow_evil_origin(client):
    response = client.options(
        "/api/models",
        headers={
            "Origin": "http://evil.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") in (None, "")
