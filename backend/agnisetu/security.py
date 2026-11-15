"""Edge protections implemented as one pure ASGI middleware, plus the admin token check.

The middleware gives every request an id, enforces the body size limit while the body is being
read, applies per client rate limits, adds security headers and writes one structured access
log line. It never reads or logs request bodies.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings, get_settings
from .logging_setup import request_id_var

log = logging.getLogger("agnisetu.access")

TILE_HOSTS = ("https://gibs.earthdata.nasa.gov", "https://tile.openstreetmap.org")

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "style-src-attr 'unsafe-inline'",
        f"img-src 'self' data: blob: {' '.join(TILE_HOSTS)}",
        f"connect-src 'self' {' '.join(TILE_HOSTS)}",
        "worker-src 'self' blob:",
        "child-src 'self' blob:",
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"geolocation=(), camera=(), microphone=(), payment=(), usb=(), interest-cohort=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
)


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Token bucket per (client, class). Memory is bounded by evicting the least recently used client."""

    def __init__(self, max_clients: int = 20_000) -> None:
        self._buckets: OrderedDict[tuple[str, str], _Bucket] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_clients

    def allow(self, client: str, klass: str, per_minute: int) -> tuple[bool, float]:
        now = time.monotonic()
        rate = per_minute / 60.0
        capacity = float(max(1, per_minute))
        key = (client, klass)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(capacity, now)
                self._buckets[key] = bucket
            else:
                bucket.tokens = min(capacity, bucket.tokens + (now - bucket.updated) * rate)
                bucket.updated = now
                self._buckets.move_to_end(key)
            while len(self._buckets) > self._max:
                self._buckets.popitem(last=False)
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0
            return False, (1.0 - bucket.tokens) / rate

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class _BodyTooLarge(Exception):
    pass


def _json_error(status: int, code: str, message: str, request_id: str) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    body = json.dumps({"error": {"code": code, "message": message, "request_id": request_id}}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    return body, headers


class EdgeMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings | None = None, limiter: RateLimiter | None = None) -> None:
        self.app = app
        self.settings = settings or get_settings()
        self.limiter = limiter or RateLimiter()

    def _client(self, scope: Scope) -> str:
        if self.settings.trust_forwarded_for:
            for name, value in scope.get("headers", []):
                if name == b"x-forwarded-for":
                    first = value.decode("latin-1").split(",")[0].strip()
                    if first:
                        return first[:64]
        client = scope.get("client")
        return client[0] if client else "unknown"

    @staticmethod
    def _class(method: str, path: str) -> str | None:
        if not path.startswith("/api/"):
            return None
        if "/admin/" in path:
            return "admin"
        if method == "POST":
            return "heavy"
        return "general"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        client = self._client(scope)
        status_holder = {"status": 500}
        overflow = {"hit": False, "answered": False}
        limit = self.settings.max_body_bytes
        is_api = path.startswith("/api/")

        async def send_wrapper(message: Message) -> None:
            if overflow["hit"] and not message.get("_edge"):
                # The framework turns a failed body read into its own error response. Replace
                # it with the correct status for an oversized body.
                if not overflow["answered"]:
                    overflow["answered"] = True
                    await reject(413, "payload_too_large", f"Request bodies are limited to {limit} bytes.")
                return
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"server"]
                present = {k.lower() for k, _ in headers}
                for name, value in SECURITY_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                if self.settings.enable_hsts:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                headers.append((b"x-request-id", request_id.encode()))
                if is_api:
                    duration = (time.perf_counter() - started) * 1000.0
                    headers.append((b"server-timing", f"app;dur={duration:.1f}".encode()))
                    if b"cache-control" not in present:
                        headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send({k: v for k, v in message.items() if k != "_edge"})

        async def reject(status: int, code: str, message: str, extra: list[tuple[bytes, bytes]] | None = None) -> None:
            body, headers = _json_error(status, code, message, request_id)
            await send_wrapper(
                {"type": "http.response.start", "status": status, "headers": headers + (extra or []), "_edge": True}
            )
            await send_wrapper({"type": "http.response.body", "body": body, "_edge": True})

        try:
            klass = self._class(method, path)
            if klass is not None:
                per_minute = {
                    "general": self.settings.rate_limit_per_minute,
                    "heavy": self.settings.heavy_rate_limit_per_minute,
                    "admin": 10,
                }[klass]
                allowed, retry_after = self.limiter.allow(client, klass, per_minute)
                if not allowed:
                    await reject(
                        429,
                        "rate_limited",
                        "Too many requests. Please slow down and retry shortly.",
                        [(b"retry-after", str(max(1, int(retry_after + 0.999))).encode())],
                    )
                    return

            declared = None
            for name, value in scope.get("headers", []):
                if name == b"content-length":
                    try:
                        declared = int(value)
                    except ValueError:
                        await reject(400, "bad_request", "Invalid Content-Length header.")
                        return
            if declared is not None and declared > limit:
                await reject(413, "payload_too_large", f"Request bodies are limited to {limit} bytes.")
                return

            received = 0

            async def receive_wrapper() -> Message:
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > limit:
                        overflow["hit"] = True
                        raise _BodyTooLarge
                return message

            try:
                await self.app(scope, receive_wrapper, send_wrapper)
            except _BodyTooLarge:
                if not overflow["answered"]:
                    overflow["answered"] = True
                    await reject(413, "payload_too_large", f"Request bodies are limited to {limit} bytes.")
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000.0, 1)
            if is_api:
                log.info(
                    "%s %s %s",
                    method,
                    path,
                    status_holder["status"],
                    extra={
                        "client": client,
                        "status": status_holder["status"],
                        "duration_ms": duration_ms,
                        "method": method,
                        "path": path,
                    },
                )
            request_id_var.reset(token)


def require_admin(
    request: Request,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
) -> None:
    """Dependency for administrative routes. They do not exist when no token is configured."""
    settings: Settings = request.app.state.settings
    if settings.admin_token is None:
        raise HTTPException(status_code=404, detail="Not found")
    supplied = x_admin_token
    if supplied is None and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied:
        raise HTTPException(status_code=401, detail="Admin token required", headers={"WWW-Authenticate": "Bearer"})
    expected = settings.admin_token.get_secret_value().encode()
    if not hmac.compare_digest(supplied.encode(), expected):
        raise HTTPException(status_code=403, detail="Invalid admin token")
