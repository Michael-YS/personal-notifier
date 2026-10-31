"""One-use pairing tickets in memory; device credentials persist in the existing JSON."""

import hmac
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import qrcode
import qrcode.image.svg
from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field


class AdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


class ManagementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str | None = Field(default=None, min_length=1, max_length=256)


class PairRequest(ManagementRequest):
    device: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class RedeemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=32, max_length=128)


def install_pairing(app, settings, store, push_keys):
    tickets = {}
    attempts = deque()
    sessions = {}
    cookie_name = "__Host-notify_session"
    session_seconds = 8 * 60 * 60
    lock = threading.Lock()
    web = Path(__file__).parent / "web"

    def current_session(request):
        token = request.cookies.get(cookie_name)
        now = time.monotonic()
        for value, deadline in list(sessions.items()):
            if deadline <= now:
                del sessions[value]
        return token if token in sessions else None

    def check_browser_request(request):
        # Cross-origin forms cannot set this header; CORS does not allow foreign origins.
        origin = request.headers.get("origin")
        public = urlsplit(settings.public_url)
        expected_origin = f"{public.scheme}://{public.netloc}"
        if request.headers.get("x-notify-request") != "dashboard" or (
            origin and origin != expected_origin
        ):
            raise HTTPException(403, "Invalid request origin")

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
    def index(request: Request):
        with lock:
            if not current_session(request):
                return RedirectResponse("/login", status_code=303)
        return FileResponse(web / "index.html", media_type="text/html")

    @app.get("/login")
    def login_page(request: Request):
        with lock:
            if current_session(request):
                return RedirectResponse("/", status_code=303)
        return FileResponse(web / "login.html", media_type="text/html")

    @app.get("/login.js")
    def login_javascript():
        return FileResponse(web / "login.js", media_type="text/javascript")

    @app.get("/pair.js")
    def javascript():
        return FileResponse(web / "pair.js", media_type="text/javascript")

    @app.get("/pair.css")
    def stylesheet():
        return FileResponse(web / "pair.css", media_type="text/css")

    def authenticate_admin(password):
        # Called under the shared lock so both admin actions share one rate limit.
        if not settings.pair_password:
            raise HTTPException(503, "Pairing is not configured")
        now = time.monotonic()
        while attempts and attempts[0] <= now - 60:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, "Wait a minute before trying again")
        attempts.append(now)
        if not hmac.compare_digest(password.encode(), settings.pair_password.encode()):
            raise HTTPException(401, "Incorrect password")

    def authenticate_management(request, password):
        if password is not None:
            authenticate_admin(password)
        else:
            if not current_session(request):
                raise HTTPException(401, "Login required")
            check_browser_request(request)

    @app.post("/session", status_code=201)
    def login(body: AdminRequest, request: Request, response: Response):
        check_browser_request(request)
        with lock:
            authenticate_admin(body.password)
            previous = current_session(request)
            if len(sessions) >= 100 and not previous:
                raise HTTPException(429, "Too many active sessions")
            if previous:
                del sessions[previous]
            token = secrets.token_urlsafe(32)
            sessions[token] = time.monotonic() + session_seconds
        response.set_cookie(
            cookie_name,
            token,
            max_age=session_seconds,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return {"authenticated": True, "expires_in": session_seconds}

    @app.get("/session")
    def session(request: Request):
        with lock:
            if not current_session(request):
                raise HTTPException(401, "Login required")
            token = request.cookies[cookie_name]
            return {
                "authenticated": True,
                "expires_in": max(1, int(sessions[token] - time.monotonic())),
            }

    @app.delete("/session", status_code=204)
    def logout(request: Request, response: Response):
        check_browser_request(request)
        with lock:
            sessions.pop(request.cookies.get(cookie_name), None)
        response.delete_cookie(cookie_name, path="/", secure=True, httponly=True, samesite="strict")

    @app.post("/push-keys", status_code=201)
    def create_push_key(body: ManagementRequest, request: Request):
        with lock:
            authenticate_management(request, body.password)
            return {"push_key": push_keys.create()}

    @app.post("/pairings")
    def create_pairing(body: PairRequest, request: Request):
        with lock:
            authenticate_management(request, body.password)
            now = time.monotonic()
            if body.device in settings.device_keys or store.has(body.device):
                raise HTTPException(409, "Device name already exists; choose a new name")
            for code, (_, deadline) in list(tickets.items()):
                if deadline <= now:
                    del tickets[code]
            if len(tickets) >= 100:
                raise HTTPException(429, "Too many pending pairings")
            code = secrets.token_urlsafe(32)
            tickets[code] = (body.device, now + 300)
        uri = "personalnotify://pair?" + urlencode({"server": settings.public_url, "code": code})
        svg = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage).to_string().decode()
        return {"qr_svg": svg, "expires_in": 300, "device": body.device}

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
