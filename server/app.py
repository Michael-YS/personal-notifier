"""Single-process personal FCM gateway. Notification history stays on the phone."""

import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import firebase_admin
from fastapi import FastAPI, Header, HTTPException
from firebase_admin import exceptions, messaging
from google.auth.exceptions import GoogleAuthError
from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class Settings:
    send_key: str
    device_keys: dict[str, str]
    state_file: Path
    pair_password: str = ""
    public_url: str = ""
    alexa_code_file: Path | None = None

    @classmethod
    def from_env(cls):
        keys = json.loads(os.environ["NOTIFY_DEVICE_KEYS"])
        send_key = os.environ["NOTIFY_SEND_KEY"]
        if not isinstance(keys, dict) or not keys:
            raise ValueError("NOTIFY_DEVICE_KEYS must be a nonempty JSON object")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) for name in keys):
            raise ValueError("Invalid device name")
        secrets = [send_key, *keys.values()]
        if any(not isinstance(key, str) or len(key) < 32 for key in secrets):
            raise ValueError("All keys must contain at least 32 characters")
        if len(set(secrets)) != len(secrets):
            raise ValueError("Use a different key for sending and for each device")
        password = os.getenv("NOTIFY_PAIR_PASSWORD", "")
        public_url = os.getenv("NOTIFY_PUBLIC_URL", "").rstrip("/")
        if password:
            url = urlsplit(public_url)
            if len(password) < 16 or password in secrets:
                raise ValueError("Use an independent pairing password of at least 16 characters")
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.query
                or url.fragment
            ):
                raise ValueError("NOTIFY_PUBLIC_URL must be an HTTPS URL")
        return cls(
            send_key,
            keys,
            Path(os.getenv("NOTIFY_STATE_FILE", "data/devices.json")),
            password,
            public_url,
            Path(os.environ["NOTIFY_ALEXA_CODE_FILE"])
            if os.getenv("NOTIFY_ALEXA_CODE_FILE")
            else None,
        )


class DeviceStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.devices = json.loads(path.read_text("utf-8")) if path.exists() else {}
        if not isinstance(self.devices, dict) or any(
            not isinstance(value, dict) or not isinstance(value.get("token"), str)
            for value in self.devices.values()
        ):
            raise ValueError("Invalid devices state file; restore it before starting")

    def get(self, name):
        with self.lock:
            value = self.devices.get(name)
            return value["token"] if value else None

    def registered(self):
        with self.lock:
            return {name: value["token"] for name, value in self.devices.items() if value["token"]}

    def update(self, name, token):
        with self.lock:
            updated = dict(self.devices)
            updated[name] = {
                **updated.get(name, {}),
                "token": token,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._save(updated)

    def has(self, name):
        with self.lock:
            return name in self.devices

    def check_key(self, name, key):
        with self.lock:
            expected = self.devices.get(name, {}).get("key_hash")
            return bool(expected) and hmac.compare_digest(
                expected, hashlib.sha256(key.encode()).hexdigest()
            )

    def add_paired(self, name, key):
        with self.lock:
            if name in self.devices:
                raise HTTPException(409, "Device name already exists")
            updated = dict(self.devices)
            updated[name] = {"token": "", "key_hash": hashlib.sha256(key.encode()).hexdigest()}
            self._save(updated)

    def remove_if_matching(self, name, token):
        with self.lock:
            if self.devices.get(name, {}).get("token") == token:
                updated = dict(self.devices)
                if "key_hash" in updated[name]:
                    updated[name] = {**updated[name], "token": ""}
                else:
                    del updated[name]
                self._save(updated)

    def _save(self, updated):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as file:
                temporary = file.name
                json.dump(updated, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            self.devices = updated
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=4096)


class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="", max_length=100)
    ttl_seconds: int = Field(default=3600, ge=0, le=2419200)
    urgent: bool = True


class FcmSender:
    def __init__(self):
        # Let the SDK cache and refresh OAuth credentials. Never ship them in the APK.
        try:
            self.firebase = firebase_admin.get_app()
        except ValueError:
            self.firebase = firebase_admin.initialize_app(options={"httpTimeout": 15})
        if not self.firebase.project_id:
            raise ValueError("Firebase project ID is missing from server credentials")

    def send(self, token, data, urgent, ttl):
        from datetime import timedelta

        return messaging.send(
            messaging.Message(
                token=token,
                data=data,
                android=messaging.AndroidConfig(
                    priority="high" if urgent else "normal",
                    ttl=timedelta(seconds=ttl),
                ),
            ),
            app=self.firebase,
        )


def create_app(settings=None, sender=None, alexa_sender=None):
    settings = settings or Settings.from_env()
    store = DeviceStore(settings.state_file)
    sender = sender or FcmSender()
    if alexa_sender is None and settings.alexa_code_file:
        from server.alexa import AlexaSender

        alexa_sender = AlexaSender(settings.alexa_code_file)
    app = FastAPI(title="Personal Notify", docs_url=None, redoc_url=None, openapi_url=None)
    from server.pairing import install_pairing

    install_pairing(app, settings, store)

    def authenticate(authorization, key):
        expected = f"Bearer {key}".encode()
        if not hmac.compare_digest((authorization or "").encode("utf-8"), expected):
            raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.put("/devices/{name}")
    def register(name: str, registration: Registration, authorization: str | None = Header(None)):
        key = settings.device_keys.get(name)
        if key is not None:
            authenticate(authorization, key)
        elif (
            not authorization
            or not authorization.startswith("Bearer ")
            or not store.check_key(name, authorization[7:])
        ):
            raise HTTPException(401, "Invalid credentials")
        store.update(name, registration.token)
        return {"device": name, "registered": True}

    @app.post("/notify")
    def notify(notification: Notification, authorization: str | None = Header(None)):
        authenticate(authorization, settings.send_key)
        include_alexa = (
            notification.device is None and notification.urgent and alexa_sender is not None
        )
        if notification.device is None:
            targets = store.registered()
            if not targets and not include_alexa:
                raise HTTPException(409, "No registered devices")
        else:
            if notification.device not in settings.device_keys and not store.has(
                notification.device
            ):
                raise HTTPException(404, "Unknown device")
            token = store.get(notification.device)
            if not token:
                raise HTTPException(409, "Device must register before sending")
            targets = {notification.device: token}
        notification_id = str(uuid.uuid4())
        data = {
            "id": notification_id,
            "title": notification.title,
            "body": notification.body,
            "source": notification.source,
            "sent_at": datetime.now(UTC).isoformat(),
        }
        # Bound UTF-8 payload size, not just character counts (Chinese/emoji take more bytes).
        if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > 3500:
            raise HTTPException(422, "Notification payload is too large")

        def deliver(target):
            name, token = target
            try:
                message_id = sender.send(token, data, notification.urgent, notification.ttl_seconds)
                return {"device": name, "status": "accepted", "fcm_message_id": message_id}
            except messaging.UnregisteredError:
                store.remove_if_matching(name, token)
                return {"device": name, "status": "failed", "error": "registration_expired"}
            except (exceptions.FirebaseError, GoogleAuthError):
                return {"device": name, "status": "failed", "error": "fcm_error"}

        if notification.device is not None:
            result = deliver(next(iter(targets.items())))
            if result["status"] == "failed":
                if result["error"] == "registration_expired":
                    raise HTTPException(
                        409, "Device registration expired; open the app to register again"
                    )
                raise HTTPException(
                    502, "FCM rejected the request; check server credentials and retry"
                )
            return {
                "id": notification_id,
                "status": "accepted",
                "fcm_message_id": result["fcm_message_id"],
            }

        # Bound concurrent upstream requests; a failed device must not stop the others.
        with ThreadPoolExecutor(max_workers=min(8, len(targets) + include_alexa)) as pool:
            alexa_future = (
                pool.submit(alexa_sender.send, notification.title, notification.body)
                if include_alexa
                else None
            )
            results = list(pool.map(deliver, targets.items()))
            if alexa_future:
                results.append(alexa_future.result())
        accepted = sum(result["status"] == "accepted" for result in results)
        return {
            "id": notification_id,
            "status": "accepted"
            if accepted == len(results)
            else "partial"
            if accepted
            else "failed",
            "mode": "broadcast",
            "accepted_count": accepted,
            "failed_count": len(results) - accepted,
            "results": results,
        }

    return app
