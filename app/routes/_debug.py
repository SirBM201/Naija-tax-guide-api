# app/routes/_debug.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

bp = Blueprint("_debug", __name__)

def _admin_ok(req) -> bool:
    expected = (os.getenv("ADMIN_KEY") or "").strip()
    got = (req.headers.get("X-Admin-Key") or "").strip()
    return bool(expected) and got == expected

@bp.get("/_debug/ping")
def ping():
    # This endpoint exists ONLY to prove the blueprint is loaded.
    if not _admin_ok(request):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return jsonify({"ok": True, "ping": "pong"}), 200

@bp.get("/_debug/subscription_health")
def subscription_health():
    if not _admin_ok(request):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    # Lazy import so app can still boot even if service changes
    from app.services.subscriptions_service import debug_expose_subscription_health

    account_id = (request.args.get("account_id") or "").strip() or None
    out = debug_expose_subscription_health(account_id=account_id)
    return jsonify(out), (200 if out.get("ok") else 400)
