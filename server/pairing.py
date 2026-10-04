"""One-use pairing tickets in memory; device credentials persist in the existing JSON."""

import hmac
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlencode

import qrcode
import qrcode.image.svg
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)
    device: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class RedeemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=32, max_length=128)


def install_pairing(app, settings, store):
    tickets = {}
    attempts = deque()
    lock = threading.Lock()
    web = Path(__file__).parent / "web"

    @app.middleware("http")
    async def privacy_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.get("/")
    def index():
        return FileResponse(web / "index.html", media_type="text/html")

    @app.get("/pair.js")
    def javascript():
        return FileResponse(web / "pair.js", media_type="text/javascript")

    @app.get("/pair.css")
    def stylesheet():
        return FileResponse(web / "pair.css", media_type="text/css")

    @app.post("/pairings")
    def create_pairing(request: PairRequest):
        if not settings.pair_password:
            raise HTTPException(503, "Pairing is not configured")
        with lock:
            now = time.monotonic()
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if len(attempts) >= 10:
                raise HTTPException(429, "Wait a minute before trying again")
            attempts.append(now)
            if not hmac.compare_digest(request.password.encode(), settings.pair_password.encode()):
                raise HTTPException(401, "Incorrect password")
            if request.device in settings.device_keys or store.has(request.device):
                raise HTTPException(409, "Device name already exists; choose a new name")
            for code, (_, deadline) in list(tickets.items()):
                if deadline <= now:
                    del tickets[code]
            if len(tickets) >= 100:
                raise HTTPException(429, "Too many pending pairings")
            code = secrets.token_urlsafe(32)
            tickets[code] = (request.device, now + 300)
        uri = "personalnotify://pair?" + urlencode({"server": settings.public_url, "code": code})
        svg = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage).to_string().decode()
        return {"qr_svg": svg, "expires_in": 300, "device": request.device}

    @app.post("/pairings/redeem")
    def redeem(request: RedeemRequest):
        with lock:
            ticket = tickets.get(request.code)
            if not ticket or ticket[1] <= time.monotonic():
                tickets.pop(request.code, None)
                raise HTTPException(410, "Pairing code expired or already used")
            device = ticket[0]
            key = secrets.token_urlsafe(32)
            store.add_paired(device, key)
            del tickets[request.code]
        return {"server": settings.public_url, "device": device, "key": key}
