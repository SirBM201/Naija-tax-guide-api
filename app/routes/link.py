from __future__ import annotations
from flask import Blueprint, jsonify, request, session
from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user
import logging

logger = logging.getLogger(__name__)

bp = Blueprint("link", __name__)


@bp.get("/link/status")
def get_link_status():
    """
    Get channel link status for current account.
    Returns WhatsApp and Telegram connection status.
    """
    current_user = get_current_user()
    
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    account_id = current_user.get("id")
    
    try:
        sb = supabase
        
        # Get channel identities
        channels_result = sb.table("channel_identities")\
            .select("*")\
            .eq("account_id", account_id)\
            .execute()
        
        whatsapp_data = None
        telegram_data = None
        
        for channel in (channels_result.data or []):
            channel_type = channel.get("channel_type")
            if channel_type == "whatsapp":
                whatsapp_data = {
                    "linked": True,
                    "provider_user_id": channel.get("provider_user_id"),
                    "display_name": channel.get("display_name"),
                    "updated_at": channel.get("updated_at"),
                    "is_verified": channel.get("is_verified", False),
                }
            elif channel_type == "telegram":
                telegram_data = {
                    "linked": True,
                    "provider_user_id": channel.get("provider_user_id"),
                    "display_name": channel.get("display_name"),
                    "updated_at": channel.get("updated_at"),
                    "is_verified": channel.get("is_verified", False),
                }
        
        return jsonify({
            "ok": True,
            "account_id": account_id,
            "whatsapp": whatsapp_data,
            "telegram": telegram_data,
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting link status: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/link/consume")
def consume_link():
    """Consume a link token to connect a channel."""
    current_user = get_current_user()
    
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    data = request.get_json() or {}
    token = data.get("token", "").strip()
    channel_type = data.get("channel_type", "").strip().lower()
    
    if not token or not channel_type:
        return jsonify({"ok": False, "error": "token_and_channel_type_required"}), 400
    
    try:
        sb = supabase
        
        # Verify token
        token_result = sb.table("link_tokens")\
            .select("*")\
            .eq("token", token)\
            .eq("used", False)\
            .gt("expires_at", "now()")\
            .limit(1)\
            .execute()
        
        if not token_result.data:
            return jsonify({"ok": False, "error": "invalid_or_expired_token"}), 400
        
        # Mark token as used
        sb.table("link_tokens")\
            .update({"used": True, "used_at": "now()"})\
            .eq("token", token)\
            .execute()
        
        # Create channel identity
        channel_data = {
            "account_id": current_user.get("id"),
            "channel_type": channel_type,
            "provider_user_id": token_result.data[0].get("provider_user_id"),
            "is_verified": True,
            "verified_at": "now()",
        }
        
        result = sb.table("channel_identities").insert(channel_data).execute()
        
        return jsonify({
            "ok": True,
            "message": f"{channel_type.upper()} connected successfully",
            "channel": result.data[0] if result.data else None,
        }), 200
        
    except Exception as e:
        logger.error(f"Error consuming link: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/link/generate")
def generate_link():
    """Generate a link token for channel connection."""
    current_user = get_current_user()
    
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    data = request.get_json() or {}
    channel_type = data.get("channel_type", "").strip().lower()
    provider_user_id = data.get("provider_user_id", "").strip()
    
    if not channel_type or not provider_user_id:
        return jsonify({"ok": False, "error": "channel_type_and_provider_user_id_required"}), 400
    
    import secrets
    import uuid
    from datetime import datetime, timezone, timedelta
    
    try:
        sb = supabase
        
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        
        link_data = {
            "id": str(uuid.uuid4()),
            "token": token,
            "account_id": current_user.get("id"),
            "channel_type": channel_type,
            "provider_user_id": provider_user_id,
            "expires_at": expires_at.isoformat(),
            "used": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        result = sb.table("link_tokens").insert(link_data).execute()
        
        return jsonify({
            "ok": True,
            "token": token,
            "expires_at": expires_at.isoformat(),
        }), 200
        
    except Exception as e:
        logger.error(f"Error generating link: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/link/status")
def link_status():
    """Alias for get_link_status - kept for compatibility."""
    return get_link_status()
