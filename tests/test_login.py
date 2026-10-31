import pytest
from fastapi.testclient import TestClient

from server.app import Settings, create_app
from tests.test_server import Sender

COOKIE = "__Host-notify_session"
BROWSER = {"X-Notify-Request": "dashboard", "Origin": "https://notify.example"}


@pytest.fixture
def dashboard(tmp_path):
    settings = Settings(
        "s" * 32,
        {"phone": "p" * 32},
        tmp_path / "devices.json",
        "admin" * 8,
        "https://notify.example",
    )
    app = create_app(settings, Sender())
    return TestClient(app, base_url=settings.public_url), settings


def login(client, settings):
    return client.post("/session", json={"password": settings.pair_password}, headers=BROWSER)


def test_dashboard_login_cookie_and_logout(dashboard):
    client, settings = dashboard
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert 'id="login-form"' in client.get("/login").text
    assert 'id="push-key-form"' not in client.get("/").text
    assert client.get("/session").status_code == 401
    assert client.post("/push-keys", json={}, headers=BROWSER).status_code == 401
    assert client.post("/pairings", json={"device": "tablet"}, headers=BROWSER).status_code == 401
    response = login(client, settings)
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    for attribute in ["HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=28800"]:
        assert attribute in cookie
    assert "Domain=" not in cookie
    assert settings.pair_password not in response.text
    token = client.cookies.get(COOKIE)
    assert token not in response.text
    assert client.get("/session").json()["authenticated"]
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"
    page = client.get("/")
    assert 'id="logout"' in page.text and 'id="push-key-form"' in page.text
    assert 'type="password"' not in page.text
    assert page.headers["cache-control"] == "no-store"
    assert client.post("/pairings", json={"device": "tablet"}, headers=BROWSER).status_code == 200
    assert client.post("/push-keys", json={}, headers=BROWSER).status_code == 201
    assert client.delete("/session", headers=BROWSER).status_code == 204
    assert client.get("/session").status_code == 401
    # A copied cookie also stops working after logout; clearing only the browser is insufficient.
    assert (
        client.post(
            "/push-keys", json={}, headers={**BROWSER, "Cookie": f"{COOKIE}={token}"}
        ).status_code
        == 401
    )


def test_cookie_admin_requests_reject_cross_origin_and_missing_header(dashboard):
    client, settings = dashboard
    assert client.post("/session", json={"password": settings.pair_password}).status_code == 403
    assert (
        client.post(
            "/session",
            json={"password": settings.pair_password},
            headers={**BROWSER, "Origin": "https://foreign.example"},
        ).status_code
        == 403
    )
    assert login(client, settings).status_code == 201
    for headers in [
        {},
        {**BROWSER, "Origin": "https://foreign.example"},
        {**BROWSER, "Origin": "null"},
    ]:
        assert client.post("/push-keys", json={}, headers=headers).status_code == 403
        assert (
            client.post("/pairings", json={"device": "tablet"}, headers=headers).status_code == 403
        )
        assert client.delete("/session", headers=headers).status_code == 403
    assert client.get("/session").status_code == 200
    assert not settings.state_file.exists()
    assert not settings.state_file.with_name("devices.push-keys.json").exists()


def test_session_expiry_restart_and_rotation(dashboard, monkeypatch):
    client, settings = dashboard
    now = [1000]
    monkeypatch.setattr("server.pairing.time.monotonic", lambda: now[0])
    assert login(client, settings).status_code == 201
    first = client.cookies.get(COOKIE)
    assert login(client, settings).status_code == 201
    second = client.cookies.get(COOKIE)
    assert first != second
    assert client.get("/session", headers={"Cookie": f"{COOKIE}={first}"}).status_code == 401
    now[0] += 28800
    assert client.get("/session").status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.post("/push-keys", json={}, headers=BROWSER).status_code == 401
    assert login(client, settings).status_code == 201
    token = client.cookies.get(COOKIE)
    restarted = TestClient(create_app(settings, Sender()), base_url=settings.public_url)
    assert restarted.get("/session", headers={"Cookie": f"{COOKIE}={token}"}).status_code == 401


def test_login_shares_password_rate_limit(dashboard):
    client, settings = dashboard
    for _ in range(5):
        assert (
            client.post("/session", json={"password": "wrong"}, headers=BROWSER).status_code == 401
        )
        assert client.post("/push-keys", json={"password": "wrong"}).status_code == 401
    assert login(client, settings).status_code == 429
    assert client.cookies.get(COOKIE) is None
    assert client.get("/session").status_code == 401


def test_session_cookie_cannot_send_notifications(dashboard):
    client, settings = dashboard
    assert login(client, settings).status_code == 201
    assert client.post("/notify", json={"title": "test", "body": "test"}).status_code == 401
