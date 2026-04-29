# app/routes/channel_admin.py
from __future__ import annotations

import os
import logging
from flask import Blueprint, jsonify, request

from app.core.security import require_admin_key
from app.services.channel_linking_service import (
    get_all_link_codes,
    revoke_link_code,
    get_channel_stats,
)

logger = logging.getLogger(__name__)

bp = Blueprint("channel_admin", __name__)


@bp.get("/admin/channels/stats")
def channel_stats():
    """Get channel statistics (admin only)"""
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    try:
        stats = get_channel_stats()
        return jsonify({
            "ok": True,
            "stats": stats
        }), 200
    except Exception as e:
        logger.error(f"Channel stats error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/admin/channels/codes")
def list_codes():
    """List all active link codes (admin only)"""
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    limit = request.args.get("limit", 100, type=int)
    
    try:
        codes = get_all_link_codes(limit)
        return jsonify({
            "ok": True,
            "codes": codes,
            "count": len(codes)
        }), 200
    except Exception as e:
        logger.error(f"List codes error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.delete("/admin/channels/revoke")
def revoke_code():
    """Revoke a link code (admin only)"""
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    data = request.get_json() or {}
    code = data.get("code", "").strip()
    
    if not code:
        return jsonify({"ok": False, "error": "code_required"}), 400
    
    try:
        result = revoke_link_code(code)
        return jsonify(result), 200 if result.get("ok") else 400
    except Exception as e:
        logger.error(f"Revoke code error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
