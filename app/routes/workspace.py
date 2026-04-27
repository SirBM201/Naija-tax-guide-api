from __future__ import annotations
from flask import Blueprint, jsonify, request, session
from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user
import logging

logger = logging.getLogger(__name__)

bp = Blueprint("workspace", __name__)


@bp.get("/workspace/limits")
def get_workspace_limits():
    """
    Get workspace limits based on current subscription.
    Returns workspace user limits, channel limits, and current usage.
    """
    current_user = get_current_user()
    
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    account_id = current_user.get("id")
    
    try:
        sb = supabase
        
        # Get user's subscription
        sub_result = sb.table("user_subscriptions")\
            .select("*")\
            .eq("account_id", account_id)\
            .eq("status", "active")\
            .limit(1)\
            .execute()
        
        subscription = sub_result.data[0] if sub_result.data else None
        
        # Determine plan - default to free plan
        plan_code = subscription.get("plan_code") if subscription else "free"
        
        # Define limits by plan
        plan_limits = {
            "free": {
                "max_workspace_users": 1,
                "max_total_channels": 1,
                "max_whatsapp_channels": 1,
                "max_telegram_channels": 1,
            },
            "basic": {
                "max_workspace_users": 3,
                "max_total_channels": 5,
                "max_whatsapp_channels": 3,
                "max_telegram_channels": 3,
            },
            "pro": {
                "max_workspace_users": 10,
                "max_total_channels": 20,
                "max_whatsapp_channels": 10,
                "max_telegram_channels": 10,
            },
            "enterprise": {
                "max_workspace_users": 100,
                "max_total_channels": 100,
                "max_whatsapp_channels": 50,
                "max_telegram_channels": 50,
            }
        }
        
        limits = plan_limits.get(plan_code, plan_limits["free"])
        
        # Get current workspace members
        members_result = sb.table("workspace_members")\
            .select("member_account_id", "status")\
            .eq("owner_account_id", account_id)\
            .execute()
        
        active_members = [m for m in (members_result.data or []) if m.get("status") == "active"]
        owner_included_total = len(active_members) + 1  # +1 for owner
        
        # Get channel usage
        channels_result = sb.table("channel_identities")\
            .select("channel_type")\
            .eq("account_id", account_id)\
            .execute()
        
        whatsapp_count = sum(1 for c in (channels_result.data or []) if c.get("channel_type") == "whatsapp")
        telegram_count = sum(1 for c in (channels_result.data or []) if c.get("channel_type") == "telegram")
        
        counts = {
            "active_members_only": len(active_members),
            "owner_included_total": owner_included_total,
            "whatsapp_count": whatsapp_count,
            "telegram_count": telegram_count,
            "total_channels": whatsapp_count + telegram_count,
        }
        
        entitlements = {
            "ok": True,
            "plan": {
                "name": subscription.get("plan_name", "Free") if subscription else "Free",
                "code": plan_code,
                "plan_family": "free" if plan_code == "free" else "paid",
            },
            "plan_code": plan_code,
            "plan_family": "free" if plan_code == "free" else "paid",
            "workspace_limits": {
                "max_workspace_users": limits["max_workspace_users"],
                "max_linked_web_accounts": 1,
            },
            "channel_limits": {
                "max_total_channels": limits["max_total_channels"],
                "max_whatsapp_channels": limits["max_whatsapp_channels"],
                "max_telegram_channels": limits["max_telegram_channels"],
            }
        }
        
        return jsonify({
            "ok": True,
            "counts": counts,
            "entitlements": entitlements,
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting workspace limits: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/workspace/members")
def get_workspace_members():
    """Get workspace members for the current account."""
    current_user = get_current_user()
    
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    account_id = current_user.get("id")
    
    try:
        sb = supabase
        
        members_result = sb.table("workspace_members")\
            .select("member_account_id, status, created_at, updated_at, accounts(email, display_name)")\
            .eq("owner_account_id", account_id)\
            .execute()
        
        members = []
        for m in (members_result.data or []):
            account_data = m.get("accounts", {})
            members.append({
                "account_id": m.get("member_account_id"),
                "status": m.get("status"),
                "email": account_data.get("email") if isinstance(account_data, dict) else None,
                "display_name": account_data.get("display_name") if isinstance(account_data, dict) else None,
                "joined_at": m.get("created_at"),
            })
        
        return jsonify({
            "ok": True,
            "members": members,
            "total": len(members),
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting workspace members: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/workspace/members/add")
def add_workspace_member():
    """Add a member to workspace."""
    current_user = get_current_user()
    
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    data = request.get_json() or {}
    member_email = data.get("email", "").strip().lower()
    
    if not member_email:
        return jsonify({"ok": False, "error": "email_required"}), 400
    
    try:
        sb = supabase
        
        # Find the account by email
        account_result = sb.table("accounts")\
            .select("id, account_id, email")\
            .eq("email", member_email)\
            .limit(1)\
            .execute()
        
        if not account_result.data:
            return jsonify({"ok": False, "error": "account_not_found"}), 404
        
        member_account_id = account_result.data[0].get("account_id") or account_result.data[0].get("id")
        
        # Check if already a member
        existing = sb.table("workspace_members")\
            .select("*")\
            .eq("owner_account_id", current_user.get("id"))\
            .eq("member_account_id", member_account_id)\
            .execute()
        
        if existing.data:
            return jsonify({"ok": False, "error": "member_already_exists"}), 400
        
        # Add member
        new_member = {
            "owner_account_id": current_user.get("id"),
            "member_account_id": member_account_id,
            "status": "pending",
            "created_at": sb.table("workspace_members").execute().data[0].get("created_at") if False else None,
        }
        
        result = sb.table("workspace_members").insert(new_member).execute()
        
        return jsonify({
            "ok": True,
            "message": "Member invited successfully",
            "member": result.data[0] if result.data else None,
        }), 200
        
    except Exception as e:
        logger.error(f"Error adding workspace member: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
