import smtplib
import ssl

import pytest
from fastapi.testclient import TestClient

from server.app import DeviceStore, Settings, create_app
from server.email_notify import EmailSender
from tests.test_server import Sender, send


@pytest.fixture
def smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_SERVER_ADDRESS_PORT", "smtp.example.com:587")
    monkeypatch.setenv("SMPT_USERNAME", "notify@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "private-test-password")
    monkeypatch.setenv("SUBSCRIBER_EMAIL", "[a@example.com, b@example.com, a@example.com]")


def test_email_settings_disabled_and_invalid(monkeypatch):
    for key in ("SMTP_SERVER_ADDRESS_PORT", "SMPT_USERNAME", "SMTP_PASSWORD", "SUBSCRIBER_EMAIL"):
        monkeypatch.delenv(key, raising=False)
    assert EmailSender.from_env() is None
    monkeypatch.setenv("SMTP_PASSWORD", "private-test-password")
    with pytest.raises(ValueError, match="all four"):
        EmailSender.from_env()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SMTP_SERVER_ADDRESS_PORT", "smtp.example.com:0"),
        ("SMTP_SERVER_ADDRESS_PORT", "smtp.example.com:587/path"),
        ("SMPT_USERNAME", "notify"),
        ("SUBSCRIBER_EMAIL", "[]"),
        ("SUBSCRIBER_EMAIL", "[a@example.com, invalid]"),
    ],
)
def test_email_settings_reject_bad_values(smtp_env, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        EmailSender.from_env()


@pytest.mark.parametrize("port", [465, 587])
def test_email_transport_privacy_partial_acceptance_and_quit_error(smtp_env, monkeypatch, port):
    monkeypatch.setenv("SMTP_SERVER_ADDRESS_PORT", f"smtp.example.com:{port}")
    commands = []

    class SMTP:
        def __init__(self, host, smtp_port, *, timeout, context=None):
            assert (host, smtp_port, timeout) == ("smtp.example.com", port, 15)
            if port == 465:
                commands.append("tls")
                assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            else:
                assert context is None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            raise smtplib.SMTPServerDisconnected("QUIT failed")

        def starttls(self, *, context):
            commands.append("tls")
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED

        def login(self, username, password):
            commands.append("auth")
            assert (username, password) == ("notify@example.com", "private-test-password")

        def send_message(self, message, *, to_addrs):
            commands.append("data")
            assert to_addrs == ("a@example.com", "b@example.com")
            assert message["From"] == "notify@example.com"
            assert message["Subject"] == "标题 Bcc: injected@example.com"
            assert message.get_content() == "正文\n"
            assert "Bcc" not in message and all(address not in str(message) for address in to_addrs)
            return {"b@example.com": (550, b"private rejection message")}

    monkeypatch.setattr(smtplib, "SMTP_SSL" if port == 465 else "SMTP", SMTP)
    sender = EmailSender.from_env()
    assert "private-test-password" not in repr(sender)
    assert sender.send("标题\r\nBcc: injected@example.com", "正文") == [
        {"channel": "email", "status": "accepted"},
        {"channel": "email", "status": "failed", "error": "email_rejected"},
    ]
    assert commands == ["tls", "auth", "data"]


def test_email_routing_and_transport_failure(smtp_env, monkeypatch, tmp_path):
    attempts = []

    def offline(*args, **kwargs):
        attempts.append(args)
        raise OSError("private SMTP server details")

    monkeypatch.setattr(smtplib, "SMTP", offline)
    monkeypatch.setenv("NOTIFY_SEND_KEY", "s" * 32)
    monkeypatch.setenv("NOTIFY_DEVICE_KEYS", '{"phone":"' + "p" * 32 + '"}')
    monkeypatch.setenv("NOTIFY_STATE_FILE", str(tmp_path / "devices.json"))
    monkeypatch.delenv("NOTIFY_ALEXA_CODE_FILE", raising=False)
    monkeypatch.delenv("NOTIFY_PAIR_PASSWORD", raising=False)
    settings = Settings.from_env()
    client = TestClient(create_app(settings, Sender()))

    response = send(client, device=None, urgent=False)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "failed" and result["failed_count"] == 2
    assert (
        result["results"] == [{"channel": "email", "status": "failed", "error": "email_error"}] * 2
    )

    DeviceStore(settings.state_file).update("phone", "test-token")
    client = TestClient(create_app(settings, Sender()))
    response = send(client, device=None)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "partial" and result["accepted_count"] == 1
    assert result["failed_count"] == 2
    assert len(attempts) == 2
    assert send(client).status_code == 200
    assert len(attempts) == 2


def test_email_only_skips_fcm_and_alexa(smtp_env, monkeypatch, tmp_path):
    monkeypatch.setenv("NOTIFY_SEND_KEY", "s" * 32)
    monkeypatch.setenv("NOTIFY_STATE_FILE", str(tmp_path / "devices.json"))
    monkeypatch.delenv("NOTIFY_DEVICE_KEYS", raising=False)
    monkeypatch.delenv("NOTIFY_PAIR_PASSWORD", raising=False)
    monkeypatch.setenv("CHANNEL_FCM_ENABLED", "false")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "missing-firebase.json"))
    monkeypatch.setenv("CHANNEL_ALEXA_ENABLED", "false")
    monkeypatch.setenv("NOTIFY_ALEXA_CODE_FILE", str(tmp_path / "missing-alexa.txt"))
    monkeypatch.setenv("CHANNEL_EMAIL_ENABLED", "true")

    def fake_email(self, title, body):
        return [{"channel": "email", "status": "accepted"}]

    monkeypatch.setattr(EmailSender, "send", fake_email)
    settings = Settings.from_env()
    DeviceStore(settings.state_file).update("phone", "existing-token")
    client = TestClient(create_app(settings))
    assert send(client, device=None).json()["results"] == [
        {"channel": "email", "status": "accepted"}
    ]
    assert send(client).json()["detail"] == "FCM channel is disabled"

    monkeypatch.setenv("CHANNEL_EMAIL_ENABLED", "false")
    monkeypatch.setenv("SMTP_SERVER_ADDRESS_PORT", "ignored invalid configuration")
    client = TestClient(create_app())
    assert send(client, device=None).json()["detail"] == "No eligible channels"
