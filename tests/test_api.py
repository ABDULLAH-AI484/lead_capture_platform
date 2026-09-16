import os
os.environ["DATABASE_URL"] = "sqlite:///./test_leads.db"
os.environ["APP_SECRET"] = "test-secret"
os.environ["RATE_LIMIT_REQUESTS"] = "2"
os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "60"
from fastapi.testclient import TestClient
from app.main import app, limiter, init_db

client = TestClient(app)
init_db()

def login(email="owner@example.com", password="owner-password"):
    return client.post("/auth/login", json={"email":email,"password":password}).json()["access_token"]

def test_auth_and_tenant_isolation():
    owner=login(); other=login("other@example.com","other-password")
    assert client.get("/widgets").status_code == 401
    created=client.post("/widgets", headers={"Authorization":f"Bearer {owner}"}, json={"title":"Private"})
    assert created.status_code == 201
    wid=created.json()["id"]
    assert client.get(f"/widgets/{wid}",headers={"Authorization":f"Bearer {other}"}).status_code == 404
    assert client.get(f"/widgets/{wid}",headers={"Authorization":f"Bearer {owner}"}).status_code == 200

def test_public_cache_cors_and_submission():
    limiter.clear(); response=client.get("/widgets/demo/config",headers={"Origin":"http://localhost:5500"})
    assert response.status_code == 200 and "max-age=60" in response.headers["cache-control"]
    preflight=client.options("/submissions",headers={"Origin":"http://localhost:5500","Access-Control-Request-Method":"POST"})
    assert preflight.status_code == 200 and "access-control-allow-origin" in preflight.headers
    accepted=client.post("/submissions",headers={"Origin":"http://localhost:5500"},json={"widget_id":"demo","data":{"email":"visitor@example.com"},"website":""})
    assert accepted.status_code == 201

def test_honeypot_and_rate_limit():
    limiter.clear()
    spam=client.post("/submissions",json={"widget_id":"demo","data":{},"website":"bot"})
    assert spam.status_code == 422
    for _ in range(2): client.post("/submissions",json={"widget_id":"demo","data":{"email":"x@y.com"},"website":""})
    assert client.post("/submissions",json={"widget_id":"demo","data":{"email":"x@y.com"},"website":""}).status_code == 429

def test_fallback_and_noncritical_failure(monkeypatch):
    limiter.clear(); monkeypatch.setenv("GEO_PROVIDER_MODE","provider_a_down"); monkeypatch.setenv("SIDE_EFFECT_MODE","fail")
    r=client.post("/submissions",json={"widget_id":"demo","data":{"email":"fallback@y.com"},"website":""})
    assert r.status_code == 201 and r.json()["geo"]["provider"] == "B"
    limiter.clear(); monkeypatch.setenv("GEO_PROVIDER_MODE","all_down")
    r=client.post("/submissions",json={"widget_id":"demo","data":{"email":"nogeo@y.com"},"website":""})
    assert r.status_code == 201 and r.json()["geo"] is None
