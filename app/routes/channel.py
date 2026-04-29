# app/routes/channel.py
from __future__ import annotations

import os
import logging
from flask import Blueprint, jsonify, request, session

from app.services.auth_service import get_current_user
from app.services.channel_linking_service import (
    generate_link_code,
    get_linked_channels,
    unlink_channel,
    get_channel_status,
)

logger = logging.getLogger(__name__)

bp = Blueprint("channel", __name__)


@bp.get("/channel/status")
def channel_status():
    """Get status of all linked channels for current user"""
    current_user = get_current_user()
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    account_id = current_user.get("account_id") or current_user.get("id")
    
    try:
        channels = get_linked_channels(account_id)
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
    provider = request.get_json().get("provider", "").strip().lower()
    
    if provider not in ("wa", "tg", "whatsapp", "telegram"):
        return jsonify({"ok": False, "error": "Invalid provider. Use 'wa' or 'tg'"}), 400
    
    # Normalize provider
    if provider == "whatsapp":
        provider = "wa"
    elif provider == "telegram":
        provider = "tg"
    
    try:
        result = generate_link_code(account_id, provider)
        return jsonify(result), 200 if result.get("ok") else 400
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
    
    if provider not in ("wa", "tg", "whatsapp", "telegram"):
        return jsonify({"ok": False, "error": "Invalid provider"}), 400
    
    if provider == "whatsapp":
        provider = "wa"
    elif provider == "telegram":
        provider = "tg"
    
    try:
        result = unlink_channel(account_id, provider)
        return jsonify(result), 200 if result.get("ok") else 400
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
        channels = get_linked_channels(account_id)
        return jsonify({
            "ok": True,
            "linked": [c for c in channels if c.get("linked")],
            "available": ["whatsapp", "telegram"]
        }), 200
    except Exception as e:
        logger.error(f"Linked channels error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
