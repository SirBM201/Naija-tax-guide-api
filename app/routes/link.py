from __future__ import annotations
from flask import Blueprint, jsonify, request, session
from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user
import logging

logger = logging.getLogger(__name__)

bp = Blueprint("link", __name__)


@bp.get("/link/status")
def get_link_status():
    current_user = get_current_user()
    
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    return jsonify({
        "ok": True,
        "account_id": current_user.get("id"),
        "whatsapp": None,
        "telegram": None,
    }), 200
