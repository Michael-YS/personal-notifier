import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from firebase_admin import exceptions, messaging

from server.app import DeviceStore, FcmSender, Settings, create_app


class Sender:
    def __init__(self):
        self.calls = []
        self.error = None

    def send(self, *args):
        if self.error:
            raise self.error
        self.calls.append(args)
        return "projects/test/messages/123"


@pytest.fixture
def gateway(tmp_path):
    sender = Sender()
    settings = Settings(
        "s" * 32, {"phone": "p" * 32, "tablet": "t" * 32}, tmp_path / "devices.json"
    )
    return TestClient(create_app(settings, sender)), sender, settings


def register(client):
    return client.put(
        "/devices/phone",
        json={"token": "test-token"},
        headers={"Authorization": "Bearer " + "p" * 32},
    )


def send(client, **changes):
    return client.post(
        "/notify",
        json={"device": "phone", "title": "标题", "body": "内容", **changes},
        headers={"Authorization": "Bearer " + "s" * 32},
    )


def test_auth_and_device_scope(gateway):
    client, sender, _ = gateway
    assert client.get("/healthz").status_code == 200
    assert (
        client.post("/notify", json={"device": "phone", "title": "x", "body": "x"}).status_code
        == 401
    )
    assert (
        client.put(
            "/devices/tablet", json={"token": "x"}, headers={"Authorization": "Bearer " + "p" * 32}
        ).status_code
        == 401
    )
    assert (
        client.put(
            "/devices/phone", json={"token": "x"}, headers={"Authorization": "Bearer " + "s" * 32}
        ).status_code
        == 401
    )
    assert not sender.calls


def test_registration_send_and_restart(gateway):
    client, sender, settings = gateway
    assert send(client).status_code == 409
    assert register(client).status_code == 200
    response = send(client)
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    token, data, urgent, ttl = sender.calls[0]
    assert token == "test-token" and urgent and ttl == 3600
    assert data["title"] == "标题" and data["id"] == response.json()["id"]
    assert "body" not in settings.state_file.read_text()
    restarted = TestClient(create_app(settings, sender))
    assert send(restarted).status_code == 200


def test_payload_and_upstream_errors(gateway):
    client, sender, settings = gateway
    register(client)
    assert send(client, body="😀" * 1500).status_code == 422
    assert send(client, ttl_seconds=-1).status_code == 422
    sender.error = exceptions.UnavailableError("unavailable")
    assert send(client).status_code == 502
    assert DeviceStore(settings.state_file).get("phone") == "test-token"
    sender.error = messaging.UnregisteredError("gone")
    assert send(client).status_code == 409
    assert DeviceStore(settings.state_file).get("phone") is None


def test_atomic_updates_and_stale_cleanup(tmp_path):
    store = DeviceStore(tmp_path / "devices.json")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: store.update(str(i), f"token-{i}"), range(20)))
    assert len(json.loads(store.path.read_text())) == 20
    store.update("0", "new-token")
    store.remove_if_matching("0", "token-0")
    assert store.get("0") == "new-token"


def test_corrupt_state_fails_closed(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text('{"phone":42}')
    with pytest.raises(ValueError):
        DeviceStore(path)


def test_write_failure_preserves_previous_state(tmp_path, monkeypatch):
    store = DeviceStore(tmp_path / "devices.json")
    store.update("phone", "old-token")

    def fail_replace(*args):
        raise OSError("disk error")

    monkeypatch.setattr("server.app.os.replace", fail_replace)
    with pytest.raises(OSError):
        store.update("phone", "new-token")
    assert store.get("phone") == "old-token"
    assert DeviceStore(store.path).get("phone") == "old-token"


def test_fcm_data_message_contract(monkeypatch):
    from types import SimpleNamespace

    import firebase_admin

    fake_app = SimpleNamespace(project_id="test-project")
    monkeypatch.setattr(firebase_admin, "get_app", lambda: fake_app)
    captured = []

    def capture(message, app):
        assert app is fake_app
        captured.append(message)
        return "accepted"

    monkeypatch.setattr(messaging, "send", capture)
    sender = FcmSender()
    assert sender.send("token", {"id": "123", "title": "标题"}, True, 60) == "accepted"
    message = captured[0]
    assert message.notification is None
    assert message.token == "token" and message.data["id"] == "123"
    assert message.android.priority == "high"
    assert message.android.ttl.total_seconds() == 60
