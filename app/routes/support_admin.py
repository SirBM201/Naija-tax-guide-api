# app/routes/support_admin.py
from __future__ import annotations

import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

from app.core.security import require_admin_key
from app.core.supabase_client import supabase

logger = logging.getLogger(__name__)

bp = Blueprint("support_admin", __name__)


@bp.get("/admin/support/tickets")
def list_all_tickets():
    """List all support tickets (admin only)"""
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    status = request.args.get("status", "").strip()
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    
    try:
        query = supabase.table("support_tickets").select("*", count="exact")
        
        if status:
            query = query.eq("status", status)
        
        query = query.order("created_at", desc=True).range(offset, offset + limit - 1)
        result = query.execute()
        
        return jsonify({
            "ok": True,
            "tickets": result.data or [],
            "total": result.count or 0,
            "limit": limit,
            "offset": offset
        }), 200
    except Exception as e:
        logger.error(f"List all tickets error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/admin/support/ticket/<ticket_id>")
def get_ticket_admin(ticket_id):
    """Get a specific support ticket (admin only)"""
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    try:
        ticket_result = supabase.table("support_tickets") \
            .select("*") \
            .eq("id", ticket_id) \
            .execute()
        
        if not ticket_result.data:
            return jsonify({"ok": False, "error": "ticket_not_found"}), 404
        
        messages_result = supabase.table("support_ticket_messages") \
            .select("*") \
            .eq("support_ticket_id", ticket_id) \
            .order("created_at", asc=True) \
            .execute()
        
        return jsonify({
            "ok": True,
            "ticket": ticket_result.data[0],
            "messages": messages_result.data or []
        }), 200
    except Exception as e:
        logger.error(f"Get ticket admin error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/admin/support/ticket/<ticket_id>/reply")
def reply_ticket_admin(ticket_id):
    """Reply to a ticket as staff (admin only)"""
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    
    if not message:
        return jsonify({"ok": False, "error": "message_required"}), 400
    
    now = datetime.now(timezone.utc).isoformat()
    
    try:
        supabase.table("support_ticket_messages").insert({
            "support_ticket_id": ticket_id,
            "account_id": None,
            "message": message,
            "is_staff": True,
            "created_at": now
        }).execute()
        
        supabase.table("support_tickets") \
            .update({"status": "in_progress", "updated_at": now}) \
            .eq("id", ticket_id) \
            .execute()
        
        return jsonify({
            "ok": True,
            "message": "Reply sent"
        }), 200
    except Exception as e:
        logger.error(f"Reply ticket admin error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/admin/support/ticket/<ticket_id>/close")
def close_ticket(ticket_id):
    """Close a support ticket (admin only)"""
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    now = datetime.now(timezone.utc).isoformat()
    
    try:
        supabase.table("support_tickets") \
            .update({"status": "closed", "updated_at": now, "closed_at": now}) \
            .eq("id", ticket_id) \
            .execute()
        
        return jsonify({
            "ok": True,
            "message": "Ticket closed"
        }), 200
    except Exception as e:
        logger.error(f"Close ticket error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/admin/support/stats")
def support_stats():
    """Get support statistics (admin only)"""
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    try:
        # Get counts by status
        status_counts = {}
        for status in ["open", "in_progress", "closed"]:
            result = supabase.table("support_tickets") \
                .select("*", count="exact") \
                .eq("status", status) \
                .execute()
            status_counts[status] = result.count or 0
        
        # Get total tickets
        total_result = supabase.table("support_tickets") \
            .select("*", count="exact") \
            .execute()
        
        # Get recent activity
        week_ago = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        recent_result = supabase.table("support_tickets") \
            .select("*", count="exact") \
            .gte("created_at", week_ago.isoformat()) \
            .execute()
        
        return jsonify({
            "ok": True,
            "stats": {
                "total": total_result.count or 0,
                "by_status": status_counts,
                "last_7_days": recent_result.count or 0
            }
        }), 200
    except Exception as e:
        logger.error(f"Support stats error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
