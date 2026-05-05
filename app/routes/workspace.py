from __future__ import annotations

import logging
import uuid
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
    
    try:
        sub_result = supabase.table("user_subscriptions")\
            .select("plan_code, plan_family")\
            .eq("account_id", account_id)\
            .eq("is_active", True)\
            .maybe_single()\
            .execute()
        
        plan_code = "free"
        plan_family = "free"
        max_workspace_users = 1
        max_linked_web_accounts = 1
        
        if sub_result.data:
            plan_code = sub_result.data.get("plan_code", "free")
            plan_family = sub_result.data.get("plan_family", "free")
            
            if plan_family in ["pro", "business"] or plan_code in ["pro", "business"]:
                max_workspace_users = 10
                max_linked_web_accounts = 10
            elif plan_family == "team" or plan_code == "team":
                max_workspace_users = 5
                max_linked_web_accounts = 5
        
        return jsonify({
            "ok": True,
            "account_id": account_id,
            "entitlements": {
                "ok": True,
                "plan_code": plan_code,
                "plan_family": plan_family,
                "plan": {
                    "name": plan_family.capitalize(),
                    "code": plan_code,
                    "plan_family": plan_family,
                },
                "workspace_limits": {
                    "max_workspace_users": max_workspace_users,
                    "max_linked_web_accounts": max_linked_web_accounts,
                },
                "channel_limits": {
                    "max_total_channels": 100 if plan_family != "free" else 5,
                    "max_whatsapp_channels": 10 if plan_family != "free" else 1,
                    "max_telegram_channels": 10 if plan_family != "free" else 1,
                }
            }
        })
    except Exception as e:
        logger.exception("Failed to get workspace limits")
        return jsonify({"ok": False, "error": str(e)}), 500


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
        owner_result = supabase.table("accounts")\
            .select("id, account_id, display_name, email, created_at")\
            .eq("id", account_id)\
            .maybe_single()\
            .execute()
        
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
    current_user = get_current_user()
    
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    auth_user_id = current_user.get("id")
    owner_account_id = _get_account_id_from_auth_user(auth_user_id)
    
    if not owner_account_id:
        return jsonify({"ok": False, "error": "account not found"}), 404
    
    data = request.get_json() or {}
    member_email = (data.get("member_email") or "").strip().lower()
    role = data.get("role", "member")
    
    if not member_email:
        return jsonify({"ok": False, "error": "member_email required"}), 400
    
    member_account = None
    try:
        result = supabase.table("accounts")\
            .select("id, account_id, email")\
            .eq("email", member_email)\
            .maybe_single()\
            .execute()
        
        if result.data:
            member_account = result.data
    except Exception as e:
        logger.error(f"Failed to lookup account: {e}")
    
    if not member_account:
        return jsonify({
            "ok": False,
            "error": "No account found with this email. User must sign up first."
        }), 404
    
    try:
        existing = supabase.table("workspace_members")\
            .select("id")\
            .eq("owner_account_id", owner_account_id)\
            .eq("member_account_id", member_account["id"])\
            .maybe_single()\
            .execute()
        
        if existing.data:
            return jsonify({"ok": False, "error": "User is already a member"}), 409
    except Exception as e:
        logger.error(f"Failed to check existing: {e}")
    
    try:
        insert_data = {
            "owner_account_id": owner_account_id,
            "member_account_id": member_account["id"],
            "role": role,
            "status": "active"
        }
        
        result = supabase.table("workspace_members").insert(insert_data).execute()
        
        if result.data:
            return jsonify({
                "ok": True,
                "message": f"Successfully added {member_email} to workspace."
            })
        else:
            return jsonify({"ok": False, "error": "Failed to add member"}), 500
    except Exception as e:
        logger.exception("Failed to add member")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/members/remove")
def remove_workspace_member():
    current_user = get_current_user()
    
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    auth_user_id = current_user.get("id")
    owner_account_id = _get_account_id_from_auth_user(auth_user_id)
    
    if not owner_account_id:
        return jsonify({"ok": False, "error": "account not found"}), 404
    
    data = request.get_json() or {}
    member_account_id = data.get("member_account_id") or data.get("member_id")
    
    if not member_account_id:
        return jsonify({"ok": False, "error": "member_account_id required"}), 400
    
    if member_account_id == owner_account_id:
        return jsonify({"ok": False, "error": "Cannot remove workspace owner"}), 403
    
    try:
        supabase.table("workspace_members")\
            .delete()\
            .eq("owner_account_id", owner_account_id)\
            .eq("member_account_id", member_account_id)\
            .execute()
        
        return jsonify({
            "ok": True,
            "message": "Member removed successfully."
        })
    except Exception as e:
        logger.exception("Failed to remove member")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/health")
def health():
    return jsonify({"ok": True, "status": "healthy"})
