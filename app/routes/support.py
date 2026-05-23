# app/routes/support.py
from __future__ import annotations

import html
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from app.core.supabase_client import get_supabase_client
from app.services.mail_service import send_email
from app.services.web_auth_service import get_account_id_from_request

logger = logging.getLogger(__name__)

bp = Blueprint("support", __name__)

SUPPORT_ROUTE_VERSION = "2026-05-23-v2-web-support-schema-safe"


# ============================================================
# Helpers
# ============================================================


def _sb():
    """
    Return the backend Supabase admin client.

    The current backend exports Supabase clients as objects. Using the canonical
    getter avoids the old error:
        'SyncClient' object is not callable
    """
    return get_supabase_client(admin=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        or "support@naijataxguides.com"
    )


def _clean_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _clean_short(value: Any, default: str = "", limit: int = 250) -> str:
    text = _clean_text(value, default)
    return text[:limit]


def _json_error(message: str, status: int = 400, **extra: Any):
    payload = {"ok": False, "error": message, **extra}
    return jsonify(payload), status


def _auth_account_id() -> Tuple[Optional[str], Dict[str, Any]]:
    account_id, auth_debug = get_account_id_from_request(request)
    return account_id, (auth_debug or {})


def _is_schema_shape_error(exc: Exception) -> bool:
    """
    Detect common Supabase/PostgREST schema-shape errors so we can fall back to
    smaller payloads when live tables do not yet have every optional column.
    """
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "column",
            "does not exist",
            "could not find",
            "schema cache",
            "pgrst204",
            "pgrst205",
            "relation",
            "table",
        )
    ) and any(
        token in text
        for token in (
            "does not exist",
            "could not find",
            "schema cache",
            "pgrst204",
            "pgrst205",
            "not found",
        )
    )


def _safe_insert(table: str, payloads: List[Dict[str, Any]]):
    """
    Insert using the richest payload first, then progressively smaller payloads.

    This keeps the support route resilient if the live Supabase table has fewer
    columns than the frontend expects.
    """
    last_error: Optional[Exception] = None

    for payload in payloads:
        clean_payload = {k: v for k, v in payload.items() if v is not None}
        try:
            return _sb().table(table).insert(clean_payload).execute()
        except Exception as exc:
            last_error = exc
            if _is_schema_shape_error(exc):
                logger.warning("Insert fallback for %s after schema error: %s", table, exc)
                continue
            raise

    if last_error:
        raise last_error
    raise RuntimeError(f"No insert payload supplied for table {table}")


def _safe_update(table: str, where: Tuple[str, Any], payloads: List[Dict[str, Any]]):
    last_error: Optional[Exception] = None
    column, value = where

    for payload in payloads:
        clean_payload = {k: v for k, v in payload.items() if v is not None}
        try:
            return _sb().table(table).update(clean_payload).eq(column, value).execute()
        except Exception as exc:
            last_error = exc
            if _is_schema_shape_error(exc):
                logger.warning("Update fallback for %s after schema error: %s", table, exc)
                continue
            raise

    if last_error:
        raise last_error
    raise RuntimeError(f"No update payload supplied for table {table}")


def _normalize_status(status: Any) -> str:
    raw = _clean_text(status, "open").lower()
    allowed = {"open", "in_progress", "awaiting_user", "in_review", "resolved", "closed"}
    return raw if raw in allowed else "open"


def _normalize_category(value: Any) -> str:
    raw = _clean_text(value, "general").lower()
    aliases = {
        "issue type: general support": "general",
        "general support": "general",
        "subscription": "billing",
        "subscriptions": "billing",
        "payment": "billing",
        "payments": "billing",
        "billing or subscription": "billing",
        "credit": "credits",
        "credits or access": "credits",
        "whatsapp": "channels",
        "telegram": "channels",
        "whatsapp or telegram linking": "channels",
        "authentication": "login",
        "login issue": "login",
        "technical issue": "technical",
        "bug": "technical",
    }
    raw = aliases.get(raw, raw)
    allowed = {"general", "billing", "credits", "channels", "login", "technical"}
    return raw if raw in allowed else "general"


def _normalize_priority(value: Any) -> str:
    raw = _clean_text(value, "normal").lower()
    aliases = {"medium": "normal"}
    raw = aliases.get(raw, raw)
    allowed = {"low", "normal", "high", "urgent"}
    return raw if raw in allowed else "normal"


def _message_preview(message: str) -> str:
    compact = " ".join(_clean_text(message).split())
    return compact[:200]


def _public_ticket(ticket: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return ticket fields in a frontend-friendly shape.
    """
    public = dict(ticket or {})
    public["ticket_id"] = public.get("ticket_id") or str(public.get("id") or "")
    public["status"] = public.get("status") or "open"
    public["category"] = public.get("category") or "general"
    public["priority"] = public.get("priority") or "normal"
    public["subject"] = public.get("subject") or "Support request"
    public["last_message_preview"] = (
        public.get("last_message_preview")
        or public.get("message")
        or None
    )
    return public


def _public_message(message: Dict[str, Any], ticket_id: str) -> Dict[str, Any]:
    public = dict(message or {})
    sender_type = public.get("sender_type")
    if not sender_type:
        sender_type = "admin" if public.get("is_staff") else "user"

    public["ticket_id"] = public.get("ticket_id") or ticket_id
    public["sender_type"] = sender_type
    public["message"] = public.get("message") or ""
    return public


def _load_ticket_for_account(ticket_id: str, account_id: str) -> Optional[Dict[str, Any]]:
    result = (
        _sb()
        .table("support_tickets")
        .select("*")
        .eq("ticket_id", ticket_id)
        .eq("account_id", account_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0]


def _ticket_update_key(ticket: Dict[str, Any], ticket_id: str) -> Tuple[str, Any]:
    ticket_pk = ticket.get("id")
    if ticket_pk is not None:
        return "id", ticket_pk
    return "ticket_id", ticket_id


# ============================================================
# Routes
# ============================================================


@bp.get("/support/health")
def support_health():
    to_email = _support_to_email()
    return jsonify(
        {
            "ok": True,
            "route_group": "support",
            "service": "support",
            "version": SUPPORT_ROUTE_VERSION,
            "mail_ready": bool(to_email),
            "support_to_email": to_email or None,
            "auth_resolver": "web_auth_service.get_account_id_from_request",
            "supabase_client": "get_supabase_client(admin=True)",
            "endpoints": [
                "GET /support/health",
                "GET /support/stats",
                "GET /support/tickets",
                "GET /support/tickets/<ticket_id>",
                "POST /support",
                "POST /support/tickets/<ticket_id>/reply",
                "DELETE /support/tickets/<ticket_id>/close",
            ],
        }
    ), 200


@bp.get("/support/tickets")
def list_tickets():
    account_id, auth_debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    limit = request.args.get("limit", 50, type=int) or 50
    limit = max(1, min(limit, 100))

    try:
        result = (
            _sb()
            .table("support_tickets")
            .select("*")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        tickets = [_public_ticket(row) for row in (result.data or [])]
        return jsonify(
            {
                "ok": True,
                "tickets": tickets,
                "count": len(tickets),
                "account_id": account_id,
            }
        ), 200
    except Exception as exc:
        logger.exception("List tickets error")
        return jsonify({"ok": False, "error": "support_tickets_load_failed", "detail": str(exc)}), 500


@bp.get("/support/tickets/<ticket_id>")
def get_ticket(ticket_id: str):
    account_id, auth_debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    ticket_id = _clean_text(ticket_id)
    if not ticket_id:
        return _json_error("ticket_id_required", 400)

    try:
        raw_ticket = _load_ticket_for_account(ticket_id, account_id)
        if not raw_ticket:
            return jsonify({"ok": False, "error": "ticket_not_found"}), 404

        ticket = _public_ticket(raw_ticket)
        ticket_pk = ticket.get("id")

        messages: List[Dict[str, Any]] = []

        # Preferred schema: messages store ticket_id and account_id.
        try:
            msg_result = (
                _sb()
                .table("support_ticket_messages")
                .select("*")
                .eq("ticket_id", ticket_id)
                .eq("account_id", account_id)
                .order("created_at", desc=False)
                .execute()
            )
            messages = msg_result.data or []
        except Exception as exc:
            if not _is_schema_shape_error(exc):
                raise
            logger.warning("ticket_id message lookup fallback: %s", exc)

        # Fallback schema: messages store support_ticket_id.
        if not messages and ticket_pk is not None:
            try:
                msg_result = (
                    _sb()
                    .table("support_ticket_messages")
                    .select("*")
                    .eq("support_ticket_id", ticket_pk)
                    .order("created_at", desc=False)
                    .execute()
                )
                messages = msg_result.data or []
            except Exception as exc:
                if not _is_schema_shape_error(exc):
                    raise
                logger.warning("support_ticket_id message lookup fallback failed: %s", exc)

        return jsonify(
            {
                "ok": True,
                "ticket": ticket,
                "messages": [_public_message(row, ticket_id) for row in messages],
                "count": len(messages),
            }
        ), 200
    except Exception as exc:
        logger.exception("Get ticket error")
        return jsonify({"ok": False, "error": "support_ticket_load_failed", "detail": str(exc)}), 500


@bp.post("/support")
def submit_support():
    account_id, auth_debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    body = request.get_json(silent=True) or {}

    subject = _clean_short(body.get("subject"), limit=180)
    message = _clean_text(body.get("message"))

    # Frontend can send issueType, older backend expected category.
    category = _normalize_category(body.get("category") or body.get("issueType") or "general")
    priority = _normalize_priority(body.get("priority") or "normal")
    channel = _clean_short(body.get("channel"), "web", limit=80)

    full_name = _clean_short(body.get("fullName") or body.get("name"), limit=120)
    contact_email = _clean_short(body.get("contactEmail") or body.get("email"), limit=180)

    if not subject or not message:
        return jsonify({"ok": False, "error": "subject_and_message_required"}), 400

    if len(message) < 10:
        return jsonify({"ok": False, "error": "message_too_short"}), 400

    try:
        now = _now()
        new_ticket_id = _ticket_id()
        preview = _message_preview(message)

        rich_ticket_payload = {
            "ticket_id": new_ticket_id,
            "account_id": account_id,
            "subject": subject,
            "category": category,
            "priority": priority,
            "status": "open",
            "channel": channel,
            "message": message,
            "last_message_preview": preview,
            "last_reply_at": now,
            "last_reply_by": "user",
            "created_at": now,
            "updated_at": now,
        }

        mid_ticket_payload = {
            "ticket_id": new_ticket_id,
            "account_id": account_id,
            "subject": subject,
            "category": category,
            "priority": priority,
            "status": "open",
            "last_message_preview": preview,
            "created_at": now,
            "updated_at": now,
        }

        minimal_ticket_payload = {
            "ticket_id": new_ticket_id,
            "account_id": account_id,
            "subject": subject,
            "category": category,
            "status": "open",
            "created_at": now,
            "updated_at": now,
        }

        ticket_result = _safe_insert(
            "support_tickets",
            [rich_ticket_payload, mid_ticket_payload, minimal_ticket_payload],
        )

        if not ticket_result.data:
            return jsonify({"ok": False, "error": "failed_to_create_ticket"}), 500

        ticket = _public_ticket(ticket_result.data[0])
        ticket_pk = ticket.get("id")

        message_payloads: List[Dict[str, Any]] = []

        if ticket_pk is not None:
            message_payloads.append(
                {
                    "support_ticket_id": ticket_pk,
                    "ticket_id": new_ticket_id,
                    "account_id": account_id,
                    "message": message,
                    "sender_type": "user",
                    "sender_name": full_name or None,
                    "is_staff": False,
                    "is_internal_note": False,
                    "created_at": now,
                }
            )

        message_payloads.extend(
            [
                {
                    "ticket_id": new_ticket_id,
                    "account_id": account_id,
                    "message": message,
                    "sender_type": "user",
                    "is_internal_note": False,
                    "created_at": now,
                },
                {
                    "ticket_id": new_ticket_id,
                    "account_id": account_id,
                    "message": message,
                    "created_at": now,
                },
            ]
        )

        try:
            _safe_insert("support_ticket_messages", message_payloads)
        except Exception as msg_exc:
            # Ticket creation should not be rolled back just because the message
            # table schema is not yet aligned. The frontend still receives the
            # created ticket and can display the ticket-level message/preview.
            logger.warning("Support message insert failed after ticket creation: %s", msg_exc)

        support_email = _support_to_email()
        if support_email:
            try:
                safe_ticket_id = html.escape(new_ticket_id)
                safe_account_id = html.escape(str(account_id))
                safe_subject = html.escape(subject)
                safe_category = html.escape(category)
                safe_priority = html.escape(priority)
                safe_name = html.escape(full_name or "Not provided")
                safe_email = html.escape(contact_email or "Not provided")
                safe_message = html.escape(message).replace("\n", "<br>")

                text_body = (
                    f"New support ticket from account {account_id}\n\n"
                    f"Ticket ID: {new_ticket_id}\n"
                    f"Subject: {subject}\n"
                    f"Category: {category}\n"
                    f"Priority: {priority}\n"
                    f"Name: {full_name or 'Not provided'}\n"
                    f"Email: {contact_email or 'Not provided'}\n\n"
                    f"Message:\n{message}"
                )
                html_body = (
                    "<h3>New Support Ticket</h3>"
                    f"<p><strong>Ticket ID:</strong> {safe_ticket_id}</p>"
                    f"<p><strong>Account ID:</strong> {safe_account_id}</p>"
                    f"<p><strong>Subject:</strong> {safe_subject}</p>"
                    f"<p><strong>Category:</strong> {safe_category}</p>"
                    f"<p><strong>Priority:</strong> {safe_priority}</p>"
                    f"<p><strong>Name:</strong> {safe_name}</p>"
                    f"<p><strong>Email:</strong> {safe_email}</p>"
                    f"<p><strong>Message:</strong></p><p>{safe_message}</p>"
                )
                send_email(
                    to_email=support_email,
                    subject=f"[Naija Tax Guide Support] {new_ticket_id}: {subject}",
                    html_body=html_body,
                    text_body=text_body,
                )
            except Exception as mail_error:
                logger.warning("Support email notification failed: %s", mail_error)

        return jsonify(
            {
                "ok": True,
                "message": "Support ticket created successfully",
                "ticket_id": new_ticket_id,
                "ticket": ticket,
            }
        ), 201
    except Exception as exc:
        logger.exception("Submit support error")
        return jsonify({"ok": False, "error": "support_submit_failed", "detail": str(exc)}), 500


@bp.post("/support/tickets/<ticket_id>/reply")
def reply_ticket(ticket_id: str):
    account_id, auth_debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    body = request.get_json(silent=True) or {}
    message = _clean_text(body.get("message"))
    sender_name = _clean_short(body.get("senderName") or body.get("fullName"), limit=120)

    if not message:
        return jsonify({"ok": False, "error": "message_required"}), 400

    ticket_id = _clean_text(ticket_id)
    if not ticket_id:
        return jsonify({"ok": False, "error": "ticket_id_required"}), 400

    try:
        ticket = _load_ticket_for_account(ticket_id, account_id)
        if not ticket:
            return jsonify({"ok": False, "error": "ticket_not_found"}), 404

        ticket_pk = ticket.get("id")
        now = _now()
        preview = _message_preview(message)

        message_payloads: List[Dict[str, Any]] = []
        if ticket_pk is not None:
            message_payloads.append(
                {
                    "support_ticket_id": ticket_pk,
                    "ticket_id": ticket_id,
                    "account_id": account_id,
                    "message": message,
                    "sender_type": "user",
                    "sender_name": sender_name or None,
                    "is_staff": False,
                    "is_internal_note": False,
                    "created_at": now,
                }
            )

        message_payloads.extend(
            [
                {
                    "ticket_id": ticket_id,
                    "account_id": account_id,
                    "message": message,
                    "sender_type": "user",
                    "is_internal_note": False,
                    "created_at": now,
                },
                {
                    "ticket_id": ticket_id,
                    "account_id": account_id,
                    "message": message,
                    "created_at": now,
                },
            ]
        )

        _safe_insert("support_ticket_messages", message_payloads)

        update_payloads = [
            {
                "status": "open",
                "updated_at": now,
                "last_message_preview": preview,
                "last_reply_at": now,
                "last_reply_by": "user",
            },
            {
                "status": "open",
                "updated_at": now,
                "last_message_preview": preview,
            },
            {
                "status": "open",
                "updated_at": now,
            },
        ]

        _safe_update("support_tickets", _ticket_update_key(ticket, ticket_id), update_payloads)

        return jsonify(
            {
                "ok": True,
                "message": "Reply added successfully",
                "ticket_id": ticket_id,
            }
        ), 200
    except Exception as exc:
        logger.exception("Reply ticket error")
        return jsonify({"ok": False, "error": "support_reply_failed", "detail": str(exc)}), 500


@bp.delete("/support/tickets/<ticket_id>/close")
def close_ticket(ticket_id: str):
    account_id, auth_debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    ticket_id = _clean_text(ticket_id)
    if not ticket_id:
        return jsonify({"ok": False, "error": "ticket_id_required"}), 400

    try:
        ticket = _load_ticket_for_account(ticket_id, account_id)
        if not ticket:
            return jsonify({"ok": False, "error": "ticket_not_found"}), 404

        now = _now()
        result = _safe_update(
            "support_tickets",
            _ticket_update_key(ticket, ticket_id),
            [
                {"status": "closed", "closed_at": now, "updated_at": now},
                {"status": "closed", "updated_at": now},
            ],
        )

        if not result.data:
            return jsonify({"ok": False, "error": "ticket_close_failed"}), 500

        return jsonify({"ok": True, "message": "Ticket closed successfully", "ticket_id": ticket_id}), 200
    except Exception as exc:
        logger.exception("Close ticket error")
        return jsonify({"ok": False, "error": "support_close_failed", "detail": str(exc)}), 500


@bp.get("/support/stats")
def support_stats():
    account_id, auth_debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    try:
        result = (
            _sb()
            .table("support_tickets")
            .select("status")
            .eq("account_id", account_id)
            .execute()
        )

        tickets = result.data or []
        stats = {
            "total": len(tickets),
            "open": 0,
            "in_progress": 0,
            "awaiting_user": 0,
            "in_review": 0,
            "resolved": 0,
            "closed": 0,
        }

        for ticket in tickets:
            status = _normalize_status(ticket.get("status") or "open")
            stats[status] = stats.get(status, 0) + 1

        return jsonify({"ok": True, "stats": stats}), 200
    except Exception as exc:
        logger.exception("Support stats error")
        return jsonify({"ok": False, "error": "support_stats_failed", "detail": str(exc)}), 500
