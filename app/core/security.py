# app/core/security.py
from __future__ import annotations

from functools import wraps
from flask import request, jsonify

from .config import ADMIN_API_KEY


def require_admin_key(fn):
    """
    Protect admin-only routes using a static key.

    Header options accepted:
      - X-Admin-Key: <key>
      - Authorization: Bearer <key>

    If ADMIN_API_KEY is empty, this returns 503 to force you to configure it in production.
    (If you prefer "open access when blank", tell me and I will adjust.)
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not ADMIN_API_KEY:
            return jsonify({"ok": False, "error": "admin_key_not_configured"}), 503

        key = (request.headers.get("X-Admin-Key") or "").strip()
        if not key:
            auth = (request.headers.get("Authorization") or "").strip()
            if auth.lower().startswith("bearer "):
                key = auth.split(" ", 1)[1].strip()

        if key != ADMIN_API_KEY:
            return jsonify({"ok": False, "error": "invalid_admin_key"}), 401

        return fn(*args, **kwargs)

    return wrapper
