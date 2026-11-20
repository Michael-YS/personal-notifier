import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from server.app import Settings, create_app
from server.push_keys import PushKeyStore
from tests.test_server import Sender


@pytest.fixture
def gateway(tmp_path):
    settings = Settings(
        "s" * 32,
        {"phone": "p" * 32},
        tmp_path / "devices.json",
        "admin" * 8,
        "https://notify.example",
    )
    sender = Sender()
    return TestClient(create_app(settings, sender)), sender, settings


def test_push_keys_persist_and_only_authorize_sending(gateway):
    client, sender, settings = gateway
    keys = []
    for _ in range(2):
        response = client.post("/push-keys", json={"password": settings.pair_password})
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        keys.append(response.json()["push_key"])
    assert keys[0] != keys[1] and all(len(key) >= 32 for key in keys)
    state = settings.state_file.with_name("devices.push-keys.json").read_text()
    assert all(key not in state for key in keys)
    assert len(json.loads(state)) == 2
    restarted = TestClient(create_app(settings, sender))
    assert (
        restarted.put(
            "/devices/phone",
            json={"token": "fake-token"},
            headers={"Authorization": "Bearer " + "p" * 32},
        ).status_code
        == 200
    )
    for key in [*keys, settings.send_key]:
        headers = {"Authorization": "Bearer " + key}
        assert (
            restarted.post(
                "/notify", json={"title": "test", "body": "test"}, headers=headers
            ).status_code
            == 200
        )
        assert (
            restarted.put("/devices/phone", json={"token": "bad"}, headers=headers).status_code
            == 401
        )
    for key in ["bad-key", keys[0] + "x", settings.pair_password, "p" * 32]:
        assert (
            restarted.post(
                "/notify",
                json={"title": "test", "body": "test"},
                headers={"Authorization": "Bearer " + key},
            ).status_code
            == 401
        )
    assert len(sender.calls) == 3


def test_admin_validation_and_shared_rate_limit(gateway):
    client, _, settings = gateway
    assert client.post("/push-keys", json={}).status_code == 401
    assert (
        client.post(
            "/push-keys", json={"password": settings.pair_password, "extra": "x"}
        ).status_code
        == 422
    )
    for _ in range(5):
        assert client.post("/push-keys", json={"password": "wrong"}).status_code == 401
        assert (
            client.post("/pairings", json={"password": "wrong", "device": "tablet"}).status_code
            == 401
        )
    assert client.post("/push-keys", json={"password": settings.pair_password}).status_code == 429
    assert not settings.state_file.with_name("devices.push-keys.json").exists()


def test_disabled_admin(tmp_path):
    client = TestClient(create_app(Settings("s" * 32, {}, tmp_path / "devices.json"), Sender()))
    assert client.post("/push-keys", json={"password": "anything"}).status_code == 503


def test_atomic_write_failure_and_concurrent_creation(tmp_path, monkeypatch):
    store = PushKeyStore(tmp_path / "devices.json")
    _, first = store.create()
    original = store.path.read_text()
    with monkeypatch.context() as patch:

        def fail(*args):
            raise OSError("disk full")

        patch.setattr("server.push_keys.os.replace", fail)
        with pytest.raises(OSError, match="disk full"):
            store.create()
    assert store.path.read_text() == original
    assert store.check(first)
    assert list(tmp_path.iterdir()) == [store.path]
    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = list(pool.map(lambda _: store.create(), range(8)))
    restarted = PushKeyStore(tmp_path / "devices.json")
    assert restarted.check(first)
    assert all(restarted.check(key) for _, key in keys)


@pytest.mark.parametrize("state", [{"id": {}}, ["not-a-hash"], [123], ["g" * 64]])
def test_invalid_key_state_fails_startup(tmp_path, state):
    (tmp_path / "devices.push-keys.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="Invalid push key state"):
        PushKeyStore(tmp_path / "devices.json")


def test_dashboard_management(gateway, monkeypatch):
    _, sender, settings = gateway
    legacy = "legacy-test-key"
    path = settings.state_file.with_name("devices.push-keys.json")
    path.write_text(json.dumps([hashlib.sha256(legacy.encode()).hexdigest()]))
    headers = {"X-Notify-Request": "dashboard"}
    client = TestClient(create_app(settings, sender), base_url=settings.public_url)
    assert client.get("/push-keys", headers=headers).status_code == 401
    assert client.get("/paired-devices", headers=headers).status_code == 401
    assert (
        client.post(
            "/session", headers=headers, json={"password": settings.pair_password}
        ).status_code
        == 201
    )
    (old,) = client.get("/push-keys", headers=headers).json()
    assert set(old) == {"id", "name"}
    assert old["name"] == "Push key 1"
    assert PushKeyStore(settings.state_file).check(legacy)
    created = client.post("/push-keys", headers=headers, json={"name": " Build "}).json()
    url = f"/push-keys/{created['id']}"
    assert client.get("/push-keys", headers=headers).json()[-1]["name"] == "Build"
    assert client.delete(url).status_code == 403
    assert client.patch(url, headers=headers, json={"name": "CI"}).status_code == 204
    assert PushKeyStore(settings.state_file).list_keys()[-1]["name"] == "CI"
    assert client.delete(url, headers=headers).status_code == 204
    assert (
        client.post(
            "/notify",
            json={"title": "test", "body": "test"},
            headers={"Authorization": f"Bearer {created['push_key']}"},
        ).status_code
        == 401
    )
    restored = PushKeyStore(settings.state_file)
    assert not restored.check(created["push_key"])
    assert restored.check(legacy)

    monkeypatch.setattr("server.pairing.secrets.token_urlsafe", lambda _: "a" * 43)
    assert client.post("/pairings", headers=headers, json={"device": "tablet"}).status_code == 200
    binding = client.post("/pairings/redeem", json={"code": "a" * 43}).json()
    auth = {"Authorization": f"Bearer {binding['key']}"}
    assert client.get("/paired-devices", headers=headers).json() == [
        {"name": "tablet", "registered": False}
    ]
    assert (
        client.put("/devices/tablet", headers=auth, json={"token": "fake-token"}).status_code == 200
    )
    assert client.get("/paired-devices", headers=headers).json() == [
        {"name": "tablet", "registered": True}
    ]
    assert client.delete("/paired-devices/tablet", headers=headers).status_code == 204
    assert client.get("/paired-devices", headers=headers).json() == []
    restarted = TestClient(create_app(settings, sender))
    assert (
        restarted.put("/devices/tablet", headers=auth, json={"token": "fake-token"}).status_code
        == 401
    )
