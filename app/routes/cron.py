# app/routes/cron.py
from __future__ import annotations

import traceback
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from ..core.security import require_admin_key
from ..core.supabase_client import supabase
from ..services.subscriptions_service import expire_overdue_subscriptions

bp = Blueprint("cron", __name__)

def _want_debug() -> bool:
    return (request.headers.get("X-Debug") or "").strip() == "1"

def _ok(payload: Dict[str, Any], status: int = 200):
    return jsonify(payload), status

def _fail(where: str, e: Exception, status: int = 500):
    payload: Dict[str, Any] = {
        "ok": False,
        "where": where,
        "error": type(e).__name__,
        "message": (str(e) or "")[:500],
    }
    if _want_debug():
        payload["debug"] = {
            "path": request.path,
            "method": request.method,
            "headers": {
                "content_type": request.content_type,
                "has_admin_key": bool(request.headers.get("X-Admin-Key")),
            },
            "trace": traceback.format_exc(),
            "json": request.get_json(silent=True),
        }
    return _ok(payload, status)

def _get_batch_limit(default: int) -> int:
    body = request.get_json(silent=True) or {}
    raw = body.get("batch_limit", body.get("batchLimit", default))
    try:
        v = int(raw)
    except Exception:
        v = default
    # prevent silly values
    if v < 1:
        v = 1
    if v > 20000:
        v = 20000
    return v

@bp.post("/internal/cron/expire-subscriptions")
def expire_subscriptions():
    guard = require_admin_key()
    if guard is not None:
        return guard

    try:
        batch_limit = _get_batch_limit(1000)
        result = expire_overdue_subscriptions(batch_limit=batch_limit)
        return _ok({"ok": True, "batch_limit": batch_limit, "result": result}, 200)
    except Exception as e:
        return _fail("expire_subscriptions", e, 500)

@bp.post("/internal/cron/expire-credits")
def expire_credits():
    guard = require_admin_key()
    if guard is not None:
        return guard

    try:
        batch_limit = _get_batch_limit(5000)

        # IMPORTANT: supabase is a client object (not callable)
        res = supabase.rpc("expire_ai_credits", {"batch_limit": batch_limit}).execute()

        data = getattr(res, "data", None)
        if isinstance(data, list):
            data = data[0] if data else {}

        return _ok({"ok": True, "batch_limit": batch_limit, "result": data}, 200)
    except Exception as e:
        return _fail("expire_credits", e, 500)
