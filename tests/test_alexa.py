import pytest
import requests
from fastapi.testclient import TestClient

from server.alexa import AlexaSender
from server.app import DeviceStore, Settings, create_app
from tests.test_server import Sender


def test_alexa_transport_sanitizes_failures(tmp_path, monkeypatch):
    path = tmp_path / "code.txt"
    path.write_text("private-test-code\n")
    sender = AlexaSender(path)
    codes = iter([200, 429, 403, 302])

    class Response:
        def __enter__(self):
            self.status_code = next(codes)
            return self

        def __exit__(self, *_):
            pass

    def post(url, **kwargs):
        assert url == "https://api.notifymyecho.com/v1/NotifyMe"
        assert kwargs["json"] == {
            "accessCode": "private-test-code",
            "title": "title",
            "notification": "body",
        }
        assert kwargs["allow_redirects"] is False and kwargs["timeout"] == (5, 15)
        return Response()

    monkeypatch.setattr("server.alexa.requests.post", post)
    assert sender.send("title", "body")["status"] == "accepted"
    assert sender.send("title", "body")["error"] == "alexa_rate_limited"
    assert sender.send("title", "body")["http_status"] == 403
    assert sender.send("title", "body")["status"] == "failed"

    def timeout(*args, **kwargs):
        raise requests.Timeout("private-test-code")

    monkeypatch.setattr("server.alexa.requests.post", timeout)
    result = sender.send("title", "body")
    assert result["error"] == "alexa_timeout" and "private-test-code" not in str(result)
    path.write_text("PASTE_YOUR_ALEXA_ACCESS_CODE_HERE")
    with pytest.raises(ValueError):
        AlexaSender(path)


def test_broadcast_alexa_failure_independent_and_targeted_phone_only(tmp_path):
    settings = Settings("s" * 32, {"phone": "p" * 32}, tmp_path / "devices.json")
    store = DeviceStore(settings.state_file)
    store.update("phone", "phone-token")
    fcm = Sender()

    class Alexa:
        def __init__(self):
            self.calls = 0

        def send(self, *_):
            self.calls += 1
            return {"channel": "alexa", "status": "failed", "error": "alexa_rate_limited"}

    alexa = Alexa()
    client = TestClient(create_app(settings, fcm, alexa))
    headers = {"Authorization": "Bearer " + settings.send_key}
    payload = {"title": "title", "body": "body"}
    assert client.post("/notify", json=payload).status_code == 401
    assert alexa.calls == 0
    response = client.post("/notify", json=payload, headers=headers)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "partial" and result["accepted_count"] == result["failed_count"] == 1
    assert len(fcm.calls) == alexa.calls == 1
    assert (
        client.post("/notify", json={**payload, "device": "phone"}, headers=headers).status_code
        == 200
    )
    assert alexa.calls == 1


def test_alexa_only_without_registered_phones(tmp_path):
    settings = Settings("s" * 32, {"phone": "p" * 32}, tmp_path / "devices.json")

    class Alexa:
        def send(self, *_):
            return {"channel": "alexa", "status": "accepted"}

    response = TestClient(create_app(settings, Sender(), Alexa())).post(
        "/notify",
        json={"title": "x", "body": "x"},
        headers={"Authorization": "Bearer " + settings.send_key},
    )
    assert response.status_code == 200
    assert response.json()["accepted_count"] == 1


@pytest.mark.parametrize("registered", [True, False])
def test_normal_priority_never_calls_alexa(tmp_path, registered):
    settings = Settings("s" * 32, {"phone": "p" * 32}, tmp_path / "devices.json")
    if registered:
        DeviceStore(settings.state_file).update("phone", "phone-token")

    class Alexa:
        def send(self, *_):
            pytest.fail("Normal priority must not send to Alexa")

    fcm = Sender()
    client = TestClient(create_app(settings, fcm, Alexa()))
    response = client.post(
        "/notify",
        json={"title": "normal", "body": "test", "urgent": False},
        headers={"Authorization": "Bearer " + settings.send_key},
    )
    if registered:
        assert response.status_code == 200
        result = response.json()
        assert result["accepted_count"] == 1 and len(result["results"]) == 1
        assert result["results"][0]["device"] == "phone"
        assert len(fcm.calls) == 1 and fcm.calls[0][2] is False
    else:
        assert response.status_code == 409 and not fcm.calls
