"""Additional send credentials, persisted as hashes beside the device state."""

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading


class PushKeyStore:
    def __init__(self, state_file):
        self.path = state_file.with_name(state_file.stem + ".push-keys.json")
        self.lock = threading.Lock()
        self.hashes = json.loads(self.path.read_text("utf-8")) if self.path.exists() else []
        if not isinstance(self.hashes, list) or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in self.hashes
        ):
            raise ValueError("Invalid push key state file; restore it before starting")

    def check(self, key):
        digest = hashlib.sha256(key.encode()).hexdigest()
        with self.lock:
            return any(hmac.compare_digest(digest, value) for value in self.hashes)

    def create(self):
        key = secrets.token_urlsafe(32)
        digest = hashlib.sha256(key.encode()).hexdigest()
        with self.lock:
            updated = [*self.hashes, digest]
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
                self.hashes = updated
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        return key
