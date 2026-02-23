from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase

bp = Blueprint("_debug", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _clip(s: str, n: int = 300) -> str:
    s = (s or "")
    return s if len(s) <= n else s[:n] + "…"


def _admin_key() -> str:
    # Supports either ADMIN_API_KEY or X_ADMIN_KEY or similar
    return (os.getenv("ADMIN_API_KEY") or os.getenv("X_ADMIN_KEY") or os.getenv("ADMIN_KEY") or "").strip()


def _require_admin() -> Optional[Dict[str, Any]]:
    """
    Returns an error dict if not authorized, else None.
    """
    expected = _admin_key()
    if not expected:
        # If you don't set an admin key, we block debug endpoints by default.
        return {"ok": False, "error": "admin_key_not_configured"}

    got = (request.headers.get("X-Admin-Key") or "").strip()
    if not got or got != expected:
        return {"ok": False, "error": "unauthorized"}

    return None


def _has_column(table: str, col: str) -> bool:
    try:
        _sb().table(table).select(col).limit(1).execute()
        return True
    except Exception:
        return False


@bp.get("/_debug/otp/latest")
def debug_latest_otp():
    """
    Safe OTP debug endpoint:
      - DOES NOT expose OTP digits
      - DOES NOT query non-existent columns like web_otps.otp
      - Works whether supabase is callable or a client
    Query:
      /api/_debug/otp/latest?email=...  OR ?contact=...
    """
    auth_err = _require_admin()
    if auth_err:
        return jsonify(auth_err), 401

    otp_table = (os.getenv("WEB_OTP_TABLE") or "web_otps").strip()
    contact = (request.args.get("contact") or request.args.get("email") or "").strip().lower()

    if not contact:
        return jsonify({"ok": False, "error": "missing_contact", "hint": "Use ?contact= or ?email="}), 400

    # Determine safe columns to select (only those that exist)
    safe_cols = []
    for c in ["id", "contact", "purpose", "expires_at", "used", "used_at", "created_at", "code_hash"]:
        if _has_column(otp_table, c):
            safe_cols.append(c)

    if not safe_cols:
        return jsonify({"ok": False, "error": "otp_table_has_no_expected_columns", "table": otp_table}), 500

    try:
        q = (
            _sb()
            .table(otp_table)
            .select(",".join(safe_cols))
            .eq("contact", contact)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = (q.data or []) if hasattr(q, "data") else []
        row = rows[0] if rows else None
    except Exception as e:
        return jsonify(
            {
                "ok": False,
                "error": "debug_otp_failed",
                "root_cause": f"{type(e).__name__}: {_clip(str(e), 400)}",
                "table": otp_table,
                "selected_cols": safe_cols,
            }
        ), 500

    if not row:
        return jsonify({"ok": True, "found": False, "table": otp_table, "contact": contact}), 200

    # Never show OTP digits (we don't store it anyway). code_hash is safe.
    return jsonify({"ok": True, "found": True, "table": otp_table, "contact": contact, "row": row}), 200


@bp.get("/_debug/config")
def debug_config():
    auth_err = _require_admin()
    if auth_err:
        return jsonify(auth_err), 401

    keys = [
        "ENV",
        "WEB_AUTH_ENABLED",
        "WEB_AUTH_DEBUG",
        "WEB_DEV_RETURN_OTP",
        "WEB_OTP_TABLE",
        "WEB_TOKEN_TABLE",
        "WEB_OTP_TTL_MINUTES",
        "WEB_SESSION_TTL_DAYS",
        "MAIL_ENABLED",
        "MAIL_HOST",
        "MAIL_PORT",
        "MAIL_USER",
        "MAIL_FROM_EMAIL",
        "MAIL_FROM_NAME",
    ]
    out: Dict[str, Any] = {}
    for k in keys:
        v = os.getenv(k)
        if v is None:
            out[k] = None
        else:
            out[k] = str(v).strip()
    # never show passwords
    if os.getenv("MAIL_PASS") is not None:
        out["MAIL_PASS"] = "set" if str(os.getenv("MAIL_PASS")).strip() else ""

    return jsonify({"ok": True, "env": out}), 200
