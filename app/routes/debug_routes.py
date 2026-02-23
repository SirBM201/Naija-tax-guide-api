# app/routes/debug_routes.py
from __future__ import annotations

import os
from flask import Blueprint, jsonify, current_app, request

from app.services.subscription_status_service import get_subscription_status

bp = Blueprint("debug_routes", __name__)

ADMIN_KEY = (os.getenv("ADMIN_KEY", "") or "").strip()


def _admin_key_configured() -> bool:
    return bool(ADMIN_KEY)


def _is_admin(req) -> bool:
    if not ADMIN_KEY:
        return False
    key = (req.headers.get("X-Admin-Key", "") or "").strip()
    return bool(key) and key == ADMIN_KEY


@bp.get("/_routes")
def list_routes():
    out = []
    for r in current_app.url_map.iter_rules():
        if r.rule.startswith("/static"):
            continue
        out.append(
            {
                "rule": r.rule,
                "methods": sorted([m for m in r.methods if m not in ("HEAD", "OPTIONS")]),
            }
        )
    return jsonify({"ok": True, "routes": out})


@bp.get("/_debug/config")
def debug_config():
    if not _admin_key_configured():
        return jsonify({"ok": False, "error": "admin_key_not_configured"}), 500

    provided = bool((request.headers.get("X-Admin-Key", "") or "").strip())
    valid = _is_admin(request)

    if not valid:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "forbidden",
                    "message": "Admin key required." if not provided else "Admin key invalid.",
                }
            ),
            403,
        )

    safe_env = {
        "env": (os.getenv("ENV", "") or os.getenv("FLASK_ENV", "") or "prod"),
        "api_prefix": (os.getenv("API_PREFIX", "") or "/api"),
        "cors_origins_set": bool((os.getenv("CORS_ORIGINS", "") or "").strip()),
        "cookie_auth_enabled": (os.getenv("COOKIE_AUTH_ENABLED", "") or ""),
        "web_auth_enabled": (os.getenv("WEB_AUTH_ENABLED", "") or ""),
        "subscriptions_table": (os.getenv("SUBSCRIPTIONS_TABLE", "") or "user_subscriptions"),
        "admin_key_configured": True,
    }

    return jsonify(
        {
            "ok": True,
            "admin_auth": {"configured": True, "provided": provided, "valid": valid},
            "safe_env": safe_env,
        }
    )


@bp.get("/_debug/subscription")
def debug_subscription():
    if not _admin_key_configured():
        return jsonify({"ok": False, "error": "admin_key_not_configured"}), 500

    provided = bool((request.headers.get("X-Admin-Key", "") or "").strip())
    valid = _is_admin(request)

    if not valid:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "forbidden",
                    "message": "Admin key required." if not provided else "Admin key invalid.",
                }
            ),
            403,
        )

    account_id = (request.args.get("account_id", "") or "").strip()
    status = get_subscription_status(account_id)
    return jsonify({"ok": True, "account_id": account_id, "computed_status": status})
