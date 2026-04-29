# app/routes/support.py
from __future__ import annotations

import os
import uuid
import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.web_auth_service import get_account_id_from_request
from app.services.mail_service import send_email

logger = logging.getLogger(__name__)

bp = Blueprint("support", __name__)


def _sb():
    return supabase()


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _ticket_id() -> str:
    return f"NTG-{str(uuid.uuid4()).split('-')[0].upper()}"


def _support_to_email() -> str:
    return (
        _env("SUPPORT_TO_EMAIL")
        or _env("SUPPORT_EMAIL")
        or _env("MAIL_FROM_EMAIL")
        or _env("SMTP_FROM")
        or _env("MAIL_USER")
        or _env("SMTP_USER")
    )


@bp.get("/support/health")
def support_health():
    """Health check endpoint"""
    to_email = _support_to_email()
    return jsonify({
        "ok": True,
        "route_group": "support",
        "mail_ready": bool(to_email),
        "support_to_email": to_email or None,
        "endpoints": ["/support", "/support/tickets", "/support/tickets/<id>", "/support/tickets/<id>/reply"]
    }), 200


@bp.get("/support/tickets")
def list_tickets():
    """List user's support tickets"""
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    limit = request.args.get("limit", 50, type=int)
    
    try:
        result = _sb().table("support_tickets") \
            .select("*") \
            .eq("account_id", account_id) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        
        return jsonify({
            "ok": True,
            "tickets": result.data or [],
            "count": len(result.data or [])
        }), 200
    except Exception as e:
        logger.error(f"List tickets error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/support/tickets/<ticket_id>")
def get_ticket(ticket_id: str):
    """Get a specific support ticket with messages"""
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    try:
        ticket_result = _sb().table("support_tickets") \
            .select("*") \
            .eq("ticket_id", ticket_id) \
            .eq("account_id", account_id) \
            .execute()
        
        if not ticket_result.data:
            return jsonify({"ok": False, "error": "ticket_not_found"}), 404
        
        messages_result = _sb().table("support_ticket_messages") \
            .select("*") \
            .eq("ticket_id", ticket_id) \
            .eq("account_id", account_id) \
            .order("created_at", asc=True) \
            .execute()
        
        return jsonify({
            "ok": True,
            "ticket": ticket_result.data[0],
            "messages": messages_result.data or []
        }), 200
    except Exception as e:
        logger.error(f"Get ticket error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/support/tickets/<ticket_id>/reply")
def reply_ticket(ticket_id: str):
    """Reply to a support ticket"""
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    
    if not message:
        return jsonify({"ok": False, "error": "message_required"}), 400

    try:
        ticket_result = _sb().table("support_tickets") \
            .select("id, ticket_id, status") \
            .eq("ticket_id", ticket_id) \
            .eq("account_id", account_id) \
            .execute()
        
        if not ticket_result.data:
            return jsonify({"ok": False, "error": "ticket_not_found"}), 404
        
        ticket = ticket_result.data[0]
        now = datetime.now(timezone.utc).isoformat()
        
        _sb().table("support_ticket_messages").insert({
            "ticket_id": ticket_id,
            "account_id": account_id,
            "message": message,
            "sender_type": "user",
            "is_internal_note": False,
            "created_at": now
        }).execute()
        
        _sb().table("support_tickets") \
            .update({"status": "open", "updated_at": now, "last_message_preview": message[:200]}) \
            .eq("id", ticket["id"]) \
            .execute()
        
        return jsonify({
            "ok": True,
            "message": "Reply added successfully"
        }), 200
    except Exception as e:
        logger.error(f"Reply ticket error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/support")
def submit_support():
    """Create a new support ticket"""
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    body = request.get_json(silent=True) or {}
    
    subject = (body.get("subject") or "").strip()
    message = (body.get("message") or "").strip()
    category = (body.get("category") or "general").strip()
    
    if not subject or not message:
        return jsonify({"ok": False, "error": "subject_and_message_required"}), 400
    
    try:
        now = datetime.now(timezone.utc).isoformat()
        new_ticket_id = _ticket_id()
        
        ticket_result = _sb().table("support_tickets").insert({
            "ticket_id": new_ticket_id,
            "account_id": account_id,
            "subject": subject,
            "category": category,
            "status": "open",
            "created_at": now,
            "updated_at": now
        }).execute()
        
        if not ticket_result.data:
            return jsonify({"ok": False, "error": "failed_to_create_ticket"}), 500
        
        _sb().table("support_ticket_messages").insert({
            "ticket_id": new_ticket_id,
            "account_id": account_id,
            "message": message,
            "sender_type": "user",
            "is_internal_note": False,
            "created_at": now
        }).execute()
        
        support_email = _support_to_email()
        if support_email:
            try:
                send_email(
                    to_email=support_email,
                    subject=f"[Support] New ticket {new_ticket_id}: {subject}",
                    html_body=f"<h3>New Support Ticket</h3><p><strong>Ticket ID:</strong> {new_ticket_id}</p><p><strong>Subject:</strong> {subject}</p><p><strong>Message:</strong></p><p>{message}</p>",
                    text_body=f"New support ticket from account {account_id}\n\nTicket ID: {new_ticket_id}\nSubject: {subject}\n\nMessage:\n{message}"
                )
            except Exception as mail_error:
                logger.warning(f"Support email notification failed: {mail_error}")
        
        return jsonify({
            "ok": True,
            "message": "Support ticket created successfully",
            "ticket_id": new_ticket_id,
            "ticket": ticket_result.data[0]
        }), 201
    except Exception as e:
        logger.error(f"Submit support error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.delete("/support/tickets/<ticket_id>/close")
def close_ticket(ticket_id: str):
    """Close a support ticket"""
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    try:
        now = datetime.now(timezone.utc).isoformat()
        
        result = _sb().table("support_tickets") \
            .update({"status": "closed", "closed_at": now, "updated_at": now}) \
            .eq("ticket_id", ticket_id) \
            .eq("account_id", account_id) \
            .execute()
        
        if not result.data:
            return jsonify({"ok": False, "error": "ticket_not_found"}), 404
        
        return jsonify({
            "ok": True,
            "message": "Ticket closed successfully"
        }), 200
    except Exception as e:
        logger.error(f"Close ticket error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/support/stats")
def support_stats():
    """Get support statistics for the authenticated user"""
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    try:
        result = _sb().table("support_tickets") \
            .select("status") \
            .eq("account_id", account_id) \
            .execute()
        
        tickets = result.data or []
        open_count = len([t for t in tickets if t.get("status") == "open"])
        closed_count = len([t for t in tickets if t.get("status") == "closed"])
        in_progress_count = len([t for t in tickets if t.get("status") == "in_progress"])
        
        return jsonify({
            "ok": True,
            "stats": {
                "total": len(tickets),
                "open": open_count,
                "closed": closed_count,
                "in_progress": in_progress_count
            }
        }), 200
    except Exception as e:
        logger.error(f"Support stats error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
