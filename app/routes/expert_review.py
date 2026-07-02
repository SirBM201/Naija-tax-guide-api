# app/routes/expert_review.py
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from app.core.supabase_client import get_supabase_client
from app.services.ai_service import classify_tax_safety_risk, classify_source_sensitivity
from app.services.web_auth_service import get_account_id_from_request

logger = logging.getLogger(__name__)

bp = Blueprint("expert_review", __name__)

EXPERT_REVIEW_ROUTE_VERSION = "2026-07-03-v1-ticket-workflow"

EXPERT_REVIEW_PACKAGES: list[dict[str, Any]] = [
    {
        "code": "triage",
        "name": "Professional Review Triage",
        "status": "available",
        "price_note": "Initial routing and scope review; final professional fees may depend on the matter.",
        "sla_note": "Target first response: 1-2 business days after complete information is received.",
        "best_for": [
            "Understanding whether a matter needs professional help",
            "Preparing facts and documents before speaking with a tax professional",
            "Checking whether AI guidance is enough or risky",
        ],
    },
    {
        "code": "notice_review",
        "name": "Tax Notice / Penalty Review",
        "status": "request_only",
        "price_note": "Quoted after scope review; not an instant AI service.",
        "sla_note": "Urgent notices should be handled with a qualified professional immediately.",
        "best_for": [
            "Audit letters",
            "Official assessments",
            "Penalty notices",
            "Objections or appeal preparation",
        ],
    },
    {
        "code": "filing_review",
        "name": "Formal Filing Review",
        "status": "request_only",
        "price_note": "Quoted after scope review based on entity type, tax head, and filing period.",
        "sla_note": "Timeline depends on records, due date, and reviewer availability.",
        "best_for": [
            "Company tax filing questions",
            "VAT/WHT/PAYE filing readiness",
            "Back-duty exposure",
            "High-value filing decisions",
        ],
    },
]


def _sb():
    return get_supabase_client(admin=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _clip(value: Any, limit: int = 800) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _ticket_id() -> str:
    return f"NTR-{str(uuid.uuid4()).split('-')[0].upper()}"


def _json_error(message: str, status: int = 400, **extra: Any):
    return jsonify({"ok": False, "error": message, **extra}), status


def _auth_account_id() -> Tuple[Optional[str], Dict[str, Any]]:
    account_id, auth_debug = get_account_id_from_request(request)
    return account_id, (auth_debug or {})


def _schema_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("column", "schema cache", "pgrst204", "does not exist", "could not find"))


def _safe_insert(table: str, payloads: List[Dict[str, Any]]):
    last_error: Optional[Exception] = None
    for payload in payloads:
        clean_payload = {k: v for k, v in payload.items() if v is not None}
        try:
            return _sb().table(table).insert(clean_payload).execute()
        except Exception as exc:
            last_error = exc
            if _schema_error(exc):
                logger.warning("Expert review insert fallback for %s: %s", table, exc)
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError(f"No payload supplied for {table}")


def _risk_priority(question: str, user_priority: str = "") -> str:
    requested = _clean(user_priority).lower()
    if requested in {"low", "normal", "high", "urgent"}:
        return requested
    route = classify_tax_safety_risk(question)
    source_sensitive = classify_source_sensitivity(question)
    if route == "escalate":
        return "high"
    if source_sensitive == "source_sensitive":
        return "high"
    return "normal"


def _subject_from_body(body: Dict[str, Any], question: str) -> str:
    subject = _clean(body.get("subject"))
    if subject:
        return _clip(subject, 180)
    if question:
        return _clip(f"Professional review request - {question[:100]}", 180)
    return "Professional review request"


@bp.get("/expert-review/health")
def expert_review_health():
    return jsonify(
        {
            "ok": True,
            "service": "expert_review",
            "version": EXPERT_REVIEW_ROUTE_VERSION,
            "endpoints": [
                "GET /expert-review/health",
                "GET /expert-review/packages",
                "POST /expert-review/request",
            ],
            "note": "Creates a tracked request for human/professional review triage; it is not instant professional representation.",
        }
    ), 200


@bp.get("/expert-review/packages")
def expert_review_packages():
    return jsonify(
        {
            "ok": True,
            "packages": EXPERT_REVIEW_PACKAGES,
            "disclaimer": "Professional review requests are triaged by humans. Availability, pricing, and scope can depend on the facts and reviewer availability.",
        }
    ), 200


@bp.post("/expert-review/request")
def expert_review_request():
    account_id, auth_debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        body = {}

    question = _clean(body.get("question") or body.get("issue") or body.get("message"))
    details = _clean(body.get("details") or body.get("message") or body.get("description"))
    package_code = _clean(body.get("package_code") or body.get("package") or "triage").lower()
    if package_code not in {pkg["code"] for pkg in EXPERT_REVIEW_PACKAGES}:
        package_code = "triage"

    if not question and not details:
        return _json_error("question_or_details_required", 400)

    priority = _risk_priority(question or details, _clean(body.get("priority")))
    route = classify_tax_safety_risk(question or details)
    source_sensitivity = classify_source_sensitivity(question or details)
    now = _now()
    ticket_id = _ticket_id()
    subject = _subject_from_body(body, question or details)

    context_lines = [
        "Professional review request",
        f"Package: {package_code}",
        f"Safety route: {route}",
        f"Source sensitivity: {source_sensitivity}",
        f"Priority: {priority}",
        "",
        "User question / issue:",
        question or "Not provided",
    ]
    if details and details != question:
        context_lines.extend(["", "Additional details:", details])
    if body.get("payment_reference"):
        context_lines.extend(["", f"Payment/reference: {_clean(body.get('payment_reference'))}"])
    message = "\n".join(context_lines).strip()

    ticket_payloads = [
        {
            "ticket_id": ticket_id,
            "account_id": account_id,
            "subject": subject,
            "category": "professional_review",
            "priority": priority,
            "status": "open",
            "channel": "web",
            "message": message,
            "last_message_preview": _clip(message.replace("\n", " "), 200),
            "last_reply_at": now,
            "last_reply_by": "user",
            "created_at": now,
            "updated_at": now,
        },
        {
            "ticket_id": ticket_id,
            "account_id": account_id,
            "subject": subject,
            "category": "professional_review",
            "priority": priority,
            "status": "open",
            "last_message_preview": _clip(message.replace("\n", " "), 200),
            "created_at": now,
            "updated_at": now,
        },
        {
            "ticket_id": ticket_id,
            "account_id": account_id,
            "subject": subject,
            "category": "professional_review",
            "status": "open",
            "created_at": now,
            "updated_at": now,
        },
    ]

    try:
        ticket_result = _safe_insert("support_tickets", ticket_payloads)
        rows = getattr(ticket_result, "data", None) or []
        ticket = rows[0] if isinstance(rows, list) and rows else {"ticket_id": ticket_id}

        ticket_pk = ticket.get("id") if isinstance(ticket, dict) else None
        message_payloads: List[Dict[str, Any]] = []
        if ticket_pk is not None:
            message_payloads.append(
                {
                    "support_ticket_id": ticket_pk,
                    "ticket_id": ticket_id,
                    "account_id": account_id,
                    "message": message,
                    "sender_type": "user",
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
        try:
            _safe_insert("support_ticket_messages", message_payloads)
        except Exception as msg_exc:
            logger.warning("Expert review message insert failed after ticket creation: %s", msg_exc)

        return jsonify(
            {
                "ok": True,
                "ticket_id": ticket_id,
                "ticket": ticket,
                "category": "professional_review",
                "priority": priority,
                "package_code": package_code,
                "safety_route": route,
                "source_sensitivity": source_sensitivity,
                "message": "Professional review request created. Track it from the Support page.",
            }
        ), 201
    except Exception as exc:
        logger.exception("Expert review request failed")
        return jsonify({"ok": False, "error": "expert_review_request_failed", "detail": str(exc)}), 500
