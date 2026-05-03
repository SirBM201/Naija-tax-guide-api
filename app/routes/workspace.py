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
        logger.warning("Workspace limits: unauthorized")
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    logger.info(f"Workspace limits for user: {current_user.get('id')}")

    return jsonify({
        "ok": True,
        "counts": {
            "active_members_only": 0,
            "owner_included_total": 1,
        },
        "entitlements": {
            "ok": True,
            "plan": {"name": "Free", "code": "free", "plan_family": "free"},
            "plan_code": "free",
            "plan_family": "free",
            "workspace_limits": {
                "max_workspace_users": 1,
                "max_linked_web_accounts": 1,
            },
            "channel_limits": {
                "web": {"enabled": True},
                "whatsapp": {"enabled": True},
                "telegram": {"enabled": True},
                "meta": {"enabled": False},
            }
        }
    })
