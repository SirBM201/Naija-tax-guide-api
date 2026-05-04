from __future__ import annotations

import logging
import uuid
from flask import Blueprint, request, jsonify

from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user
from app.services.accounts_service import lookup_account, upsert_account

logger = logging.getLogger(__name__)

bp = Blueprint("workspace", __name__, url_prefix="/api/workspace")


def _get_account_id_from_auth_user(auth_user_id: str) -> str | None:
    """Get accounts.id from auth_user_id"""
    if not auth_user_id:
        return None
    
    try:
        result = supabase().table("accounts")\
            .select("id")\
            .eq("auth_user_id", auth_user_id)\
            .maybe_single()\
            .execute()
        
        if result.data:
            return result.data.get("id")
    except Exception as e:
        logger.error(f"Failed to get account_id: {e}")
    
    return None


def _get_or_create_account_from_email(email: str) -> dict | None:
    """Get or create account record from email"""
    if not email:
        return None
    
    try:
        # First, try to find existing account by email
        result = supabase().table("accounts")\
            .select("id, account_id, auth_user_id, display_name, email")\
            .eq("email", email)\
            .maybe_single()\
            .execute()
        
        if result.data:
            return result.data
        
        # Create a placeholder account for the email (will need to link later)
        # This creates a basic account record that can be linked later
        new_account = {
            "account_id": str(uuid.uuid4()),
            "email": email,
            "display_name": email.split("@")[0],
            "provider": "email",
            "provider_user_id": email,
        }
        
        insert_result = supabase().table("accounts").insert(new_account).execute()
        
        if insert_result.data:
            return insert_result.data[0]
        
    except Exception as e:
        logger.error(f"Failed to get/create account from email: {e}")
    
    return None


@bp.get("/limits")
def get_workspace_limits():
    """Get workspace limits for the current user"""
    current_user = get_current_user()
    
    if not current_user:
        logger.warning("Workspace limits: unauthorized")
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    auth_user_id = current_user.get("id")
    account_id = _get_account_id_from_auth_user(auth_user_id)
    
    if not account_id:
        return jsonify({"ok": False, "error": "account not found"}), 404
    
    try:
        # Get user's subscription/plan
        sub_result = supabase().table("user_subscriptions")\
            .select("plan_code, plan_family, subscription")\
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
            
            # Plan limits based on plan_code
            if plan_family in ["pro", "business"] or plan_code in ["pro", "business"]:
                max_workspace_users = 10
                max_linked_web_accounts = 10
            elif plan_family == "team" or plan_code == "team":
                max_workspace_users = 5
                max_linked_web_accounts = 5
        
        # Count workspace members/owners
        members_result = supabase().table("workspace_members")\
            .select("id, role")\
            .eq("owner_account_id", account_id)\
            .execute()
        
        member_count = len(members_result.data) if members_result.data else 0
        
        return jsonify({
            "ok": True,
            "account_id": account_id,
            "counts": {
                "active_members_only": max(0, member_count - 1),
                "owner_included_total": member_count,
            },
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
    """List all members in the user's workspace"""
    current_user = get_current_user()
    
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    auth_user_id = current_user.get("id")
    account_id = _get_account_id_from_auth_user(auth_user_id)
    
    if not account_id:
        return jsonify({"ok": False, "error": "account not found"}), 404
    
    try:
        # Get owner info
        owner_result = supabase().table("accounts")\
            .select("id, account_id, display_name, email, created_at, updated_at, provider, provider_user_id")\
            .eq("id", account_id)\
            .maybe_single()\
            .execute()
        
        owner = None
        if owner_result.data:
            owner = owner_result.data
        
        # Get members
        members_result = supabase().table("workspace_members")\
            .select("""
                id,
                owner_account_id,
                member_account_id,
                role,
                status,
                created_at,
                updated_at,
                member_account:member_account_id (
                    display_name,
                    email,
                    provider,
                    provider_user_id,
                    account_id
                )
            """)\
            .eq("owner_account_id", account_id)\
            .execute()
        
        members = []
        if members_result.data:
            for m in members_result.data:
                member_account = m.get("member_account", {})
                members.append({
                    "id": m.get("id"),
                    "owner_account_id": m.get("owner_account_id"),
                    "member_account_id": m.get("member_account_id"),
                    "role": m.get("role", "member"),
                    "status": m.get("status", "active"),
                    "created_at": m.get("created_at"),
                    "updated_at": m.get("updated_at"),
                    "member_email": member_account.get("email") if isinstance(member_account, dict) else None,
                    "member_display_name": member_account.get("display_name") if isinstance(member_account, dict) else None,
                    "member_provider": member_account.get("provider") if isinstance(member_account, dict) else None,
                    "member_provider_user_id": member_account.get("provider_user_id") if isinstance(member_account, dict) else None,
                })
        
        # Get plan info (same as limits endpoint)
        sub_result = supabase().table("user_subscriptions")\
            .select("plan_code, plan_family")\
            .eq("account_id", account_id)\
            .eq("is_active", True)\
            .maybe_single()\
            .execute()
        
        plan_code = "free"
        plan_family = "free"
        if sub_result.data:
            plan_code = sub_result.data.get("plan_code", "free")
            plan_family = sub_result.data.get("plan_family", "free")
        
        return jsonify({
            "ok": True,
            "account_id": account_id,
            "owner": owner,
            "members": members,
            "count": len(members),
            "counts": {
                "active_members_only": len(members),
                "owner_included_total": len(members) + 1,
            },
            "entitlements": {
                "ok": True,
                "plan_code": plan_code,
                "plan_family": plan_family,
                "plan": {
                    "name": plan_family.capitalize(),
                    "code": plan_code,
                    "plan_family": plan_family,
                }
            }
        })
        
    except Exception as e:
        logger.exception("Failed to list workspace members")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/members/add")
def add_workspace_member():
    """Add a member to the workspace by email"""
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
    
    # Check if email belongs to an existing account
    member_account = None
    try:
        result = supabase().table("accounts")\
            .select("id, account_id, email")\
            .eq("email", member_email)\
            .maybe_single()\
            .execute()
        
        if result.data:
            member_account = result.data
    except Exception as e:
        logger.error(f"Failed to lookup account by email: {e}")
    
    if not member_account:
        return jsonify({
            "ok": False, 
            "error": "No account found with this email. User must sign up first.",
            "fix": "Ask the user to create an account on the web platform before adding them to the workspace."
        }), 404
    
    # Check if already a member
    try:
        existing = supabase().table("workspace_members")\
            .select("id")\
            .eq("owner_account_id", owner_account_id)\
            .eq("member_account_id", member_account["id"])\
            .maybe_single()\
            .execute()
        
        if existing.data:
            return jsonify({"ok": False, "error": "User is already a member of this workspace"}), 409
    except Exception as e:
        logger.error(f"Failed to check existing membership: {e}")
    
    # Get plan limits
    sub_result = supabase().table("user_subscriptions")\
        .select("plan_family")\
        .eq("account_id", owner_account_id)\
        .eq("is_active", True)\
        .maybe_single()\
        .execute()
    
    max_workspace_users = 1
    if sub_result.data:
        plan_family = sub_result.data.get("plan_family", "free")
        if plan_family in ["pro", "business"]:
            max_workspace_users = 10
        elif plan_family == "team":
            max_workspace_users = 5
    
    # Count current members
    count_result = supabase().table("workspace_members")\
        .select("id", count="exact")\
        .eq("owner_account_id", owner_account_id)\
        .execute()
    
    current_count = len(count_result.data) if count_result.data else 0
    
    if current_count >= max_workspace_users:
        return jsonify({
            "ok": False,
            "error": f"Workspace limit reached (max {max_workspace_users} members). Upgrade your plan to add more members.",
            "fix": "Upgrade your subscription to add more workspace members."
        }), 403
    
    # Add the member
    try:
        insert_data = {
            "owner_account_id": owner_account_id,
            "member_account_id": member_account["id"],
            "role": role,
            "status": "active"
        }
        
        result = supabase().table("workspace_members").insert(insert_data).execute()
        
        if result.data:
            return jsonify({
                "ok": True,
                "message": f"Successfully added {member_email} to workspace.",
                "member": result.data[0]
            })
        else:
            return jsonify({"ok": False, "error": "Failed to add member"}), 500
            
    except Exception as e:
        logger.exception("Failed to add workspace member")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/members/remove")
def remove_workspace_member():
    """Remove a member from the workspace"""
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
    
    # Prevent removing self
    if member_account_id == owner_account_id:
        return jsonify({"ok": False, "error": "Cannot remove the workspace owner"}), 403
    
    try:
        result = supabase().table("workspace_members")\
            .delete()\
            .eq("owner_account_id", owner_account_id)\
            .eq("member_account_id", member_account_id)\
            .execute()
        
        return jsonify({
            "ok": True,
            "message": "Member removed successfully."
        })
        
    except Exception as e:
        logger.exception("Failed to remove workspace member")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/health")
def health():
    """Health check endpoint"""
    return jsonify({"ok": True, "status": "healthy"})
'@ | Out-File -FilePath "C:\Users\sirbm\Naija-tax-guide-api\app\routes\workspace.py" -Encoding UTF8
