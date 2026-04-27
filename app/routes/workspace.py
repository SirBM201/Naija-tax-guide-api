from __future__ import annotations
from flask import Blueprint, jsonify, request, session
from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user
import logging

logger = logging.getLogger(__name__)

bp = Blueprint("workspace", __name__)


@bp.get("/workspace/limits")
def get_workspace_limits():
    current_user = get_current_user()
    
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    return jsonify({
        "ok": True,
        "counts": {
            "active_members_only": 0,
            "owner_included_total": 1,
        },
        "entitlements": {
            "ok": True,
            "plan": {"name": "Free", "code": "free"},
            "workspace_limits": {
                "max_workspace_users": 1,
                "max_linked_web_accounts": 1,
            },
            "channel_limits": {
                "max_total_channels": 1,
                "max_whatsapp_channels": 1,
                "max_telegram_channels": 1,
            }
        }
    }), 200


@bp.get("/workspace/members")
def get_workspace_members():
    current_user = get_current_user()
    
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    return jsonify({
        "ok": True,
        "members": [],
        "total": 0,
    }), 200
