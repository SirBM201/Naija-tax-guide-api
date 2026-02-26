from flask import Blueprint, request, jsonify
from app.services.web_auth_service import require_web_session
from app.core.supabase_client import supabase

bp = Blueprint("debug_auth", __name__, url_prefix="/debug")


@bp.get("/auth")
def debug_auth():
    auth = request.headers.get("Authorization", "")
    token_check = require_web_session(auth)

    sb = supabase() if callable(supabase) else supabase

    raw = auth.replace("Bearer ", "").strip()

    return jsonify({
        "token_present": bool(raw),
        "token_valid": token_check.get("ok"),
        "account_id": token_check.get("account_id"),
        "token_hash_lookup": raw[:12] + "...",
        "subscriptions": sb.table("user_subscriptions")
            .select("*")
            .eq("account_id", token_check.get("account_id"))
            .execute()
            .data if token_check.get("ok") else None
    })
