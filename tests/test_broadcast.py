from fastapi.testclient import TestClient
from firebase_admin import exceptions, messaging

from server.app import DeviceStore, Settings, create_app
from tests.test_server import Sender


def test_default_broadcast_includes_paired_and_skips_pending(tmp_path):
    settings = Settings("s" * 32, {"phone": "p" * 32}, tmp_path / "devices.json")
    store = DeviceStore(settings.state_file)
    store.update("phone", "phone-token")
    store.add_paired("tablet", "t" * 32)
    store.update("tablet", "tablet-token")
    store.add_paired("pending", "q" * 32)
    sender = Sender()
    client = TestClient(create_app(settings, sender))
    payload = {"title": "broadcast", "body": "test"}
    assert client.post("/notify", json=payload).status_code == 401
    headers = {"Authorization": "Bearer " + settings.send_key}
    response = client.post("/notify", json=payload, headers=headers)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "accepted" and result["mode"] == "broadcast"
    assert result["accepted_count"] == 2 and result["failed_count"] == 0
    assert {r["device"] for r in result["results"]} == {"phone", "tablet"}
    assert {call[0] for call in sender.calls} == {"phone-token", "tablet-token"}
    assert {call[1]["id"] for call in sender.calls} == {result["id"]}
    assert "tablet-token" not in response.text
    sender.calls.clear()
    targeted = client.post("/notify", json={**payload, "device": "tablet"}, headers=headers)
    assert targeted.status_code == 200 and "fcm_message_id" in targeted.json()
    assert len(sender.calls) == 1 and sender.calls[0][0] == "tablet-token"
    assert (
        client.post("/notify", json={**payload, "device": ""}, headers=headers).status_code == 422
    )


def test_broadcast_partial_failure_and_expired_cleanup(tmp_path):
    settings = Settings("s" * 32, {"phone": "p" * 32}, tmp_path / "devices.json")
    store = DeviceStore(settings.state_file)
    store.update("phone", "good")
    store.add_paired("expired", "e" * 32)
    store.update("expired", "expired")
    store.update("unavailable", "unavailable")

    class MixedSender:
        def send(self, token, *_):
            if token == "expired":
                raise messaging.UnregisteredError("expired")
            if token == "unavailable":
                raise exceptions.UnavailableError("unavailable")
            return "accepted"

    client = TestClient(create_app(settings, MixedSender()))
    response = client.post(
        "/notify",
        json={"title": "x", "body": "x"},
        headers={"Authorization": "Bearer " + settings.send_key},
    )
    assert response.status_code == 200
    result = response.json()
    assert (
        result["status"] == "partial"
        and result["accepted_count"] == 1
        and result["failed_count"] == 2
    )
    assert {r.get("error") for r in result["results"] if r["status"] == "failed"} == {
        "registration_expired",
        "fcm_error",
    }
    restored = DeviceStore(settings.state_file)
    assert not restored.get("expired") and restored.check_key("expired", "e" * 32)
    assert restored.get("unavailable") == "unavailable"


def test_broadcast_empty_and_all_failed(tmp_path):
    settings = Settings("s" * 32, {"phone": "p" * 32}, tmp_path / "devices.json")
    sender = Sender()
    client = TestClient(create_app(settings, sender))
    headers = {"Authorization": "Bearer " + settings.send_key}
    payload = {"title": "x", "body": "x"}
    assert client.post("/notify", json=payload, headers=headers).status_code == 409
    assert (
        client.put(
            "/devices/phone",
            json={"token": "token"},
            headers={"Authorization": "Bearer " + "p" * 32},
        ).status_code
        == 200
    )
    sender.error = exceptions.UnavailableError("unavailable")
    result = client.post("/notify", json=payload, headers=headers).json()
    assert (
        result["status"] == "failed"
        and result["accepted_count"] == 0
        and result["failed_count"] == 1
    )
    sender.error = None
    sender.calls.clear()
    assert (
        client.post("/notify", json={**payload, "body": "😀" * 1500}, headers=headers).status_code
        == 422
    )
    assert not sender.calls
