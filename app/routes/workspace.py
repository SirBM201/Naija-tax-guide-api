# Create a minimal workspace.py that won't crash
@'
# app/routes/workspace.py
from __future__ import annotations

import os
import logging
from flask import Blueprint, request, jsonify

from app.core.supabase_client import supabase

bp = Blueprint("workspace", __name__, url_prefix="/api/workspace")

def get_auth_user_id():
    """Get authenticated user ID from Authorization header or session"""
    # Try Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            # Verify token with Supabase
            user = supabase().auth.get_user(token)
            if user and user.user:
                return user.user.id
        except:
            pass
    
    # Try X-User-ID header (for internal calls)
    user_id = request.headers.get("X-User-ID", "")
    if user_id:
        return user_id
    
    return None

@bp.route("/", methods=["GET"])
def list_workspaces():
    """Get all workspaces for the authenticated user"""
    user_id = get_auth_user_id()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        result = supabase().table("workspace_members")\
            .select("workspace_id, workspaces(*)")\
            .eq("user_id", user_id)\
            .execute()
        
        workspaces = [row.get("workspaces") for row in result.data if row.get("workspaces")]
        return jsonify({"ok": True, "data": workspaces})
    except Exception as e:
        logging.exception("Failed to list workspaces")
        return jsonify({"error": str(e)}), 500

@bp.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    return jsonify({"ok": True, "status": "healthy"})
'@ | Out-File -FilePath "C:\Users\sirbm\Naija-tax-guide-api\app\routes\workspace.py" -Encoding UTF8
