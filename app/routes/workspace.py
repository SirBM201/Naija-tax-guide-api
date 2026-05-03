# Write the proper workspace.py content
Set-Content -Path "C:\Users\sirbm\Naija-tax-guide-api\app\routes\workspace.py" -Value @"
# app/routes/workspace.py
from __future__ import annotations

import logging
from flask import Blueprint, request, jsonify

from app.core.supabase_client import supabase

bp = Blueprint("workspace", __name__, url_prefix="/api/workspace")


def _get_user_from_request():
    """Get user ID from Authorization header"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            user = supabase().auth.get_user(token)
            if user and user.user:
                return user.user.id
        except Exception as e:
            logging.debug(f"Auth failed: {e}")
    
    # Fallback for internal/testing
    user_id = request.headers.get("X-User-ID", "")
    if user_id:
        return user_id
    
    return None


@bp.route("/", methods=["GET"])
def list_workspaces():
    """Get workspaces for current user"""
    user_id = _get_user_from_request()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        result = supabase().table("workspace_members")\
            .select("workspace_id, workspaces(*)")\
            .eq("user_id", user_id)\
            .execute()
        
        workspaces = []
        for row in result.data:
            if row.get("workspaces"):
                workspaces.append(row["workspaces"])
        
        return jsonify({"ok": True, "data": workspaces})
    except Exception as e:
        logging.exception("Failed to list workspaces")
        return jsonify({"error": str(e)}), 500


@bp.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    return jsonify({"ok": True, "status": "healthy"})
"@
