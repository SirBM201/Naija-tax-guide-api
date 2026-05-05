# app/routes/channel.py
from __future__ import annotations

import secrets
import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request

from app.services.auth_service import get_current_user
from app.core.supabase_client import supabase

logger = logging.getLogger(__name__)

bp = Blueprint("channel", __name__)


def _normalize_provider(provider: str) -> str:
    p = str(provider or "").strip().lower()
    if p in {"wa", "whatsapp", "waba"}:
        return "wa"
    if p in {"tg", "telegram"}:
        return "tg"
    return p


@bp.get("/channel/status")
def channel_status():
    """Get status of all linked channels for current user"""
    current_user = get_current_user()
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    account_id = current_user.get("account_id") or current_user.get("id")
    
    try:
        # Get channel identities for this account
        result = supabase().table("channel_identities") \
            .select("*") \
            .eq("account_id", account_id) \
            .execute()
        
        channels = []
        for identity in (result.data or []):
            channel_type = identity.get("channel_type", "")
            if channel_type == "whatsapp":
                channels.append({
                    "provider": "whatsapp",
                    "linked": True,
                    "linked_at": identity.get("created_at"),
                    "user_id": identity.get("provider_user_id")
                })
            elif channel_type == "telegram":
                channels.append({
                    "provider": "telegram",
                    "linked": True,
                    "linked_at": identity.get("created_at"),
                    "user_id": identity.get("provider_user_id")
                })
        
        return jsonify({
            "ok": True,
            "channels": channels,
            "account_id": account_id
        }), 200
    except Exception as e:
        logger.error(f"Channel status error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/channel/generate-code")
def generate_code():
    """Generate a link code for connecting WhatsApp/Telegram"""
    current_user = get_current_user()
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    account_id = current_user.get("account_id") or current_user.get("id")
    data = request.get_json() or {}
    provider = data.get("provider", "").strip().lower()
    provider = _normalize_provider(provider)
    
    if provider not in ("wa", "tg"):
        return jsonify({"ok": False, "error": "Invalid provider. Use 'wa' or 'tg'"}), 400
    
    try:
        # Generate 8-character code
        code = secrets.token_urlsafe(6).upper().replace("-", "").replace("_", "")[:8]
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        
        result = supabase().table("link_tokens").insert({
            "code": code,
            "provider": provider,
            "auth_user_id": account_id,
            "expires_at": expires_at.isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "used": False
        }).execute()
        
        if not result.data:
            return jsonify({"ok": False, "error": "failed_to_generate_code"}), 500
        
        return jsonify({
            "ok": True,
            "code": code,
            "expires_at": expires_at.isoformat(),
            "message": f"Share this code on {provider.upper()} to link your account"
        }), 200
    except Exception as e:
        logger.error(f"Generate code error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.delete("/channel/unlink")
def unlink():
    """Unlink a channel from the account"""
    current_user = get_current_user()
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    account_id = current_user.get("account_id") or current_user.get("id")
    data = request.get_json() or {}
    provider = data.get("provider", "").strip().lower()
    
    if provider not in ("whatsapp", "telegram"):
        return jsonify({"ok": False, "error": "Invalid provider. Use 'whatsapp' or 'telegram'"}), 400
    
    channel_type = "whatsapp" if provider == "whatsapp" else "telegram"
    
    try:
        # Delete channel identity
        supabase().table("channel_identities") \
            .delete() \
            .eq("account_id", account_id) \
            .eq("channel_type", channel_type) \
            .execute()
        
        return jsonify({
            "ok": True,
            "message": f"{provider} unlinked successfully"
        }), 200
    except Exception as e:
        logger.error(f"Unlink error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/channel/linked")
def linked_channels():
    """Get list of linked channels for current user"""
    current_user = get_current_user()
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    account_id = current_user.get("account_id") or current_user.get("id")
    
    try:
        result = supabase().table("channel_identities") \
            .select("channel_type") \
            .eq("account_id", account_id) \
            .execute()
        
        linked = []
        for identity in (result.data or []):
            channel_type = identity.get("channel_type", "")
            if channel_type == "whatsapp":
                linked.append("whatsapp")
            elif channel_type == "telegram":
                linked.append("telegram")
        
        return jsonify({
            "ok": True,
            "linked": linked,
            "available": ["whatsapp", "telegram"]
        }), 200
    except Exception as e:
        logger.error(f"Linked channels error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
