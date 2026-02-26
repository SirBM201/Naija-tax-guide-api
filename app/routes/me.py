# app/routes/me.py
from __future__ import annotations

"""
/me endpoint (HARDENED)

✅ Returns canonical accounts.account_id (NOT accounts.id)
✅ Auto-repairs accounts.account_id if NULL (sets to id)
✅ Strong failure exposers

NOTE:
This endpoint is only useful if you truly use Supabase Auth sessions.
If your current web login uses OTP->web_tokens cookie, use that instead of /me.
"""

from flask import Blueprint, jsonify
from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user

bp = Blueprint("me", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


@bp.get("/me")
def me():
    user = get_current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    uid = user["id"]
    sb = _sb()

    # accounts table must have supabase_user_id column (text/uuid)
    try:
        res = sb.table("accounts").select("id,account_id").eq("supabase_user_id", uid).limit(1).execute()
        row = (res.data[0] if getattr(res, "data", None) else None) or None
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "accounts_lookup_failed",
            "root_cause": f"{type(e).__name__}: {str(e)[:220]}",
            "fix": "Check accounts table/RLS and that supabase_user_id column exists.",
        }), 500

    if row:
        account_id = (row.get("account_id") or "").strip()
        row_id = (row.get("id") or "").strip()

        # auto-repair if needed
        if not account_id and row_id:
            try:
                sb.table("accounts").update({"account_id": row_id}).eq("id", row_id).execute()
                account_id = row_id
            except Exception as e:
                return jsonify({
                    "ok": False,
                    "error": "account_id_repair_failed",
                    "root_cause": f"{type(e).__name__}: {str(e)[:220]}",
                    "fix": "Run SQL: update accounts set account_id = id where account_id is null; then enforce unique index.",
                    "details": {"row_id": row_id},
                }), 500

        if not account_id:
            return jsonify({
                "ok": False,
                "error": "account_id_missing",
                "root_cause": "accounts row exists but account_id is null/empty",
                "fix": "Ensure accounts.account_id is populated (trigger or repair).",
                "details": {"row_id": row_id},
            }), 500

        return jsonify({"ok": True, "account_id": account_id, "user_id": uid})

    # Create account if missing (one-time)
    try:
        created = sb.table("accounts").insert({"supabase_user_id": uid, "provider": "web"}).select("id,account_id").execute()
        row = (created.data[0] if getattr(created, "data", None) else None) or None
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "account_create_failed",
            "root_cause": f"{type(e).__name__}: {str(e)[:220]}",
            "fix": "Check accounts insert permissions and required columns.",
        }), 500

    if not row:
        return jsonify({
            "ok": False,
            "error": "account_create_failed",
            "root_cause": "insert returned no row",
            "fix": "Use .select('id,account_id') on insert and ensure Supabase returns inserted rows.",
        }), 500

    row_id = (row.get("id") or "").strip()
    account_id = (row.get("account_id") or "").strip()

    if not account_id and row_id:
        try:
            sb.table("accounts").update({"account_id": row_id}).eq("id", row_id).execute()
            account_id = row_id
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": "account_id_repair_failed",
                "root_cause": f"{type(e).__name__}: {str(e)[:220]}",
                "fix": "Ensure accounts.account_id is writable and not blocked by RLS.",
                "details": {"row_id": row_id},
            }), 500

    if not account_id:
        return jsonify({
            "ok": False,
            "error": "account_id_missing",
            "root_cause": "created row but account_id is still empty",
            "fix": "Ensure accounts.account_id exists and is populated.",
            "details": {"row_id": row_id},
        }), 500

    return jsonify({"ok": True, "account_id": account_id, "user_id": uid})
