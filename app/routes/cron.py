# app/routes/cron.py
from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, jsonify, request

from app.core.security import require_admin_key
from app.core.supabase_client import supabase as sb  # must be the CLIENT object, not a function


bp = Blueprint("cron", __name__)


def _json() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


@bp.post("/internal/cron/expire-subscriptions")
def expire_subscriptions():
    guard = require_admin_key()
    if guard is not None:
        return guard

    payload = _json()
    batch_limit = payload.get("batch_limit", 1000)
    try:
        batch_limit = int(batch_limit)
    except Exception:
        batch_limit = 1000

    # Prefer DB RPC (single source of truth), not Python service imports
    try:
        res = sb.rpc("expire_overdue_subscriptions", {"batch_limit": batch_limit}).execute()
        data = getattr(res, "data", None)
        if isinstance(data, list):
            data = data[0] if data else {}
        return jsonify({"ok": True, "result": data}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/internal/cron/expire-credits")
def expire_credits():
    guard = require_admin_key()
    if guard is not None:
        return guard

    payload = _json()
    batch_limit = payload.get("batch_limit", 5000)
    try:
        batch_limit = int(batch_limit)
    except Exception:
        batch_limit = 5000

    try:
        # IMPORTANT: call the function name you actually created (see SQL section below)
        res = sb.rpc("expire_ai_credits", {"batch_limit": batch_limit}).execute()
        data = getattr(res, "data", None)
        if isinstance(data, list):
            data = data[0] if data else {}
        return jsonify({"ok": True, "result": data}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
