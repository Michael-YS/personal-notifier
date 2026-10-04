import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from firebase_admin import messaging

from server.app import Settings, create_app
from tests.test_server import Sender


def setup(tmp_path, monkeypatch):
    settings = Settings(
        "s" * 32,
        {"phone": "p" * 32},
        tmp_path / "devices.json",
        "admin" * 8,
        "https://notify.example",
    )
    sender = Sender()
    codes = iter(["a" * 43, "b" * 43, "c" * 43, "d" * 43])
    monkeypatch.setattr("server.pairing.secrets.token_urlsafe", lambda _: next(codes))
    return TestClient(create_app(settings, sender)), sender, settings


def test_pairing_one_use_scope_and_restart(tmp_path, monkeypatch):
    client, sender, settings = setup(tmp_path, monkeypatch)
    assert (
        client.post("/pairings", json={"password": "wrong", "device": "tablet"}).status_code == 401
    )
    response = client.post(
        "/pairings", json={"password": settings.pair_password, "device": "tablet"}
    )
    assert response.status_code == 200 and "<svg" in response.json()["qr_svg"]
    assert response.headers["cache-control"] == "no-store"
    response = client.post("/pairings/redeem", json={"code": "a" * 43})
    assert response.status_code == 200
    binding = response.json()
    assert binding["device"] == "tablet" and binding["server"] == settings.public_url
    assert binding["key"] not in settings.state_file.read_text()
    assert client.post("/pairings/redeem", json={"code": "a" * 43}).status_code == 410
    assert (
        client.post(
            "/pairings", json={"password": settings.pair_password, "device": "phone"}
        ).status_code
        == 409
    )
    restarted = TestClient(create_app(settings, sender))
    headers = {"Authorization": "Bearer " + binding["key"]}
    assert (
        restarted.put("/devices/phone", json={"token": "bad"}, headers=headers).status_code == 401
    )
    assert (
        restarted.put(
            "/devices/tablet", json={"token": "tablet-token"}, headers=headers
        ).status_code
        == 200
    )
    payload = {"device": "tablet", "title": "test", "body": "test"}
    send_headers = {"Authorization": "Bearer " + settings.send_key}
    assert restarted.post("/notify", json=payload, headers=send_headers).status_code == 200
    sender.error = messaging.UnregisteredError("expired")
    assert restarted.post("/notify", json=payload, headers=send_headers).status_code == 409
    assert (
        restarted.put("/devices/tablet", json={"token": "new-token"}, headers=headers).status_code
        == 200
    )
    assert json.loads(settings.state_file.read_text())["tablet"]["token"] == "new-token"


def test_expiry_rate_limit_and_concurrent_redeem(tmp_path, monkeypatch):
    client, _, settings = setup(tmp_path, monkeypatch)
    now = [1000]
    monkeypatch.setattr("server.pairing.time.monotonic", lambda: now[0])
    payload = {"password": settings.pair_password, "device": "tablet"}
    assert client.post("/pairings", json=payload).status_code == 200
    now[0] += 301
    assert client.post("/pairings/redeem", json={"code": "a" * 43}).status_code == 410
    assert client.post("/pairings", json=payload).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: client.post("/pairings/redeem", json={"code": "b" * 43}).status_code,
                range(2),
            )
        )
    assert sorted(results) == [200, 410]
    for _ in range(9):
        assert (
            client.post("/pairings", json={"password": "wrong", "device": "x"}).status_code == 401
        )
    assert client.post("/pairings", json=payload).status_code == 429


def test_pairing_write_failure_does_not_consume_ticket(tmp_path, monkeypatch):
    client, _, settings = setup(tmp_path, monkeypatch)
    assert (
        client.post(
            "/pairings", json={"password": settings.pair_password, "device": "tablet"}
        ).status_code
        == 200
    )
    import server.app

    original = server.app.os.replace

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(server.app.os, "replace", fail)
    import pytest

    with pytest.raises(OSError, match="disk full"):
        client.post("/pairings/redeem", json={"code": "a" * 43})
    monkeypatch.setattr(server.app.os, "replace", original)
    assert client.post("/pairings/redeem", json={"code": "a" * 43}).status_code == 200
