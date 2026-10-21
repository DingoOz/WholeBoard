"""Optional password for other computers on the network.

When a password is set, any client that is not this computer (loopback) must log in. A signed,
expiring cookie then covers the web page, the API and the live WebSocket. Changing or clearing the
password signs everyone out. The password itself is never stored, only a PBKDF2 hash.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
import threading
import time
from urllib.parse import parse_qs

from . import config

COOKIE = "wb_auth"
LIFETIME_S = 30 * 24 * 3600
ITERATIONS = 240_000
OPEN_PATHS = {"/login", "/api/login", "/static/style.css", "/static/icon.svg", "/favicon.ico"}
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_attempts: dict[str, list[float]] = {}
_lock = threading.Lock()


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters)).hex()
    except (ValueError, TypeError):
        return False
    return algo == "pbkdf2_sha256" and hmac.compare_digest(got, digest)


def _server() -> dict:
    return config.load().get("server") or {}


def enabled() -> bool:
    return bool(_server().get("password_hash"))


def set_password(password: str | None) -> None:
    """Set (or with None/'' clear) the password; a new secret signs every browser out."""
    srv = dict(_server())
    srv["password_hash"] = hash_password(password) if password else ""
    srv["secret"] = secrets.token_hex(32)
    config.update({"server": srv})


def _key() -> bytes:
    srv = _server()
    secret = srv.get("secret")
    if not secret:
        secret = secrets.token_hex(32)
        config.update({"server": {**srv, "secret": secret}})
    return (secret + (srv.get("password_hash") or "")).encode()


def make_token() -> str:
    exp = str(int(time.time()) + LIFETIME_S)
    return exp + "." + hmac.new(_key(), exp.encode(), hashlib.sha256).hexdigest()


def valid_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp, sig = token.split(".", 1)
    if not exp.isdigit() or int(exp) < time.time():
        return False
    return hmac.compare_digest(sig, hmac.new(_key(), exp.encode(), hashlib.sha256).hexdigest())


def allow_attempt(ip: str) -> bool:
    """At most 8 password tries a minute from one address."""
    now = time.time()
    with _lock:
        recent = [t for t in _attempts.get(ip, []) if now - t < 60]
        recent.append(now)
        _attempts[ip] = recent
        return len(recent) <= 8


def is_local(scope: dict) -> bool:
    client = scope.get("client") or ("", 0)
    return client[0] in LOOPBACK or client[0].startswith("127.")


def _cookie(scope: dict) -> str | None:
    for k, v in scope.get("headers") or []:
        if k == b"cookie":
            for part in v.decode("latin-1").split(";"):
                name, _, val = part.strip().partition("=")
                if name == COOKIE:
                    return val
    return None


class AuthMiddleware:
    """ASGI middleware, so it guards HTTP and WebSocket requests alike."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not enabled() or is_local(scope) \
                or scope.get("path") in OPEN_PATHS or valid_token(_cookie(scope)):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        path = scope.get("path", "/")
        if path.startswith("/api/"):
            body = json.dumps({"detail": "log in first"}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await send({"type": "http.response.start", "status": 303, "headers": [(b"location", b"/login")]})
        await send({"type": "http.response.body", "body": b""})


def login_page(error: str = "") -> str:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>WholeBoard login</title><link rel="icon" href="/static/icon.svg"><link rel="stylesheet" href="/static/style.css">
<style>.login{{max-width:360px;margin:12vh auto;padding:24px;display:grid;gap:12px}}.login input{{padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text)}}</style>
</head><body><form class="login card" method="post" action="/api/login">
<h2>WholeBoard</h2><p class="muted">This WholeBoard is password protected. Enter the password set on the computer running it.</p>
{err}<input type="password" name="password" autofocus autocomplete="current-password" placeholder="Password">
<button class="btn primary" type="submit">Log in</button></form>
<script>try{{const t=localStorage.getItem("wb-theme");if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script></body></html>"""


def form_password(body: bytes) -> str:
    return (parse_qs(body.decode("utf-8", "replace")).get("password") or [""])[0]
