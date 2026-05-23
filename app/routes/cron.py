# app/routes/cron.py
from __future__ import annotations

import os
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

bp = Blueprint("cron", __name__)

ROUTE_VERSION = "2026-05-23-v3-deadlines-referrals-merged-safe"
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------

def _clean(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value is None:
            return default
        return Decimal(str(value))
    except Exception:
        return default


def _parse_json() -> Dict[str, Any]:
    try:
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def _json_error(error: str, status: int = 400, **extra: Any):
    payload: Dict[str, Any] = {
        "ok": False,
        "error": error,
        "route_version": ROUTE_VERSION,
    }
    payload.update(extra)
    return jsonify(payload), status


def _get_supabase():
    from app.core.supabase_client import get_supabase_client

    return get_supabase_client(admin=True)


# -----------------------------------------------------------------------------
# Cron authorization
# -----------------------------------------------------------------------------

def _cron_secret() -> str:
    return _clean(os.getenv("CRON_SECRET") or os.getenv("ADMIN_CRON_SECRET"))


def _cron_authorized() -> bool:
    """
    Protected cron endpoints require CRON_SECRET or ADMIN_CRON_SECRET.

    Accepted locations:
    - X-Cron-Secret header
    - X-Webhook-Secret header
    - cron_secret query/body field, useful for manual browser testing only
    """
    secret = _cron_secret()
    if not secret:
        return False

    body = _parse_json() if request.method in {"POST", "PUT", "PATCH"} else {}
    incoming = (
        request.headers.get("X-Cron-Secret")
        or request.headers.get("X-Webhook-Secret")
        or request.args.get("cron_secret")
        or body.get("cron_secret")
        or ""
    )
    return _clean(incoming) == secret


def _require_cron_auth():
    if _cron_authorized():
        return None
    return _json_error(
        "unauthorized",
        401,
        message="Missing or invalid cron secret. Use X-Cron-Secret header.",
        secret_configured=bool(_cron_secret()),
    )


# -----------------------------------------------------------------------------
# Health / diagnostics
# -----------------------------------------------------------------------------

@bp.get("/cron/health")
def cron_health():
    return jsonify(
        {
            "ok": True,
            "service": "cron",
            "version": ROUTE_VERSION,
            "cron_secret_configured": bool(_cron_secret()),
            "deadline_send_enabled": _truthy(os.getenv("DEADLINE_REMINDER_SEND_ENABLED")),
            "payout_enabled_env": _truthy(os.getenv("REFERRAL_PAYOUT_ENABLED") or os.getenv("PAYOUT_ENABLED")),
        }
    ), 200


@bp.route("/cron/test", methods=["GET", "POST"])
def cron_test():
    auth_error = _require_cron_auth()
    if auth_error:
        return auth_error

    return jsonify(
        {
            "ok": True,
            "service": "cron",
            "route_version": ROUTE_VERSION,
            "method": request.method,
            "timestamp": _now_iso(),
            "message": "Cron blueprint is working.",
        }
    ), 200


# -----------------------------------------------------------------------------
# Deadline reminder cron
# -----------------------------------------------------------------------------

def _parse_date(value: Any) -> Optional[date]:
    raw = _clean(value)
    if not raw:
        return None
    try:
        if "T" in raw:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        return date.fromisoformat(raw[:10])
    except Exception:
        return None


def _deadline_due_today(row: Dict[str, Any], today: date) -> Tuple[bool, Dict[str, Any]]:
    due = _parse_date(row.get("due_date") or row.get("deadline_date"))
    days_before = _safe_int(row.get("reminder_days_before"), 7)

    debug = {
        "due_date": due.isoformat() if due else None,
        "reminder_days_before": days_before,
    }

    if not due:
        debug["reason"] = "missing_or_invalid_due_date"
        return False, debug

    days_until_due = (due - today).days
    debug["days_until_due"] = days_until_due

    if days_until_due < 0:
        debug["reason"] = "deadline_already_passed"
        return False, debug

    if days_until_due != days_before:
        debug["reason"] = "not_reminder_day"
        return False, debug

    return True, debug


def _deadline_message(row: Dict[str, Any]) -> str:
    tax_type = _clean(row.get("tax_type") or row.get("tax_name") or "Tax").upper()
    due_date = _clean(row.get("due_date") or row.get("deadline_date"))[:10]
    days = _safe_int(row.get("reminder_days_before"), 7)

    return (
        "🔔 Naija Tax Guide Deadline Reminder\n\n"
        f"Tax type: {tax_type}\n"
        f"Due date: {due_date}\n"
        f"Reminder: {days} day(s) before due date\n\n"
        "Please prepare early and confirm the exact requirement with the relevant Nigerian tax authority where needed."
    )


def _send_whatsapp(phone: str, message: str) -> Dict[str, Any]:
    phone = _clean(phone)
    if not phone:
        return {"ok": False, "error": "missing_whatsapp_phone"}
    try:
        from app.services.outbound_service import send_whatsapp_text

        sent = send_whatsapp_text(phone, message)
        return {"ok": bool(sent), "channel": "whatsapp", "to": phone}
    except Exception as exc:
        logger.exception("WhatsApp reminder send failed")
        return {"ok": False, "channel": "whatsapp", "to": phone, "error": f"{type(exc).__name__}: {exc}"}


def _send_telegram(chat_id: str, message: str) -> Dict[str, Any]:
    chat_id = _clean(chat_id)
    if not chat_id:
        return {"ok": False, "error": "missing_telegram_chat_id"}
    try:
        from app.services.outbound_service import send_telegram_text

        sent = send_telegram_text(chat_id, message)
        return {"ok": bool(sent), "channel": "telegram", "to": chat_id}
    except Exception as exc:
        logger.exception("Telegram reminder send failed")
        return {"ok": False, "channel": "telegram", "to": chat_id, "error": f"{type(exc).__name__}: {exc}"}


def _send_email(email: str, message: str) -> Dict[str, Any]:
    email = _clean(email)
    if not email:
        return {"ok": False, "error": "missing_email"}
    try:
        from app.core.mailer import send_mail

        result = send_mail(
            to=email,
            subject="Naija Tax Guide Deadline Reminder",
            text=message,
            html=f"<pre>{message}</pre>",
            debug=False,
        )
        return {"ok": bool(result.get("ok")), "channel": "email", "to": email, "result": result}
    except Exception as exc:
        logger.exception("Email reminder send failed")
        return {"ok": False, "channel": "email", "to": email, "error": f"{type(exc).__name__}: {exc}"}


def _update_deadline_last_error(deadline_id: str, message: str) -> None:
    deadline_id = _clean(deadline_id)
    if not deadline_id:
        return
    try:
        _get_supabase().table("tax_deadlines").update(
            {
                "reminder_last_error": _clean(message)[:900],
                "updated_at": _now_iso(),
            }
        ).eq("id", deadline_id).execute()
    except Exception:
        # Some databases may not yet have reminder_last_error. Never fail cron because of this.
        logger.exception("Could not update tax_deadlines.reminder_last_error")


def _normalize_modes(row: Dict[str, Any]) -> List[str]:
    raw = _clean(row.get("reminder_mode") or row.get("mode") or "whatsapp").lower()
    modes = [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    allowed = {"whatsapp", "email", "telegram", "sms"}
    return [mode for mode in modes if mode in allowed] or ["whatsapp"]


def _targets_for_mode(row: Dict[str, Any], mode: str) -> List[str]:
    if mode == "whatsapp":
        return [
            _clean(row.get("reminder_phone")),
            _clean(row.get("whatsapp_phone")),
            _clean(row.get("phone")),
            _clean(row.get("provider_user_id")),
        ]
    if mode == "telegram":
        return [
            _clean(row.get("reminder_telegram_chat_id")),
            _clean(row.get("telegram_chat_id")),
            _clean(row.get("telegram_user_id")),
        ]
    if mode == "email":
        return [
            _clean(row.get("reminder_email")),
            _clean(row.get("email")),
        ]
    return []


@bp.route("/cron/send-deadline-reminders", methods=["GET", "POST"])
def cron_send_deadline_reminders():
    """
    Finds tax_deadlines whose reminder date is today.

    Safety rule:
    - By default this route runs in dry-run mode.
    - Actual sending requires either:
        ?send=1
        or JSON {"send": true}
        or DEADLINE_REMINDER_SEND_ENABLED=true in env.

    This protects you from accidental repeated messages while testing cron-job.org.
    """
    auth_error = _require_cron_auth()
    if auth_error:
        return auth_error

    body = _parse_json()
    send_enabled = _truthy(body.get("send")) or _truthy(request.args.get("send")) or _truthy(os.getenv("DEADLINE_REMINDER_SEND_ENABLED"))
    limit = max(1, min(_safe_int(body.get("limit") or request.args.get("limit"), 500), 5000))
    today = _parse_date(body.get("today") or request.args.get("today")) or _now().date()

    try:
        res = (
            _get_supabase()
            .table("tax_deadlines")
            .select("*")
            .eq("enabled", True)
            .limit(limit)
            .execute()
        )
        rows = getattr(res, "data", None) or []
    except Exception as exc:
        logger.exception("Deadline reminder query failed")
        return _json_error(
            "deadline_reminder_query_failed",
            500,
            root_cause=f"{type(exc).__name__}: {exc}",
            hint="Confirm tax_deadlines table exists and Supabase service key has access.",
        )

    due_items: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    deliveries: List[Dict[str, Any]] = []

    for row in rows:
        row = row if isinstance(row, dict) else {}
        due_now, due_debug = _deadline_due_today(row, today)
        deadline_id = _clean(row.get("id"))
        if not due_now:
            skipped.append({"id": deadline_id, **due_debug})
            continue

        modes = _normalize_modes(row)
        message = _deadline_message(row)
        item = {
            "id": deadline_id,
            "account_id": _clean(row.get("account_id") or row.get("user_id")),
            "tax_type": _clean(row.get("tax_type") or row.get("tax_name")),
            "due_date": _clean(row.get("due_date") or row.get("deadline_date"))[:10],
            "reminder_days_before": _safe_int(row.get("reminder_days_before"), 7),
            "reminder_mode": modes,
        }
        due_items.append(item)

        if not send_enabled:
            continue

        for mode in modes:
            targets = [target for target in _targets_for_mode(row, mode) if target]
            if not targets:
                result = {"ok": False, "id": deadline_id, "channel": mode, "error": "missing_delivery_target"}
                deliveries.append(result)
                _update_deadline_last_error(deadline_id, f"{mode}: missing delivery target")
                continue

            # Use first available target for each mode.
            target = targets[0]
            if mode == "whatsapp":
                result = _send_whatsapp(target, message)
            elif mode == "telegram":
                result = _send_telegram(target, message)
            elif mode == "email":
                result = _send_email(target, message)
            else:
                result = {"ok": False, "channel": mode, "to": target, "error": "unsupported_mode"}

            result["id"] = deadline_id
            deliveries.append(result)
            if not result.get("ok"):
                _update_deadline_last_error(deadline_id, f"{mode}: {result.get('error') or 'send_failed'}")

    return jsonify(
        {
            "ok": True,
            "route_version": ROUTE_VERSION,
            "today": today.isoformat(),
            "dry_run": not send_enabled,
            "send_enabled": send_enabled,
            "checked_count": len(rows),
            "due_count": len(due_items),
            "skipped_count": len(skipped),
            "delivery_count": len(deliveries),
            "due_items": due_items,
            "deliveries": deliveries,
            "note": "Actual sending is disabled unless send=1 or DEADLINE_REMINDER_SEND_ENABLED=true.",
        }
    ), 200


@bp.get("/cron/deadlines/upcoming")
def cron_get_upcoming_deadlines():
    """Public helper for general upcoming Nigerian tax calendar items."""
    days_ahead = max(1, min(_safe_int(request.args.get("days"), 30), 366))
    try:
        from app.services.tax_deadline_service import get_upcoming_deadlines

        deadlines = get_upcoming_deadlines(days_ahead)
        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "days_ahead": days_ahead,
                "count": len(deadlines),
                "deadlines": deadlines,
            }
        ), 200
    except Exception as exc:
        logger.exception("Upcoming deadline lookup failed")
        return _json_error(
            "upcoming_deadlines_failed",
            500,
            root_cause=f"{type(exc).__name__}: {exc}",
        )


# -----------------------------------------------------------------------------
# Referral maturity / payout cron
# -----------------------------------------------------------------------------

def _payout_days() -> List[int]:
    raw = _clean(os.getenv("REFERRAL_PAYOUT_DAYS") or "15,30")
    out: List[int] = []
    for part in raw.split(","):
        day = _safe_int(part.strip(), 0)
        if 1 <= day <= 31:
            out.append(day)
    return out or [15, 30]


def _is_payout_window_today() -> bool:
    return _now().day in _payout_days()


def _unique_account_ids_from_rewards(status: str = "approved") -> List[str]:
    resp = (
        _get_supabase()
        .table("referral_rewards")
        .select("account_id")
        .eq("status", status)
        .execute()
    )
    rows = getattr(resp, "data", None) or []

    seen = set()
    account_ids: List[str] = []
    for row in rows:
        aid = _clean((row or {}).get("account_id"))
        if aid and aid not in seen:
            seen.add(aid)
            account_ids.append(aid)
    return account_ids


def _pick_account_ids(body: Dict[str, Any]) -> List[str]:
    raw = body.get("account_ids")
    if raw is None:
        raw = request.args.getlist("account_id")

    if raw is None or raw == []:
        single = _clean(body.get("account_id") or request.args.get("account_id"))
        return [single] if single else []

    values = raw if isinstance(raw, list) else [raw]

    out: List[str] = []
    seen = set()
    for value in values:
        text = _clean(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


@bp.route("/cron/referrals/mature", methods=["GET", "POST"])
def cron_referrals_mature():
    auth_error = _require_cron_auth()
    if auth_error:
        return auth_error

    body = _parse_json()
    reward_ids = body.get("reward_ids") or request.args.getlist("reward_id") or []
    if not isinstance(reward_ids, list):
        reward_ids = [reward_ids]

    account_id = _clean(body.get("account_id") or request.args.get("account_id")) or None
    limit = max(1, min(_safe_int(body.get("limit") or request.args.get("limit"), 2000), 10000))
    force = _truthy(body.get("force")) or _truthy(request.args.get("force"))

    try:
        from app.services.referral_service import mature_pending_rewards

        result = mature_pending_rewards(
            account_id=account_id,
            reward_ids=reward_ids,
            limit=limit,
            force=force,
        )
        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "result": result,
            }
        ), 200
    except Exception as exc:
        logger.exception("Referral maturity cron failed")
        return _json_error(
            "cron_referrals_mature_failed",
            500,
            root_cause=f"{type(exc).__name__}: {exc}",
            debug={
                "account_id": account_id,
                "reward_ids": reward_ids,
                "limit": limit,
                "force": force,
            },
        )


@bp.route("/cron/referrals/payout-batch", methods=["GET", "POST"])
def cron_referrals_payout_batch():
    auth_error = _require_cron_auth()
    if auth_error:
        return auth_error

    body = _parse_json()
    force = _truthy(body.get("force")) or _truthy(request.args.get("force"))
    limit = max(1, min(_safe_int(body.get("limit") or request.args.get("limit"), 5000), 20000))
    requested_account_ids = _pick_account_ids(body)

    try:
        from app.services.referral_service import mature_pending_rewards
        from app.services.payout_service import (
            approved_balance_for_account,
            create_payout_row,
            get_pending_or_processing_payout,
            get_payout_account,
            min_payout_amount,
            payout_currency,
            payout_enabled,
            payout_provider,
        )
    except Exception as exc:
        logger.exception("Referral payout imports failed")
        return _json_error(
            "referral_payout_import_failed",
            500,
            root_cause=f"{type(exc).__name__}: {exc}",
        )

    if not payout_enabled():
        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "skipped": True,
                "reason": "payout_disabled",
                "hint": "Set REFERRAL_PAYOUT_ENABLED=true or PAYOUT_ENABLED=true only when ready.",
            }
        ), 200

    try:
        maturity_result = mature_pending_rewards(limit=limit)

        if not force and not _is_payout_window_today():
            return jsonify(
                {
                    "ok": True,
                    "route_version": ROUTE_VERSION,
                    "skipped": True,
                    "reason": "not_payout_window_today",
                    "allowed_days": _payout_days(),
                    "today": _now().day,
                    "force": force,
                    "maturity_result": maturity_result,
                }
            ), 200

        account_ids = requested_account_ids or _unique_account_ids_from_rewards(status="approved")
        prepared: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        minimum = min_payout_amount()

        for account_id in account_ids:
            payout_account = get_payout_account(account_id)
            if not payout_account:
                skipped.append({"account_id": account_id, "reason": "missing_payout_account"})
                continue

            if not bool(payout_account.get("is_verified")):
                skipped.append(
                    {
                        "account_id": account_id,
                        "reason": "missing_verified_payout_account",
                        "payout_account_id": payout_account.get("id"),
                    }
                )
                continue

            existing = get_pending_or_processing_payout(account_id)
            if existing:
                skipped.append(
                    {
                        "account_id": account_id,
                        "reason": "existing_pending_or_processing_payout",
                        "payout": existing,
                    }
                )
                continue

            amount = approved_balance_for_account(account_id)
            if amount <= Decimal("0"):
                skipped.append(
                    {
                        "account_id": account_id,
                        "reason": "no_approved_balance",
                        "amount": str(amount),
                    }
                )
                continue

            if amount < minimum:
                skipped.append(
                    {
                        "account_id": account_id,
                        "reason": "below_minimum_payout_amount",
                        "amount": str(amount),
                        "minimum": str(minimum),
                    }
                )
                continue

            payout = create_payout_row(
                account_id=account_id,
                amount=_to_decimal(amount),
                currency=payout_currency(),
                provider=payout_provider(),
                status="pending",
                metadata={
                    "source": "cron_payout_batch",
                    "route_version": ROUTE_VERSION,
                    "forced": force,
                },
            )
            prepared.append(
                {
                    "account_id": account_id,
                    "amount": str(amount),
                    "payout": payout,
                }
            )

        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "force": force,
                "payout_provider": payout_provider(),
                "currency": payout_currency(),
                "minimum_payout_amount": str(minimum),
                "account_scope": "explicit" if requested_account_ids else "all_approved_accounts",
                "account_count": len(account_ids),
                "prepared_count": len(prepared),
                "skipped_count": len(skipped),
                "prepared": prepared,
                "skipped": skipped,
                "maturity_result": maturity_result,
            }
        ), 200

    except Exception as exc:
        logger.exception("Referral payout batch cron failed")
        return _json_error(
            "cron_referrals_payout_batch_failed",
            500,
            root_cause=f"{type(exc).__name__}: {exc}",
            debug={
                "force": force,
                "limit": limit,
                "requested_account_ids": requested_account_ids,
            },
        )
