"""Request guards: Host check, cross-site blocking, optional API key, rate limits.

FansCut has no accounts. What it does have:

- A Host check. Bound to loopback (the default), only loopback names (plus
  server.allowed_hosts) are served, so a web page cannot DNS-rebind its own
  domain onto 127.0.0.1 and drive the API.
- Cross-site blocking. A state-changing request a browser marks as coming from
  another site (Sec-Fetch-Site / Origin) is refused, so a page you visit cannot
  POST to your FansCut. Tools that send no Origin (curl, Sonarr) are unaffected.
- An API key, generated on first start. Webhooks always need it. With
  server.auth = "api_key" every /api call needs it too: as an X-Api-Key header,
  or the cookie POST /api/auth/login sets. server.viewer_key is a second key
  that may only read.
- Per-client rate limits on the endpoints that start real work.
"""

from __future__ import annotations

import hmac
import ipaddress
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

COOKIE = "fanscut_key"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
# Host names always accepted besides loopback; tests add Starlette's "testserver".
ALWAYS_ALLOWED: set[str] = set()


def new_key() -> str:
    return secrets.token_urlsafe(24)


def _same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


def _hostname(host_header: str) -> str:
    h = host_header.strip().lower()
    if h.startswith("["):  # [::1]:6464
        return h[1:].split("]", 1)[0]
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def _is_loopback(host: str) -> bool:
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_allowed(host_header: str, bound: str, allowed: list[str]) -> bool:
    """Bound to loopback: only loopback names and `allowed`. Bound wider: anything,
    unless `allowed` lists names, then those plus loopback."""
    name = _hostname(host_header)
    if not name:
        return False
    if _is_loopback(name) or name in ALWAYS_ALLOWED:
        return True
    names = {a.strip().lower() for a in allowed if a.strip()}
    if "*" in names:
        return True
    if names:
        return name in names
    return not _is_loopback(bound)


def cross_site(request: Request) -> bool:
    """True when a browser says this request comes from another site."""
    site = request.headers.get("sec-fetch-site", "")
    if site == "cross-site":
        return True
    origin = request.headers.get("origin", "")
    if not origin or origin == "null":
        return origin == "null"
    return (urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower())


def presented_key(request: Request) -> str:
    """The key a request carries: X-Api-Key, ?apikey=, Basic auth password, or the cookie."""
    key = request.headers.get("x-api-key") or request.query_params.get("apikey") or ""
    if not key:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            import base64
            import binascii

            try:
                key = base64.b64decode(auth[6:]).decode("utf-8", "replace").partition(":")[2]
            except (binascii.Error, ValueError):
                key = ""
        elif auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    return key or request.cookies.get(COOKIE, "")


def role_of(key: str, server: Any) -> str | None:
    """'admin' for the API key, 'viewer' for the viewer key, else None."""
    if _same(key, server.api_key):
        return "admin"
    if _same(key, server.viewer_key):
        return "viewer"
    return None


# Paths under /api that need no key even with auth on: the login handshake,
# which also tells the UI whether to ask for one.
OPEN_PATHS = ("/api/auth/",)


class GuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, get_cfg: Callable[[], Any]):
        super().__init__(app)
        self.get_cfg = get_cfg

    async def dispatch(self, request: Request,
                       call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        server = self.get_cfg().server
        if not host_allowed(request.headers.get("host", ""), server.host, server.allowed_hosts):
            return JSONResponse({"detail": "unknown Host header; add it to server.allowed_hosts",
                                 "code": "bad_host"}, status_code=421)
        path = request.url.path
        if request.method not in SAFE_METHODS and cross_site(request):
            return JSONResponse({"detail": "cross-site request refused", "code": "cross_site"},
                                status_code=403)
        if path.startswith("/api/") and not path.startswith("/api/webhooks/") \
                and server.auth == "api_key" and not path.startswith(OPEN_PATHS):
            role = role_of(presented_key(request), server)
            if role is None:
                return JSONResponse({"detail": "API key required", "code": "auth"},
                                    status_code=401)
            if role == "viewer" and request.method not in SAFE_METHODS:
                return JSONResponse({"detail": "the viewer key can only read",
                                     "code": "forbidden"}, status_code=403)
        return await call_next(request)


def require_key(request: Request, server: Any) -> None:
    """For webhooks: the admin API key must be presented, whatever server.auth says."""
    if role_of(presented_key(request), server) != "admin":
        raise HTTPException(401, "API key required: add ?apikey=<key> to the webhook URL")


# -- rate limits ---------------------------------------------------------------
class _Bucket:
    __slots__ = ("stamp", "tokens")

    def __init__(self, tokens: float, stamp: float):
        self.tokens, self.stamp = tokens, stamp


_buckets: dict[tuple[str, str], _Bucket] = {}
_lock = threading.Lock()


def rate_limit(name: str, burst: int, per_seconds: float) -> Callable[[Request], None]:
    """A FastAPI dependency: at most `burst` calls per `per_seconds` for each client."""
    rate = burst / per_seconds

    def check(request: Request) -> None:
        who = request.client.host if request.client else "?"
        now = time.monotonic()
        with _lock:
            b = _buckets.get((name, who))
            if b is None:
                b = _buckets[(name, who)] = _Bucket(float(burst), now)
            b.tokens = min(float(burst), b.tokens + (now - b.stamp) * rate)
            b.stamp = now
            if b.tokens < 1:
                wait = int((1 - b.tokens) / rate) + 1
                raise HTTPException(429, f"too many requests; try again in {wait}s",
                                    headers={"Retry-After": str(wait)})
            b.tokens -= 1

    return check


def reset_rate_limits() -> None:
    with _lock:
        _buckets.clear()
