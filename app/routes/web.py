# app/routes/me.py
from flask import Blueprint, jsonify
from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user

bp = Blueprint("me", __name__)


@bp.get("/me")
def me():
    user = get_current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    sb = supabase()
    uid = user["id"]

    # Prefer global account id: accounts.account_id
    res = sb.table("accounts").select("id,account_id").eq("supabase_user_id", uid).limit(1).execute()
    if res.data:
        row = res.data[0]
        gid = (row.get("account_id") or row.get("id") or "")
        return jsonify({"ok": True, "account_id": gid, "user_id": uid})

    # Create account if missing
    created = sb.table("accounts").insert({"supabase_user_id": uid, "provider": "web"}).select("id,account_id").execute()
    row = (created.data or [{}])[0]
    gid = (row.get("account_id") or row.get("id") or "")

    # Failure exposer if something odd happens
    if not gid:
        return jsonify({
            "ok": False,
            "error": "account_create_failed",
            "debug": {
                "reason": "missing_id_and_account_id",
                "suggestion": "Ensure accounts has id default and/or account_id backfill trigger.",
            }
        }), 500

    return jsonify({"ok": True, "account_id": gid, "user_id": uid})
