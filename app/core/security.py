# app/core/security.py
from __future__ import annotations

from functools import wraps
from typing import Callable, Optional, Tuple

from flask import jsonify, request

from app.core.config import ADMIN_API_KEY


def _extract_key_from_headers() -> str:
    """
    Accept:
      - X-Admin-Key: <key>
      - Authorization: Bearer <key>
    """
    key = (request.headers.get("X-Admin-Key") or "").strip()
    if key:
        return key

    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()

    return ""


def check_admin_key() -> Tuple[bool, str]:
    """
    Returns (ok, reason).
    This is useful if you ever want to guard inside a route without decorators.
    """
    if not ADMIN_API_KEY:
        return False, "admin_key_not_configured"

    key = _extract_key_from_headers()
    if not key:
        return False, "missing_admin_key"

    if key != ADMIN_API_KEY:
        return False, "invalid_admin_key"

    return True, "ok"


def require_admin_key(fn: Callable):
    """
    ✅ REAL DECORATOR (use as @require_admin_key)

    If ADMIN_API_KEY is empty -> 503 (forces production config).
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        ok, reason = check_admin_key()
        if not ok:
            status = 503 if reason == "admin_key_not_configured" else 401
            return jsonify({"ok": False, "error": reason}), status
        return fn(*args, **kwargs)

    return wrapper
