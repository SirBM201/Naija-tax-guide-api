# app/routes/cron.py
from __future__ import annotations

import logging
import os
import hmac
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


bp = Blueprint("cron", __name__)

ROUTE_VERSION = "2026-05-28-v31a-cron-auth-deadline-safe-delivery"
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------

def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _truthy(value: Any) -> bool:
    return _lower(value) in {"1", "true", "yes", "y", "on", "send", "live"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_lagos() -> datetime:
    """
    Default business time for Nigerian tax reminders.

    If ZoneInfo is unavailable for any reason, UTC is used as a safe fallback.
    """
    if ZoneInfo is None:
        return _now_utc()
    try:
        return datetime.now(ZoneInfo("Africa/Lagos"))
    except Exception:
        return _now_utc()


def _now_iso() -> str:
    return _now_utc().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
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


def _clip(value: Any, limit: int = 900) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "..."


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


def _response_data(response: Any) -> List[Dict[str, Any]]:
    rows = getattr(response, "data", None) or []
    return rows if isinstance(rows, list) else []


def _request_value(body: Dict[str, Any], key: str, default: Any = None) -> Any:
    if key in body:
        return body.get(key)
    return request.args.get(key, default)


# -----------------------------------------------------------------------------
# Cron authorization
# -----------------------------------------------------------------------------

def _cron_secret() -> str:
    """
    Use CRON_SECRET as the official production env variable.

    ADMIN_CRON_SECRET and CRON_JOB_SECRET remain supported so older deployments
    do not break during transition.
    """
    return _clean(
        os.getenv("CRON_SECRET")
        or os.getenv("ADMIN_CRON_SECRET")
        or os.getenv("CRON_JOB_SECRET")
    )


def _get_supplied_cron_secret(body: Optional[Dict[str, Any]] = None) -> str:
    body = body if isinstance(body, dict) else {}

    authorization = _clean(request.headers.get("Authorization"))
    bearer_value = ""
    if authorization.lower().startswith("bearer "):
        bearer_value = authorization[7:].strip()

    header_value = (
        request.headers.get("X-Cron-Secret")
        or request.headers.get("X-Cron-Token")
        or request.headers.get("X-Webhook-Secret")
        or request.headers.get("X-Admin-Key")
        or ""
    )

    body_value = _clean(body.get("cron_secret") or body.get("cron_token") or body.get("token"))

    # Query-string tokens can leak into logs. Keep disabled unless intentionally enabled.
    query_value = ""
    if _truthy(os.getenv("CRON_ALLOW_QUERY_SECRET")):
        query_value = _clean(
            request.args.get("cron_secret")
            or request.args.get("cron_token")
            or request.args.get("token")
            or ""
        )

    return _clean(bearer_value or header_value or body_value or query_value)


def _cron_authorized(body: Optional[Dict[str, Any]] = None) -> bool:
    expected = _cron_secret()
    supplied = _get_supplied_cron_secret(body)
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


def _require_cron_auth(body: Optional[Dict[str, Any]] = None):
    if _cron_authorized(body):
        return None

    return _json_error(
        "unauthorized",
        401,
        message=(
            "Missing or invalid cron secret. Use Authorization: Bearer <CRON_SECRET> "
            "or X-Cron-Secret: <CRON_SECRET>."
        ),
        secret_configured=bool(_cron_secret()),
        accepted_headers=["Authorization: Bearer ...", "X-Cron-Secret", "X-Cron-Token"],
        query_secret_allowed=_truthy(os.getenv("CRON_ALLOW_QUERY_SECRET")),
    )


# -----------------------------------------------------------------------------
# Health / diagnostics
# -----------------------------------------------------------------------------

@bp.route("/cron/health", methods=["GET"])
def cron_health():
    return jsonify(
        {
            "ok": True,
            "service": "cron",
            "route_version": ROUTE_VERSION,
            "cron_secret_configured": bool(_cron_secret()),
            "query_secret_allowed": _truthy(os.getenv("CRON_ALLOW_QUERY_SECRET")),
            "deadline_send_enabled_env": _truthy(os.getenv("DEADLINE_REMINDER_SEND_ENABLED")),
            "payout_enabled_env": _truthy(os.getenv("REFERRAL_PAYOUT_ENABLED") or os.getenv("PAYOUT_ENABLED")),
            "server_time_utc": _now_iso(),
            "server_time_lagos": _now_lagos().isoformat(),
        }
    ), 200


@bp.route("/cron/test", methods=["GET", "POST"])
def cron_test():
    body = _parse_json()
    auth_error = _require_cron_auth(body)
    if auth_error:
        return auth_error

    return jsonify(
        {
            "ok": True,
            "service": "cron",
            "route_version": ROUTE_VERSION,
            "method": request.method,
            "timestamp_utc": _now_iso(),
            "timestamp_lagos": _now_lagos().isoformat(),
            "message": "Cron blueprint is working and authentication passed.",
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


def _parse_time(value: Any, default: time = time(9, 0)) -> time:
    raw = _clean(value)
    if not raw:
        return default

    try:
        part = raw[:5]
        hh, mm = part.split(":", 1)
        hour = int(hh)
        minute = int(mm)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
    except Exception:
        pass

    return default


def _display_time(value: Any) -> str:
    parsed = _parse_time(value)
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def _business_today(body: Dict[str, Any]) -> date:
    override = _parse_date(_request_value(body, "today"))
    if override:
        return override
    return _now_lagos().date()


def _business_now_time(body: Dict[str, Any]) -> time:
    override = _clean(_request_value(body, "now_time"))
    if override:
        return _parse_time(override, default=time(23, 59))
    return _now_lagos().time().replace(second=0, microsecond=0)


def _deadline_reminder_date(row: Dict[str, Any]) -> Optional[date]:
    due = _parse_date(row.get("due_date") or row.get("deadline_date"))
    if not due:
        return None
    days_before = max(0, _safe_int(row.get("reminder_days_before"), 7))
    try:
        from datetime import timedelta

        return due - timedelta(days=days_before)
    except Exception:
        return None


def _deadline_sent_key(row: Dict[str, Any], today: date) -> str:
    deadline_id = _clean(row.get("id"))
    due = _clean(row.get("due_date") or row.get("deadline_date"))[:10]
    days_before = _safe_int(row.get("reminder_days_before"), 7)
    mode = _clean(row.get("reminder_mode") or row.get("mode") or "whatsapp").lower()
    return f"deadline:{deadline_id}:{due}:{days_before}:{mode}:{today.isoformat()}"


def _already_sent_today(row: Dict[str, Any], sent_key: str, today: date) -> bool:
    existing_key = _clean(row.get("last_reminder_sent_key") or row.get("reminder_sent_key"))
    if existing_key and existing_key == sent_key:
        return True

    sent_at = _parse_date(row.get("last_reminder_sent_at") or row.get("reminder_sent_at"))
    return bool(sent_at and sent_at == today)


def _deadline_due_now(
    row: Dict[str, Any],
    today: date,
    now_time: time,
    *,
    ignore_time: bool = False,
    force: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    due = _parse_date(row.get("due_date") or row.get("deadline_date"))
    days_before = max(0, _safe_int(row.get("reminder_days_before"), 7))
    reminder_date = _deadline_reminder_date(row)
    reminder_time = _parse_time(row.get("reminder_time"), default=time(9, 0))
    sent_key = _deadline_sent_key(row, today)

    debug = {
        "due_date": due.isoformat() if due else None,
        "reminder_days_before": days_before,
        "reminder_date": reminder_date.isoformat() if reminder_date else None,
        "reminder_time": f"{reminder_time.hour:02d}:{reminder_time.minute:02d}",
        "today": today.isoformat(),
        "now_time": f"{now_time.hour:02d}:{now_time.minute:02d}",
        "sent_key": sent_key,
    }

    if not due:
        debug["reason"] = "missing_or_invalid_due_date"
        return False, debug

    if not bool(row.get("enabled", True)):
        debug["reason"] = "disabled"
        return False, debug

    if due < today:
        debug["reason"] = "deadline_already_passed"
        return False, debug

    if reminder_date != today:
        debug["reason"] = "not_reminder_date"
        return False, debug

    if not ignore_time and now_time < reminder_time:
        debug["reason"] = "before_reminder_time"
        return False, debug

    if not force and _already_sent_today(row, sent_key, today):
        debug["reason"] = "already_sent_today"
        return False, debug

    debug["reason"] = "due_now"
    return True, debug


def _deadline_message(row: Dict[str, Any]) -> str:
    tax_type = _clean(row.get("tax_type") or row.get("tax_name") or "Tax").upper()
    due_date = _clean(row.get("due_date") or row.get("deadline_date"))[:10]
    days = max(0, _safe_int(row.get("reminder_days_before"), 7))

    return (
        "🔔 Naija Tax Guide Deadline Reminder\n\n"
        f"Tax type: {tax_type}\n"
        f"Due date: {due_date}\n"
        f"Reminder: {days} day(s) before due date\n\n"
        "Please prepare early, keep your supporting records, and confirm the exact "
        "requirement with the relevant Nigerian tax authority where needed."
    )


def _send_whatsapp(phone: str, message: str) -> Dict[str, Any]:
    phone = _clean(phone)
    if not phone:
        return {"ok": False, "channel": "whatsapp", "error": "missing_whatsapp_phone"}

    try:
        from app.services.outbound_service import send_whatsapp_text

        sent = send_whatsapp_text(phone, message)
        return {"ok": bool(sent), "channel": "whatsapp", "to": phone}
    except Exception as exc:
        logger.exception("WhatsApp reminder send failed")
        return {"ok": False, "channel": "whatsapp", "to": phone, "error": f"{type(exc).__name__}: {_clip(exc)}"}


def _send_telegram(chat_id: str, message: str) -> Dict[str, Any]:
    chat_id = _clean(chat_id)
    if not chat_id:
        return {"ok": False, "channel": "telegram", "error": "missing_telegram_chat_id"}

    try:
        from app.services.outbound_service import send_telegram_text

        sent = send_telegram_text(chat_id, message)
        return {"ok": bool(sent), "channel": "telegram", "to": chat_id}
    except Exception as exc:
        logger.exception("Telegram reminder send failed")
        return {"ok": False, "channel": "telegram", "to": chat_id, "error": f"{type(exc).__name__}: {_clip(exc)}"}


def _send_email(email: str, message: str) -> Dict[str, Any]:
    email = _clean(email)
    if not email:
        return {"ok": False, "channel": "email", "error": "missing_email"}

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
        return {"ok": False, "channel": "email", "to": email, "error": f"{type(exc).__name__}: {_clip(exc)}"}


def _normalize_modes(row: Dict[str, Any]) -> List[str]:
    raw = _lower(row.get("reminder_mode") or row.get("mode") or "whatsapp")
    modes = [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    allowed = {"whatsapp", "email", "telegram", "sms"}
    normalized = [mode for mode in modes if mode in allowed]
    return normalized or ["whatsapp"]


def _identity_targets(account_id: str, mode: str) -> List[str]:
    account_id = _clean(account_id)
    mode = _lower(mode)
    if not account_id or mode not in {"whatsapp", "telegram"}:
        return []

    try:
        res = (
            _get_supabase()
            .table("channel_identities")
            .select("provider_user_id,metadata,channel_type,is_verified,verified")
            .eq("account_id", account_id)
            .eq("channel_type", mode)
            .limit(5)
            .execute()
        )
        rows = _response_data(res)
    except Exception:
        logger.exception("Could not load channel_identities for deadline target fallback")
        return []

    targets: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        # Prefer verified identities where the column exists, but do not block older rows
        # if the field is absent.
        if row.get("is_verified") is False or row.get("verified") is False:
            continue

        provider_user_id = _clean(row.get("provider_user_id"))
        if provider_user_id:
            targets.append(provider_user_id)

        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if isinstance(metadata, dict):
            for key in ("chat_id", "telegram_chat_id", "wa_id", "phone", "provider_user_id"):
                value = _clean(metadata.get(key))
                if value:
                    targets.append(value)

    seen = set()
    unique: List[str] = []
    for target in targets:
        if target and target not in seen:
            seen.add(target)
            unique.append(target)
    return unique


def _targets_for_mode(row: Dict[str, Any], mode: str) -> List[str]:
    mode = _lower(mode)
    account_id = _clean(row.get("account_id") or row.get("user_id"))

    candidates: List[str] = []
    if mode == "whatsapp":
        candidates.extend(
            [
                _clean(row.get("reminder_phone")),
                _clean(row.get("whatsapp_phone")),
                _clean(row.get("phone")),
                _clean(row.get("provider_user_id")),
            ]
        )
        candidates.extend(_identity_targets(account_id, "whatsapp"))

    elif mode == "telegram":
        candidates.extend(
            [
                _clean(row.get("reminder_telegram_chat_id")),
                _clean(row.get("telegram_chat_id")),
                _clean(row.get("telegram_user_id")),
                _clean(row.get("reminder_phone")),
                _clean(row.get("provider_user_id")),
            ]
        )
        candidates.extend(_identity_targets(account_id, "telegram"))

    elif mode == "email":
        candidates.extend(
            [
                _clean(row.get("reminder_email")),
                _clean(row.get("email")),
            ]
        )

    seen = set()
    out: List[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _update_deadline_attempt(deadline_id: str, *, error: Optional[str] = None) -> None:
    deadline_id = _clean(deadline_id)
    if not deadline_id:
        return

    payload = {
        "reminder_last_attempt_at": _now_iso(),
        "reminder_last_error": _clip(error) if error else None,
        "updated_at": _now_iso(),
    }

    try:
        _get_supabase().table("tax_deadlines").update(payload).eq("id", deadline_id).execute()
    except Exception:
        # Optional tracking columns may not exist until the Batch 31A SQL is run.
        try:
            _get_supabase().table("tax_deadlines").update({"updated_at": _now_iso()}).eq("id", deadline_id).execute()
        except Exception:
            logger.exception("Could not update deadline attempt tracking")


def _mark_deadline_sent(deadline_id: str, sent_key: str) -> None:
    deadline_id = _clean(deadline_id)
    if not deadline_id:
        return

    payload = {
        "last_reminder_sent_at": _now_iso(),
        "last_reminder_sent_key": sent_key,
        "reminder_last_attempt_at": _now_iso(),
        "reminder_last_error": None,
        "updated_at": _now_iso(),
    }

    try:
        _get_supabase().table("tax_deadlines").update(payload).eq("id", deadline_id).execute()
    except Exception:
        # If optional columns have not been added, never fail the send.
        logger.exception("Could not mark deadline reminder as sent; run Batch 31A SQL optional columns")


def _select_deadline_rows(body: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    query = (
        _get_supabase()
        .table("tax_deadlines")
        .select("*")
        .eq("enabled", True)
        .limit(limit)
    )

    account_id = _clean(_request_value(body, "account_id"))
    deadline_id = _clean(_request_value(body, "deadline_id") or _request_value(body, "id"))
    tax_type = _clean(_request_value(body, "tax_type")).upper()

    if account_id:
        query = query.eq("account_id", account_id)
    if deadline_id:
        query = query.eq("id", deadline_id)
    if tax_type:
        query = query.eq("tax_type", tax_type)

    try:
        query = query.order("created_at", desc=True)
    except Exception:
        pass

    res = query.execute()
    return _response_data(res)


@bp.route("/cron/send-deadline-reminders", methods=["GET", "POST"])
def cron_send_deadline_reminders():
    """
    Finds and optionally sends due deadline reminders from public.tax_deadlines.

    Safety rules:
    - Authentication is mandatory.
    - Dry run is the default.
    - Actual sending requires send=1 in query/body OR DEADLINE_REMINDER_SEND_ENABLED=true.
    - Optional Batch 31A tracking columns prevent duplicate same-day sends.
    """
    body = _parse_json()
    auth_error = _require_cron_auth(body)
    if auth_error:
        return auth_error

    send_enabled = (
        _truthy(_request_value(body, "send"))
        or _truthy(os.getenv("DEADLINE_REMINDER_SEND_ENABLED"))
    )
    force = _truthy(_request_value(body, "force"))
    ignore_time = _truthy(_request_value(body, "ignore_time"))

    limit = max(1, min(_safe_int(_request_value(body, "limit"), 500), 5000))
    today = _business_today(body)
    now_time = _business_now_time(body)

    try:
        rows = _select_deadline_rows(body, limit)
    except Exception as exc:
        logger.exception("Deadline reminder query failed")
        return _json_error(
            "deadline_reminder_query_failed",
            500,
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
            hint="Confirm public.tax_deadlines exists and Supabase service key has access.",
        )

    due_items: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    deliveries: List[Dict[str, Any]] = []
    already_sent_count = 0

    for row in rows:
        row = row if isinstance(row, dict) else {}
        deadline_id = _clean(row.get("id"))

        due_now, due_debug = _deadline_due_now(
            row,
            today,
            now_time,
            ignore_time=ignore_time,
            force=force,
        )

        if not due_now:
            if due_debug.get("reason") == "already_sent_today":
                already_sent_count += 1
            skipped.append({"id": deadline_id, **due_debug})
            continue

        modes = _normalize_modes(row)
        message = _deadline_message(row)
        sent_key = _deadline_sent_key(row, today)

        item = {
            "id": deadline_id,
            "account_id": _clean(row.get("account_id") or row.get("user_id")),
            "tax_type": _clean(row.get("tax_type") or row.get("tax_name")),
            "due_date": _clean(row.get("due_date") or row.get("deadline_date"))[:10],
            "reminder_days_before": max(0, _safe_int(row.get("reminder_days_before"), 7)),
            "reminder_time": _display_time(row.get("reminder_time")),
            "reminder_mode": modes,
            "sent_key": sent_key,
        }
        due_items.append(item)

        if not send_enabled:
            continue

        row_delivery_results: List[Dict[str, Any]] = []
        for mode in modes:
            if mode == "sms":
                result = {"ok": False, "id": deadline_id, "channel": "sms", "error": "sms_not_configured"}
                deliveries.append(result)
                row_delivery_results.append(result)
                continue

            targets = _targets_for_mode(row, mode)
            if not targets:
                result = {"ok": False, "id": deadline_id, "channel": mode, "error": "missing_delivery_target"}
                deliveries.append(result)
                row_delivery_results.append(result)
                continue

            # Use the first available target for each mode to avoid duplicate sends
            # when both reminder_phone and channel_identities point to the same user.
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
            row_delivery_results.append(result)

        if any(bool(result.get("ok")) for result in row_delivery_results):
            _mark_deadline_sent(deadline_id, sent_key)
        else:
            joined_errors = "; ".join(
                _clean(result.get("error") or "send_failed")
                for result in row_delivery_results
                if not result.get("ok")
            )
            _update_deadline_attempt(deadline_id, error=joined_errors or "send_failed")

    return jsonify(
        {
            "ok": True,
            "route_version": ROUTE_VERSION,
            "today": today.isoformat(),
            "now_time": f"{now_time.hour:02d}:{now_time.minute:02d}",
            "dry_run": not send_enabled,
            "send_enabled": send_enabled,
            "force": force,
            "ignore_time": ignore_time,
            "checked_count": len(rows),
            "due_count": len(due_items),
            "skipped_count": len(skipped),
            "already_sent_count": already_sent_count,
            "delivery_count": len(deliveries),
            "due_items": due_items,
            "deliveries": deliveries,
            "skipped_sample": skipped[:20],
            "note": (
                "Actual sending is disabled unless send=1 or DEADLINE_REMINDER_SEND_ENABLED=true. "
                "Run Batch 31A SQL optional tracking columns to prevent duplicate same-day sends."
            ),
        }
    ), 200


@bp.route("/cron/deadlines/upcoming", methods=["GET"])
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
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
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
    return _now_lagos().day in _payout_days()


def _unique_account_ids_from_rewards(status: str = "approved") -> List[str]:
    resp = (
        _get_supabase()
        .table("referral_rewards")
        .select("account_id")
        .eq("status", status)
        .execute()
    )
    rows = _response_data(resp)

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
    body = _parse_json()
    auth_error = _require_cron_auth(body)
    if auth_error:
        return auth_error

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
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
            debug={
                "account_id": account_id,
                "reward_ids": reward_ids,
                "limit": limit,
                "force": force,
            },
        )


@bp.route("/cron/referrals/payout-batch", methods=["GET", "POST"])
def cron_referrals_payout_batch():
    body = _parse_json()
    auth_error = _require_cron_auth(body)
    if auth_error:
        return auth_error

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
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
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
                    "today": _now_lagos().day,
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
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
            debug={
                "force": force,
                "limit": limit,
                "requested_account_ids": requested_account_ids,
            },
        )
