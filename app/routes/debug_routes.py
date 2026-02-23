# app/routes/debug_routes.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from app.services.subscription_status_service import get_subscription_status

bp = Blueprint("debug", __name__)

ADMIN_KEY = (os.getenv("ADMIN_KEY", "") or "").strip()


def _is_admin(req) -> bool:
    if not ADMIN_KEY:
        return False
    key = (req.headers.get("X-Admin-Key", "") or "").strip()
    return bool(key) and key == ADMIN_KEY


def _safe_env() -> Dict[str, Any]:
    # Never return secrets/values, only booleans / safe strings
    api_prefix = (os.getenv("API_PREFIX", "") or "/api").strip() or "/api"
    return {
        "env": (os.getenv("ENV", "") or os.getenv("FLASK_ENV", "") or "prod").strip() or "prod",
        "api_prefix": api_prefix,
        "admin_key_configured": bool(ADMIN_KEY),
        "cookie_auth_enabled": (os.getenv("COOKIE_AUTH_ENABLED", "") or "").strip() or "0",
        "web_auth_enabled": (os.getenv("WEB_AUTH_ENABLED", "") or "").strip() or "0",
        "cors_origins_set": bool((os.getenv("CORS_ORIGINS", "") or "").strip()),
    }


@bp.get("/_debug/config")
def debug_config():
    if not _is_admin(request):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    provided = bool((request.headers.get("X-Admin-Key", "") or "").strip())
    return jsonify(
        {
            "ok": True,
            "admin_auth": {"configured": bool(ADMIN_KEY), "provided": provided, "valid": True},
            "safe_env": _safe_env(),
        }
    ), 200


@bp.get("/_debug/subscription")
def debug_subscription():
    if not _is_admin(request):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    account_id = (request.args.get("account_id") or request.args.get("user_id") or "").strip()
    if not account_id:
        return jsonify({"ok": False, "error": "missing_account_id"}), 400

    computed = get_subscription_status(account_id)

    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "computed_status": computed,
            "safe_env": _safe_env(),
        }
    ), 200
