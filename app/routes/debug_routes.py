# app/routes/debug_routes.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request, g

from app.core.auth import require_auth_plus
from app.services.subscriptions_service import get_subscription_status

bp = Blueprint("debug_routes", __name__)

ADMIN_KEY = (os.getenv("ADMIN_KEY", "") or "").strip()


def _is_admin(req) -> bool:
    if not ADMIN_KEY:
        return False
    key = (req.headers.get("X-Admin-Key", "") or "").strip()
    return bool(key) and key == ADMIN_KEY


def _safe_env() -> Dict[str, Any]:
    """
    SAFE debug info only: never return secrets.
    """
    api_prefix = (os.getenv("API_PREFIX", "") or "").strip() or "/api"
    cors_origins = (os.getenv("CORS_ORIGINS", "") or "").strip()
    return {
        "env": (os.getenv("ENV", "") or os.getenv("FLASK_ENV", "") or "prod").strip() or "prod",
        "api_prefix": api_prefix,
        "cors_origins_set": bool(cors_origins),
        "cookie_auth_enabled": (os.getenv("COOKIE_AUTH_ENABLED", "") or "").strip(),
        "web_auth_enabled": (os.getenv("WEB_AUTH_ENABLED", "") or "").strip(),
        "admin_key_configured": bool(ADMIN_KEY),
    }


@bp.get("/_debug/config")
def debug_config():
    if not _is_admin(request):
        return jsonify({"ok": False, "error": "forbidden", "message": "Admin key required."}), 403

    return jsonify(
        {
            "ok": True,
            "safe_env": _safe_env(),
            "admin_auth": {
                "configured": bool(ADMIN_KEY),
                "provided": bool((request.headers.get("X-Admin-Key", "") or "").strip()),
                "valid": _is_admin(request),
            },
        }
    ), 200


@bp.get("/_debug/subscription")
def debug_subscription():
    if not _is_admin(request):
        return jsonify({"ok": False, "error": "forbidden", "message": "Admin key required."}), 403

    account_id = (request.args.get("account_id") or "").strip()
    if not account_id:
        return jsonify({"ok": False, "error": "missing_account_id"}), 400

    status = get_subscription_status(account_id)
    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "computed_status": status,
            "safe_env": _safe_env(),
        }
    ), 200


@bp.get("/_debug/whoami")
@require_auth_plus
def debug_whoami():
    return jsonify(
        {
            "ok": True,
            "account_id": getattr(g, "account_id", None),
            "auth_mode": getattr(g, "auth_mode", None),
        }
    ), 200
