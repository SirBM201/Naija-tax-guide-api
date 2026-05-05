from __future__ import annotations

import logging
from flask import Blueprint, request, jsonify

from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user

logger = logging.getLogger(__name__)

bp = Blueprint("workspace", __name__, url_prefix="/api/workspace")


def _get_account_id_from_auth_user(auth_user_id: str) -> str | None:
    if not auth_user_id:
        return None
    try:
        result = supabase.table("accounts")\
            .select("id")\
            .eq("auth_user_id", auth_user_id)\
            .maybe_single()\
            .execute()
        if result.data:
            return result.data.get("id")
    except Exception as e:
        logger.error(f"Failed to get account_id: {e}")
    return None


@bp.get("/limits")
def get_workspace_limits():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    auth_user_id = current_user.get("id")
    account_id = _get_account_id_from_auth_user(auth_user_id)
    
    if not account_id:
        return jsonify({"ok": False, "error": "account not found"}), 404
    
    return jsonify({
        "ok": True,
        "account_id": account_id,
        "counts": {
            "active_members_only": 0,
            "owner_included_total": 1,
        },
        "entitlements": {
            "ok": True,
            "plan_code": "free",
            "plan_family": "free",
            "plan": {
                "name": "Free",
                "code": "free",
                "plan_family": "free",
            },
            "workspace_limits": {
                "max_workspace_users": 1,
                "max_linked_web_accounts": 1,
            },
            "channel_limits": {
                "max_total_channels": 5,
                "max_whatsapp_channels": 1,
                "max_telegram_channels": 1,
            }
        }
    })


@bp.get("/members")
def list_workspace_members():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    auth_user_id = current_user.get("id")
    account_id = _get_account_id_from_auth_user(auth_user_id)
    
    if not account_id:
        return jsonify({"ok": False, "error": "account not found"}), 404
    
    try:
        # Get owner info
        owner_result = supabase.table("accounts")\
            .select("id, account_id, display_name, email, created_at")\
            .eq("id", account_id)\
            .maybe_single()\
            .execute()
        
        # Get members
        members_result = supabase.table("workspace_members")\
            .select("id, member_account_id, role, status, created_at")\
            .eq("owner_account_id", account_id)\
            .execute()
        
        members = []
        if members_result.data:
            for m in members_result.data:
                member_acc = supabase.table("accounts")\
                    .select("display_name, email, account_id")\
                    .eq("id", m.get("member_account_id"))\
                    .maybe_single()\
                    .execute()
                member_data = member_acc.data if member_acc.data else {}
                members.append({
                    "id": m.get("id"),
                    "member_account_id": m.get("member_account_id"),
                    "role": m.get("role", "member"),
                    "status": m.get("status", "active"),
                    "created_at": m.get("created_at"),
                    "member_email": member_data.get("email"),
                    "member_display_name": member_data.get("display_name"),
                })
        
        return jsonify({
            "ok": True,
            "account_id": account_id,
            "owner": owner_result.data if owner_result.data else None,
            "members": members,
            "count": len(members)
        })
    except Exception as e:
        logger.exception("Failed to list workspace members")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/members/add")
def add_workspace_member():
    return jsonify({"ok": False, "error": "Feature coming soon"}), 501


@bp.post("/members/remove")
def remove_workspace_member():
    return jsonify({"ok": False, "error": "Feature coming soon"}), 501


@bp.get("/health")
def health():
    return jsonify({"ok": True, "status": "healthy"})
