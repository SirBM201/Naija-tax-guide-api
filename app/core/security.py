# app/core/security.py
from __future__ import annotations

from functools import wraps
from flask import request, jsonify

from .config import ADMIN_API_KEY


def _extract_key_from_headers() -> str:
    key = (request.headers.get("X-Admin-Key") or "").strip()
    if key:
        return key

    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()

    return ""


def require_admin_key(fn):
    """
    Decorator to protect admin-only routes using a static key.

    Header options:
      - X-Admin-Key: <key>
      - Authorization: Bearer <key>

    Behavior:
      - If ADMIN_API_KEY is empty -> returns 503 (forces you to configure it in production).
      - If provided key mismatches -> 401.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        # If you want "open access when blank", tell me and I will change this.
        if not ADMIN_API_KEY:
            return jsonify({"ok": False, "error": "admin_key_not_configured"}), 503

        key = _extract_key_from_headers()
        if key != ADMIN_API_KEY:
            return jsonify({"ok": False, "error": "invalid_admin_key"}), 401

        return fn(*args, **kwargs)

    return wrapper
