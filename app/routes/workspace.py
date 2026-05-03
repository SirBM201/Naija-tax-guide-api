# app/routes/workspace.py
from __future__ import annotations

import logging
from flask import Blueprint, request, jsonify

from app.core.supabase_client import supabase
from app.services.accounts_service import require_auth

bp = Blueprint("workspace", __name__, url_prefix="/api/workspace")


@bp.route("/", methods=["GET"])
def list_workspaces():
    """Get all workspaces for the authenticated user"""
    auth_user_id = require_auth(request)
    if not auth_user_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        result = supabase().table("workspace_members")\
            .select("workspace_id, workspaces(*)")\
            .eq("user_id", auth_user_id)\
            .execute()
        
        workspaces = []
        for row in result.data:
            if row.get("workspaces"):
                workspaces.append(row["workspaces"])
        
        return jsonify({"ok": True, "data": workspaces})
    except Exception as e:
        logging.exception("Failed to list workspaces")
        return jsonify({"error": str(e)}), 500


@bp.route("/", methods=["POST"])
def create_workspace():
    """Create a new workspace"""
    auth_user_id = require_auth(request)
    if not auth_user_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    
    if not name:
        return jsonify({"error": "Workspace name required"}), 400
    
    try:
        # Create workspace
        workspace_result = supabase().table("workspaces").insert({
            "name": name,
            "created_by": auth_user_id
        }).execute()
        
        if not workspace_result.data:
            return jsonify({"error": "Failed to create workspace"}), 500
        
        workspace = workspace_result.data[0]
        
        # Add creator as owner
        supabase().table("workspace_members").insert({
            "workspace_id": workspace["id"],
            "user_id": auth_user_id,
            "role": "owner"
        }).execute()
        
        return jsonify({"ok": True, "data": workspace})
    except Exception as e:
        logging.exception("Failed to create workspace")
        return jsonify({"error": str(e)}), 500


@bp.route("/<workspace_id>", methods=["GET"])
def get_workspace(workspace_id: str):
    """Get a specific workspace"""
    auth_user_id = require_auth(request)
    if not auth_user_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        # Verify membership
        member = supabase().table("workspace_members")\
            .select("workspace_id")\
            .eq("workspace_id", workspace_id)\
            .eq("user_id", auth_user_id)\
            .maybe_single()\
            .execute()
        
        if not member.data:
            return jsonify({"error": "Access denied"}), 403
        
        result = supabase().table("workspaces")\
            .select("*")\
            .eq("id", workspace_id)\
            .maybe_single()\
            .execute()
        
        return jsonify({"ok": True, "data": result.data})
    except Exception as e:
        logging.exception("Failed to get workspace")
        return jsonify({"error": str(e)}), 500


@bp.route("/<workspace_id>/members", methods=["GET"])
def get_members(workspace_id: str):
    """Get workspace members"""
    auth_user_id = require_auth(request)
    if not auth_user_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        result = supabase().table("workspace_members")\
            .select("user_id, role, users(display_name, email)")\
            .eq("workspace_id", workspace_id)\
            .execute()
        
        return jsonify({"ok": True, "data": result.data})
    except Exception as e:
        logging.exception("Failed to get members")
        return jsonify({"error": str(e)}), 500
