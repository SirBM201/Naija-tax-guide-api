# app/routes/debug_routes.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request, g

from app.core.auth import require_auth_plus
from app.core.supabase_client import supabase
from app.services.subscriptions_service import get_subscription_status

bp = Blueprint("debug_routes", __name__)

ADMIN_KEY = (os.getenv("ADMIN_KEY", "") or "").strip()


def _admin_key_configured() -> bool:
    return bool(ADMIN_KEY)


def _admin_key_provided(req) -> bool:
    return bool((req.headers.get("X-Admin-Key", "") or "").strip())


def _is_admin(req) -> bool:
    if not ADMIN_KEY:
        return False
    key = (req.headers.get("X-Admin-Key", "") or "").strip()
    return bool(key) and key == ADMIN_KEY


def _forbidden(payload: Dict[str, Any], status_code: int = 403):
    return jsonify(payload), status_code


def _safe_env_snapshot() -> Dict[str, Any]:
    """
    Safe, non-secret runtime hints. Never include passwords/tokens/keys.
    """
    return {
        "env": (os.getenv("ENV", "") or os.getenv("FLASK_ENV", "") or "unknown"),
        "cookie_auth_enabled": (os.getenv("COOKIE_AUTH_ENABLED", "") or "").strip(),
        "web_auth_enabled": (os.getenv("WEB_AUTH_ENABLED", "") or "").strip(),
        "cors_origins_set": bool((os.getenv("CORS_ORIGINS", "") or "").strip()),
        "api_prefix": (os.getenv("API_PREFIX", "") or "").strip(),
        "admin_key_configured": _admin_key_configured(),
    }


@bp.get("/_debug/ping")
def debug_ping():
    return jsonify({"ok": True, "service": "naija-tax-guide-api", "debug": True}), 200


@bp.get("/_debug/whoami")
@require_auth_plus
def debug_whoami():
    """
    Auth-required: confirms what account_id the backend resolved from cookie/bearer.
    """
    return jsonify(
        {
            "ok": True,
            "account_id": getattr(g, "account_id", None),
            "auth_mode": getattr(g, "auth_mode", None),
        }
    ), 200


@bp.get("/_debug/config")
def debug_config():
    """
    Admin-only: safe config snapshot and admin auth diagnostics.
    """
    if not _admin_key_configured():
        return _forbidden(
            {
                "ok": False,
                "error": "admin_key_not_configured",
                "message": "ADMIN_KEY env var is not set on the server.",
                "admin_auth": {
                    "configured": False,
                    "provided": _admin_key_provided(request),
                    "valid": False,
                },
            },
            500,
        )

    if not _is_admin(request):
        return _forbidden(
            {
                "ok": False,
                "error": "forbidden",
                "message": "Admin key required." if not _admin_key_provided(request) else "Admin key invalid.",
                "admin_auth": {
                    "configured": True,
                    "provided": _admin_key_provided(request),
                    "valid": False,
                },
            },
            403,
        )

    return jsonify(
        {
            "ok": True,
            "admin_auth": {"configured": True, "provided": True, "valid": True},
            "safe_env": _safe_env_snapshot(),
        }
    ), 200


def _get_latest_subscription_row(account_id: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            supabase.table("subscriptions")
            .select("id, user_id, plan_code, status, expires_at, grace_until, created_at, updated_at")
            .eq("user_id", account_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        return rows[0] if rows else None
    except Exception:
        return None


@bp.get("/_debug/subscription")
def debug_subscription():
    """
    Admin-only: inspect subscription state for a target account_id.
    Never returns secrets; only DB fields needed for diagnosis.
    Query:
      ?account_id=<uuid>
    """
    account_id = (request.args.get("account_id") or "").strip()

    if not _admin_key_configured():
        return _forbidden(
            {
                "ok": False,
                "error": "admin_key_not_configured",
                "message": "ADMIN_KEY env var is not set on the server.",
                "hint": "Set ADMIN_KEY on Koyeb, redeploy, then retry.",
            },
            500,
        )

    if not _is_admin(request):
        return _forbidden(
            {
                "ok": False,
                "error": "forbidden",
                "message": "Admin key required." if not _admin_key_provided(request) else "Admin key invalid.",
            },
            403,
        )

    if not account_id:
        return jsonify({"ok": False, "error": "missing_account_id"}), 400

    status = get_subscription_status(account_id)
    row = _get_latest_subscription_row(account_id)

    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "computed_status": status,
            "latest_row": row,
            "safe_env": _safe_env_snapshot(),
        }
    ), 200
