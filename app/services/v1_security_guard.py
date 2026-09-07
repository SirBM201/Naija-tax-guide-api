from __future__ import annotations

"""NTG V1 application-level security, privacy and abuse guard.

Webhook authenticity is enforced by each provider route.  This layer avoids
accidentally throttling legitimate Paystack/Meta retries while retaining
application abuse controls for user-facing endpoints.
"""

import hmac
import ipaddress
import os
import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Tuple

from flask import jsonify, request

_LOCK = threading.Lock()
_BUCKETS: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)

SENSITIVE_RESPONSE_PREFIXES = (
    "/api/web/auth/", "/api/referrals/", "/api/billing/", "/api/payments/",
    "/api/accounts/", "/api/profile/",
)
PUBLIC_DIAGNOSTICS = {"/api/_boot", "/api/_diag", "/api/_debug_routes"}
WEBHOOK_PATH_MARKERS = ("/paystack/webhook", "/webhooks/paystack", "/whatsapp/webhook", "/telegram/webhook")


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except Exception:
        return default


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _valid_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except Exception:
        return ""


def _client_key() -> str:
    remote = _valid_ip(request.remote_addr or "") or "unknown"
    # X-Forwarded-For is attacker-controlled unless the deployment explicitly
    # opts in after confirming the request reaches Flask only through a trusted
    # reverse proxy. Secure default: ignore forwarded client-IP headers.
    if _truthy(os.getenv("NTG_TRUST_PROXY_HEADERS", "0")):
        forwarded = (request.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
        forwarded_ip = _valid_ip(forwarded)
        if forwarded_ip:
            return forwarded_ip
    return remote


def _is_provider_webhook() -> bool:
    path = request.path.lower()
    return any(marker in path for marker in WEBHOOK_PATH_MARKERS)


def _rate_policy() -> tuple[str, int, int] | None:
    path = request.path
    method = request.method.upper()
    if method == "OPTIONS" or _is_provider_webhook():
        # Provider routes perform cryptographic/token verification and must be
        # able to receive legitimate retry bursts. Do not apply per-IP user
        # throttling to them here.
        return None
    if "/web/auth/" in path:
        return "auth", _int_env("NTG_AUTH_RATE_LIMIT", 20), 60
    if path.endswith("/ask") or "/web/ask" in path or "/whatsapp" in path or "/telegram" in path:
        return "assistant", _int_env("NTG_ASSISTANT_HTTP_RATE_LIMIT", 30), 60
    if "payout-request" in path or "/billing/" in path or "/payments/" in path:
        return "money", _int_env("NTG_MONEY_RATE_LIMIT", 20), 60
    return None


def _limited(bucket: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    now = time.monotonic()
    key = (bucket, _client_key())
    cutoff = now - window_seconds
    with _LOCK:
        q = _BUCKETS[key]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            retry = max(1, int(window_seconds - (now - q[0]))) if q else window_seconds
            return True, retry
        q.append(now)
        return False, 0


def _diagnostic_allowed() -> bool:
    if request.path not in PUBLIC_DIAGNOSTICS:
        return True
    if _truthy(os.getenv("ENABLE_PUBLIC_DIAGNOSTICS", "0")):
        return True
    supplied = (request.headers.get("X-Admin-Key") or "").strip()
    expected = (os.getenv("ADMIN_KEY") or "").strip()
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


def install_v1_security_guard(app: Any) -> None:
    @app.before_request
    def _ntg_v1_security_before_request():
        if not _diagnostic_allowed():
            return jsonify({"ok": False, "error": "not_found"}), 404
        policy = _rate_policy()
        if policy:
            bucket, limit, window = policy
            blocked, retry_after = _limited(bucket, limit, window)
            if blocked:
                response = jsonify({"ok": False, "error": "rate_limited", "retry_after_seconds": retry_after})
                response.status_code = 429
                response.headers["Retry-After"] = str(retry_after)
                return response
        return None

    @app.after_request
    def _ntg_v1_security_after_request(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # API responses default to no active content. HTML callback/return pages
        # may render their own stricter CSP instead of being broken globally.
        if response.mimetype != "text/html":
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if request.is_secure or (request.headers.get("X-Forwarded-Proto") or "").lower() == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if any(request.path.startswith(prefix) for prefix in SENSITIVE_RESPONSE_PREFIXES):
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["Pragma"] = "no-cache"
        return response
