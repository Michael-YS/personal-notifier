"""Additional send credentials, persisted as hashes beside the device state."""

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
from uuid import uuid4


def valid_digest(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


class PushKeyStore:
    def __init__(self, state_file):
        self.path = state_file.with_name(state_file.stem + ".push-keys.json")
        self.lock = threading.Lock()
        state = json.loads(self.path.read_text("utf-8")) if self.path.exists() else []
        if isinstance(state, list) and all(valid_digest(value) for value in state):
            state = {
                hashlib.sha256(value.encode()).hexdigest()[:32]: {
                    "digest": value,
                    "name": f"Push key {number}",
                }
                for number, value in enumerate(state, 1)
            }
        if not isinstance(state, dict) or any(
            not isinstance(record, dict)
            or not valid_digest(record.get("digest"))
            or not isinstance(record.get("name"), str)
            for record in state.values()
        ):
            raise ValueError("Invalid push key state file; restore it before starting")
        self.records = state

    def check(self, key):
        digest = hashlib.sha256(key.encode()).hexdigest()
        with self.lock:
            return any(
                hmac.compare_digest(digest, record["digest"]) for record in self.records.values()
            )

    def create(self, name="Push key"):
        key = secrets.token_urlsafe(32)
        digest = hashlib.sha256(key.encode()).hexdigest()
        key_id = uuid4().hex
        with self.lock:
            self._save({**self.records, key_id: {"digest": digest, "name": name}})
        return key_id, key

    def list_keys(self):
        with self.lock:
            return [
                {"id": key_id, "name": record["name"]} for key_id, record in self.records.items()
            ]

    def change(self, key_id, *, name=None):
        with self.lock:
            if key_id not in self.records:
                return False
            updated = dict(self.records)
            if name is None:
                del updated[key_id]
            else:
                updated[key_id] = {**updated[key_id], "name": name}
            self._save(updated)
            return True

    def _save(self, updated):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as file:
                temporary = file.name
                json.dump(updated, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            self.records = updated
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
