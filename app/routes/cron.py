# app/routes/cron.py
from __future__ import annotations

import os
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import requests
from flask import Blueprint, current_app, jsonify, request
from zoneinfo import ZoneInfo


cron_bp = Blueprint("cron", __name__)
bp = cron_bp


# ============================================================
# Batch 31D: Final production cron monitoring response cleanup
# Route version:
# 2026-05-28-v31d-production-cron-monitoring-cleanup
#
# Purpose:
# - Keep cron-job.org history clean in production.
# - Keep full debug output available only with debug=1.
# - Preserve Batch 31A safe deadline delivery behavior.
# - Prevent duplicate same-day reminder delivery using sent keys.
# - Support Telegram and WhatsApp reminder delivery.
# ============================================================

CRON_ROUTE_VERSION = "2026-05-28-v31d-production-cron-monitoring-cleanup"

DEFAULT_TIMEZONE = os.getenv("APP_TIMEZONE", "Africa/Lagos")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

SUPPORTED_REMINDER_MODES = {"telegram", "whatsapp"}


# ============================================================
# Generic helpers
# ============================================================

def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _app_now() -> datetime:
    try:
        return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    except Exception:
        return datetime.now(timezone.utc)


def _arg(name: str, default: Any = None) -> Any:
    """
    Reads from query string first, then JSON body, then form body.
    Useful for GET from cron-job.org and manual POST tests.
    """
    if name in request.args:
        return request.args.get(name)

    try:
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict) and name in payload:
            return payload.get(name)
    except Exception:
        pass

    if name in request.form:
        return request.form.get(name)

    return default


def _cron_debug_requested() -> bool:
    """
    Debug mode is manual only.
    Do not add debug=1 to cron-job.org production URL.
    """
    return _truthy(_arg("debug"))


def _normalize_date(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, datetime):
        return value.date().isoformat()

    text = str(value).strip()
    if not text:
        return None

    # Accept "2026-09-18", "2026-09-18T00:00:00", etc.
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if not match:
        return None

    return match.group(0)


def _parse_date(value: Any) -> date | None:
    normalized = _normalize_date(value)
    if not normalized:
        return None

    try:
        return date.fromisoformat(normalized)
    except Exception:
        return None


def _normalize_time(value: Any, default: str = "09:00") -> str:
    if value is None:
        return default

    if isinstance(value, time):
        return value.strftime("%H:%M")

    text = str(value).strip()
    if not text:
        return default

    # Accept "09:00", "09:00:00", "9:00"
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return default

    hour = max(0, min(23, _safe_int(match.group(1), 9)))
    minute = max(0, min(59, _safe_int(match.group(2), 0)))

    return f"{hour:02d}:{minute:02d}"


def _normalize_mode(value: Any) -> list[str]:
    """
    Supports:
    - "telegram"
    - "whatsapp"
    - "telegram,whatsapp"
    - ["telegram", "whatsapp"]
    """
    if value is None:
        return []

    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[,/| ]+", str(value or "").strip())

    modes: list[str] = []
    for item in raw_items:
        mode = str(item or "").strip().lower()
        if not mode:
            continue

        if mode in {"wa", "whats_app", "whatsapp"}:
            mode = "whatsapp"
        elif mode in {"tg", "tele", "telegram"}:
            mode = "telegram"

        if mode and mode not in modes:
            modes.append(mode)

    return modes


def _mask_target(value: Any) -> str | None:
    """
    Masks phone numbers / chat IDs in debug response.
    Example:
      96566805262 -> 965****5262
      5351975324  -> 535****5324
    """
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if len(text) <= 6:
        return "***"

    return f"{text[:3]}****{text[-4:]}"


def _compact_delivery(delivery: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel": delivery.get("channel"),
        "ok": bool(delivery.get("ok")),
        "to": _mask_target(delivery.get("to")),
        "error": delivery.get("error") or delivery.get("reason"),
    }


def _json_error(
    *,
    message: str,
    status_code: int = 400,
    extra: dict[str, Any] | None = None,
):
    payload: dict[str, Any] = {
        "ok": False,
        "route_version": CRON_ROUTE_VERSION,
        "error": message,
    }

    if _cron_debug_requested() and extra:
        payload["debug"] = extra

    return jsonify(payload), status_code


def _build_cron_response(
    *,
    today: str,
    now_time: str,
    send_enabled: bool,
    dry_run: bool,
    force: bool,
    ignore_time: bool,
    checked_count: int,
    due_count: int,
    delivery_count: int,
    already_sent_count: int,
    skipped_count: int,
    deliveries: list[dict[str, Any]] | None = None,
    due_items: list[dict[str, Any]] | None = None,
    skipped_sample: list[dict[str, Any]] | None = None,
    failed_count: int = 0,
    extra_debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Production default:
      Clean summary only.

    Debug mode:
      Shows detailed diagnostics only when debug=1 is passed manually.
    """
    deliveries = deliveries or []
    due_items = due_items or []
    skipped_sample = skipped_sample or []
    extra_debug = extra_debug or {}

    debug = _cron_debug_requested()

    if failed_count <= 0:
        failed_count = sum(1 for item in deliveries if not bool(item.get("ok")))

    if delivery_count > 0 and failed_count == 0:
        status = "delivered"
    elif delivery_count > 0 and failed_count > 0:
        status = "completed_with_delivery_errors"
    elif due_count > 0 and dry_run:
        status = "due_dry_run"
    elif already_sent_count > 0 and due_count == 0:
        status = "already_sent_or_no_new_due"
    elif checked_count == 0:
        status = "no_active_reminders_checked"
    elif due_count == 0:
        status = "no_due_reminders"
    else:
        status = "completed"

    response: dict[str, Any] = {
        "ok": True,
        "route_version": CRON_ROUTE_VERSION,
        "mode": "debug" if debug else "production",
        "status": status,
        "today": today,
        "now_time": now_time,
        "send_enabled": bool(send_enabled),
        "dry_run": bool(dry_run),
        "force": bool(force),
        "ignore_time": bool(ignore_time),
        "summary": {
            "checked": _safe_int(checked_count),
            "due": _safe_int(due_count),
            "delivered": _safe_int(delivery_count),
            "already_sent": _safe_int(already_sent_count),
            "skipped": _safe_int(skipped_count),
            "failed": _safe_int(failed_count),
        },
    }

    if debug:
        response["debug"] = {
            "deliveries": [_compact_delivery(item) for item in deliveries],
            "due_items": due_items,
            "skipped_sample": skipped_sample,
            **extra_debug,
        }

    return response


# ============================================================
# Supabase client helper
# ============================================================

def _get_supabase_client():
    """
    Flexible Supabase loader.

    Supports common project patterns:
    - app.core.supabase_client.get_supabase_admin_client()
    - app.core.supabase_client.get_supabase_client()
    - app.core.supabase_client.supabase_admin
    - app.core.supabase_client.supabase
    - direct create_client fallback from env
    """
    try:
        from app.core import supabase_client as sc  # type: ignore

        for name in (
            "get_supabase_admin_client",
            "get_admin_supabase_client",
            "get_service_supabase_client",
            "get_supabase_client",
        ):
            fn = getattr(sc, name, None)
            if callable(fn):
                client = fn()
                if client is not None:
                    return client

        for name in (
            "supabase_admin",
            "admin_supabase",
            "service_supabase",
            "supabase",
            "client",
        ):
            client = getattr(sc, name, None)
            if client is not None:
                return client

    except Exception as exc:
        current_app.logger.warning("cron.supabase_import_warning: %s", exc)

    try:
        from supabase import create_client  # type: ignore

        url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
        key = (
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_SERVICE_KEY")
            or os.getenv("SUPABASE_KEY")
            or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
        )

        if not url or not key:
            raise RuntimeError(
                "Missing SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY/SUPABASE_KEY env vars."
            )

        return create_client(url, key)

    except Exception as exc:
        raise RuntimeError(f"Could not create Supabase client: {exc}") from exc


def _execute_data(query) -> list[dict[str, Any]]:
    result = query.execute()
    data = getattr(result, "data", None)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return []


def _safe_update_deadline(
    sb,
    deadline_id: str,
    values: dict[str, Any],
) -> tuple[bool, str | None]:
    try:
        sb.table("tax_deadlines").update(values).eq("id", deadline_id).execute()
        return True, None
    except Exception as exc:
        current_app.logger.warning(
            "cron.deadline_update_failed id=%s error=%s",
            deadline_id,
            exc,
        )
        return False, str(exc)


# ============================================================
# Cron authentication
# ============================================================

def _configured_cron_secret() -> str | None:
    return (
        os.getenv("DEADLINE_CRON_SECRET")
        or os.getenv("CRON_SECRET")
        or os.getenv("CRON_JOB_SECRET")
        or os.getenv("NTG_CRON_SECRET")
    )


def _provided_cron_secret() -> str | None:
    auth_header = request.headers.get("Authorization") or ""
    bearer = ""

    if auth_header.lower().startswith("bearer "):
        bearer = auth_header.split(" ", 1)[1].strip()

    return (
        request.headers.get("X-Cron-Secret")
        or request.headers.get("X-CRON-SECRET")
        or request.headers.get("X-NTG-Cron-Secret")
        or bearer
        or _arg("cron_secret")
        or _arg("secret")
        or _arg("token")
    )


def _authorize_cron() -> tuple[bool, str | None]:
    configured = _configured_cron_secret()

    if not configured:
        return False, "cron_secret_not_configured"

    provided = _provided_cron_secret()

    if not provided:
        return False, "cron_secret_missing"

    if str(provided).strip() != str(configured).strip():
        return False, "cron_secret_invalid"

    return True, None


# ============================================================
# Channel target resolution
# ============================================================

CHANNEL_TABLE_CANDIDATES = [
    "linked_channels",
    "channel_links",
    "account_channels",
    "account_channel_links",
    "user_channels",
    "user_channel_links",
    "communication_channels",
    "messaging_channels",
    "channel_connections",
    "linked_messaging_channels",
]


def _row_text(row: dict[str, Any], keys: list[str]) -> str:
    parts = []
    for key in keys:
        value = row.get(key)
        if value is not None:
            parts.append(str(value))
    return " ".join(parts).lower()


def _row_is_inactive(row: dict[str, Any]) -> bool:
    status_text = _row_text(
        row,
        [
            "status",
            "state",
            "link_status",
            "verification_status",
            "connection_status",
        ],
    )

    if any(word in status_text for word in ["deleted", "inactive", "disabled", "revoked", "unlinked"]):
        return True

    for key in ["enabled", "is_active", "active"]:
        if key in row and row.get(key) is False:
            return True

    return False


def _row_is_verified(row: dict[str, Any]) -> bool:
    if _row_is_inactive(row):
        return False

    for key in ["verified", "is_verified", "channel_verified"]:
        if key in row:
            return bool(row.get(key))

    status_text = _row_text(
        row,
        [
            "status",
            "state",
            "link_status",
            "verification_status",
            "connection_status",
        ],
    )

    if not status_text:
        return True

    return any(word in status_text for word in ["verified", "linked", "active", "connected"])


def _row_matches_channel(row: dict[str, Any], channel: str) -> bool:
    channel = channel.lower().strip()

    descriptor = _row_text(
        row,
        [
            "platform",
            "channel",
            "channel_type",
            "provider",
            "type",
            "mode",
            "source",
            "service",
        ],
    )

    if channel in descriptor:
        return True

    if channel == "telegram":
        return any(
            row.get(key)
            for key in [
                "telegram_chat_id",
                "telegram_user_id",
                "chat_id",
                "telegram_id",
            ]
        )

    if channel == "whatsapp":
        return any(
            row.get(key)
            for key in [
                "whatsapp_number",
                "whatsapp_phone",
                "phone",
                "phone_number",
                "linked_number",
            ]
        )

    return False


def _extract_channel_target(row: dict[str, Any], channel: str) -> str | None:
    if channel == "telegram":
        keys = [
            "telegram_chat_id",
            "telegram_user_id",
            "chat_id",
            "telegram_id",
            "provider_user_id",
            "external_id",
            "external_user_id",
            "linked_account",
            "linked_account_id",
            "channel_user_id",
            "recipient",
            "destination",
            "identifier",
            "value",
        ]
    elif channel == "whatsapp":
        keys = [
            "whatsapp_number",
            "whatsapp_phone",
            "phone",
            "phone_number",
            "linked_number",
            "mobile",
            "msisdn",
            "provider_user_id",
            "external_id",
            "external_user_id",
            "channel_user_id",
            "recipient",
            "destination",
            "identifier",
            "value",
        ]
    else:
        return None

    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()

    return None


def _fetch_channel_rows(sb, account_id: str) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []

    for table_name in CHANNEL_TABLE_CANDIDATES:
        try:
            rows = _execute_data(
                sb.table(table_name)
                .select("*")
                .eq("account_id", account_id)
                .limit(20)
            )

            for row in rows:
                row_copy = dict(row)
                row_copy["_source_table"] = table_name
                all_rows.append(row_copy)

            if rows:
                # Stop at first real channel table with results.
                break

        except Exception:
            continue

    return all_rows


def _fallback_account_target(sb, account_id: str, channel: str) -> tuple[str | None, str | None]:
    try:
        rows = _execute_data(
            sb.table("accounts")
            .select("*")
            .eq("id", account_id)
            .limit(1)
        )
    except Exception:
        return None, None

    if not rows:
        return None, None

    row = rows[0]

    if channel == "telegram":
        keys = [
            "telegram_chat_id",
            "telegram_user_id",
            "telegram_id",
            "chat_id",
        ]
    else:
        keys = [
            "whatsapp_number",
            "whatsapp_phone",
            "phone",
            "phone_number",
            "mobile",
        ]

    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip(), "accounts"

    return None, None


def _resolve_channel_target(
    sb,
    *,
    account_id: str,
    channel: str,
) -> tuple[str | None, str | None]:
    rows = _fetch_channel_rows(sb, account_id)

    for row in rows:
        if not _row_matches_channel(row, channel):
            continue

        if not _row_is_verified(row):
            continue

        target = _extract_channel_target(row, channel)
        if target:
            return target, str(row.get("_source_table") or "channel_table")

    return _fallback_account_target(sb, account_id, channel)


# ============================================================
# Message delivery
# ============================================================

def _deadline_message(item: dict[str, Any]) -> str:
    tax_type = str(item.get("tax_type") or "Tax").upper()
    due_date = _normalize_date(item.get("due_date")) or str(item.get("due_date") or "")
    days_before = _safe_int(item.get("reminder_days_before"), 0)

    return (
        "🔔 Naija Tax Guide Deadline Reminder\n\n"
        f"Tax type: {tax_type}\n"
        f"Due date: {due_date}\n"
        f"Reminder: {days_before} day(s) before due date\n\n"
        "Please prepare early, keep your supporting records, and confirm the exact "
        "requirement with the relevant Nigerian tax authority where needed."
    )


def _send_telegram_message(chat_id: str, text: str) -> tuple[bool, str | None]:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")

    if not token:
        return False, "missing_telegram_bot_token"

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = requests.post(
            url,
            json={
                "chat_id": str(chat_id),
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )

        if response.ok:
            payload = response.json()
            if payload.get("ok") is True:
                return True, None
            return False, str(payload)

        return False, f"telegram_http_{response.status_code}: {response.text[:300]}"

    except Exception as exc:
        return False, f"telegram_exception: {exc}"


def _normalize_whatsapp_to(value: str) -> str:
    text = str(value or "").strip()
    text = text.replace("+", "")
    text = re.sub(r"\D+", "", text)
    return text


def _send_whatsapp_message(to_number: str, text: str) -> tuple[bool, str | None]:
    access_token = (
        os.getenv("WHATSAPP_ACCESS_TOKEN")
        or os.getenv("META_WHATSAPP_ACCESS_TOKEN")
        or os.getenv("WHATSAPP_TOKEN")
    )

    phone_number_id = (
        os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        or os.getenv("META_WHATSAPP_PHONE_NUMBER_ID")
        or os.getenv("WHATSAPP_FROM_PHONE_NUMBER_ID")
    )

    graph_version = os.getenv("META_GRAPH_VERSION", "v20.0")

    if not access_token:
        return False, "missing_whatsapp_access_token"

    if not phone_number_id:
        return False, "missing_whatsapp_phone_number_id"

    to = _normalize_whatsapp_to(to_number)

    if not to:
        return False, "invalid_whatsapp_recipient"

    url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text,
        },
    }

    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=25,
        )

        if response.ok:
            return True, None

        return False, f"whatsapp_http_{response.status_code}: {response.text[:300]}"

    except Exception as exc:
        return False, f"whatsapp_exception: {exc}"


def _send_channel_message(
    *,
    channel: str,
    target: str,
    text: str,
) -> tuple[bool, str | None]:
    channel = channel.lower().strip()

    if channel == "telegram":
        return _send_telegram_message(target, text)

    if channel == "whatsapp":
        return _send_whatsapp_message(target, text)

    return False, f"unsupported_channel:{channel}"


# ============================================================
# Deadline reminder selection
# ============================================================

def _fetch_active_deadlines(
    sb,
    *,
    account_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    query = (
        sb.table("tax_deadlines")
        .select("*")
        .eq("enabled", True)
        .order("due_date", desc=False)
        .limit(limit)
    )

    if account_id:
        query = query.eq("account_id", account_id)

    return _execute_data(query)


def _sent_key(
    *,
    deadline_id: str,
    due_date: str,
    days_before: int,
    mode: str,
    today: str,
) -> str:
    return f"deadline:{deadline_id}:{due_date}:{days_before}:{mode}:{today}"


def _append_skip(
    skipped: list[dict[str, Any]],
    *,
    reason: str,
    row: dict[str, Any],
    today: str,
    now_time: str,
    reminder_date: str | None = None,
    mode: str | None = None,
    sent_key: str | None = None,
):
    if len(skipped) >= 25:
        return

    skipped.append(
        {
            "id": row.get("id"),
            "tax_type": row.get("tax_type"),
            "due_date": _normalize_date(row.get("due_date")),
            "reminder_days_before": row.get("reminder_days_before"),
            "reminder_time": _normalize_time(row.get("reminder_time")),
            "reminder_mode": mode or row.get("reminder_mode"),
            "reminder_date": reminder_date,
            "today": today,
            "now_time": now_time,
            "sent_key": sent_key,
            "reason": reason,
        }
    )


# ============================================================
# Main cron route
# ============================================================

@cron_bp.route("/cron/send-deadline-reminders", methods=["GET", "POST"])
@cron_bp.route("/api/cron/send-deadline-reminders", methods=["GET", "POST"])
def send_deadline_reminders():
    ok, auth_error = _authorize_cron()
    if not ok:
        return _json_error(
            message=auth_error or "cron_auth_failed",
            status_code=401,
        )

    app_now = _app_now()

    today = str(_arg("today") or app_now.date().isoformat()).strip()
    now_time = _normalize_time(_arg("now_time") or app_now.strftime("%H:%M"))

    account_id = _arg("account_id")
    account_id = str(account_id).strip() if account_id else None

    limit = _safe_int(_arg("limit"), DEFAULT_LIMIT)
    limit = max(1, min(limit, MAX_LIMIT))

    force = _truthy(_arg("force"))
    ignore_time = _truthy(_arg("ignore_time"))

    send_enabled = (
        _truthy(_arg("send"))
        or _truthy(os.getenv("DEADLINE_REMINDER_SEND_ENABLED"))
        or _truthy(os.getenv("CRON_SEND_ENABLED"))
    )

    dry_run = not send_enabled

    try:
        today_date = date.fromisoformat(today)
    except Exception:
        return _json_error(
            message="invalid_today_date",
            status_code=400,
            extra={"today": today},
        )

    try:
        sb = _get_supabase_client()
    except Exception as exc:
        return _json_error(
            message="supabase_client_unavailable",
            status_code=500,
            extra={"detail": str(exc)},
        )

    try:
        rows = _fetch_active_deadlines(sb, account_id=account_id, limit=limit)
    except Exception as exc:
        return _json_error(
            message="deadline_fetch_failed",
            status_code=500,
            extra={"detail": str(exc)},
        )

    checked_count = len(rows)
    already_sent_count = 0
    skipped: list[dict[str, Any]] = []
    due_items: list[dict[str, Any]] = []
    deliveries: list[dict[str, Any]] = []

    for row in rows:
        deadline_id = str(row.get("id") or "").strip()
        row_account_id = str(row.get("account_id") or "").strip()

        if not deadline_id:
            _append_skip(
                skipped,
                reason="missing_deadline_id",
                row=row,
                today=today,
                now_time=now_time,
            )
            continue

        if not row_account_id:
            _append_skip(
                skipped,
                reason="missing_account_id",
                row=row,
                today=today,
                now_time=now_time,
            )
            continue

        due_date_obj = _parse_date(row.get("due_date"))
        if not due_date_obj:
            _append_skip(
                skipped,
                reason="invalid_due_date",
                row=row,
                today=today,
                now_time=now_time,
            )
            continue

        due_date_str = due_date_obj.isoformat()
        reminder_days_before = _safe_int(row.get("reminder_days_before"), 0)
        reminder_date_obj = due_date_obj - timedelta(days=reminder_days_before)
        reminder_date_str = reminder_date_obj.isoformat()

        reminder_time = _normalize_time(row.get("reminder_time"))

        modes = _normalize_mode(row.get("reminder_mode"))
        if not modes:
            _append_skip(
                skipped,
                reason="missing_reminder_mode",
                row=row,
                today=today,
                now_time=now_time,
                reminder_date=reminder_date_str,
            )
            continue

        if due_date_obj < today_date:
            _append_skip(
                skipped,
                reason="deadline_already_passed",
                row=row,
                today=today,
                now_time=now_time,
                reminder_date=reminder_date_str,
            )
            continue

        if reminder_date_obj != today_date:
            _append_skip(
                skipped,
                reason="not_reminder_date",
                row=row,
                today=today,
                now_time=now_time,
                reminder_date=reminder_date_str,
            )
            continue

        if not ignore_time and now_time < reminder_time:
            _append_skip(
                skipped,
                reason="not_yet_reminder_time",
                row=row,
                today=today,
                now_time=now_time,
                reminder_date=reminder_date_str,
            )
            continue

        for mode in modes:
            mode = mode.lower().strip()

            if mode not in SUPPORTED_REMINDER_MODES:
                _append_skip(
                    skipped,
                    reason="unsupported_reminder_mode",
                    row=row,
                    today=today,
                    now_time=now_time,
                    reminder_date=reminder_date_str,
                    mode=mode,
                )
                continue

            current_sent_key = _sent_key(
                deadline_id=deadline_id,
                due_date=due_date_str,
                days_before=reminder_days_before,
                mode=mode,
                today=today,
            )

            last_sent_key = str(row.get("last_reminder_sent_key") or "").strip()

            if not force and last_sent_key == current_sent_key:
                already_sent_count += 1
                _append_skip(
                    skipped,
                    reason="already_sent_today",
                    row=row,
                    today=today,
                    now_time=now_time,
                    reminder_date=reminder_date_str,
                    mode=mode,
                    sent_key=current_sent_key,
                )
                continue

            due_item = {
                "id": deadline_id,
                "account_id": row_account_id,
                "tax_type": str(row.get("tax_type") or "").upper(),
                "due_date": due_date_str,
                "reminder_days_before": reminder_days_before,
                "reminder_time": reminder_time,
                "reminder_mode": [mode],
                "sent_key": current_sent_key,
            }
            due_items.append(due_item)

            if dry_run:
                continue

            target, target_source = _resolve_channel_target(
                sb,
                account_id=row_account_id,
                channel=mode,
            )

            attempt_at = _utc_now_iso()
            _safe_update_deadline(
                sb,
                deadline_id,
                {
                    "reminder_last_attempt_at": attempt_at,
                    "reminder_last_error": None,
                    "updated_at": attempt_at,
                },
            )

            if not target:
                error_message = f"missing_{mode}_target"
                deliveries.append(
                    {
                        "id": deadline_id,
                        "channel": mode,
                        "ok": False,
                        "to": None,
                        "error": error_message,
                    }
                )
                _safe_update_deadline(
                    sb,
                    deadline_id,
                    {
                        "reminder_last_attempt_at": _utc_now_iso(),
                        "reminder_last_error": error_message,
                    },
                )
                continue

            message = _deadline_message(due_item)
            delivered, delivery_error = _send_channel_message(
                channel=mode,
                target=target,
                text=message,
            )

            deliveries.append(
                {
                    "id": deadline_id,
                    "channel": mode,
                    "ok": bool(delivered),
                    "to": target,
                    "target_source": target_source,
                    "error": delivery_error,
                }
            )

            if delivered:
                sent_at = _utc_now_iso()
                _safe_update_deadline(
                    sb,
                    deadline_id,
                    {
                        "last_reminder_sent_at": sent_at,
                        "last_reminder_sent_key": current_sent_key,
                        "reminder_last_attempt_at": sent_at,
                        "reminder_last_error": None,
                        "updated_at": sent_at,
                    },
                )
            else:
                _safe_update_deadline(
                    sb,
                    deadline_id,
                    {
                        "reminder_last_attempt_at": _utc_now_iso(),
                        "reminder_last_error": delivery_error or "delivery_failed",
                    },
                )

    delivery_count = len(deliveries)
    failed_count = sum(1 for item in deliveries if not bool(item.get("ok")))
    due_count = len(due_items)
    skipped_count = len(skipped)

    response = _build_cron_response(
        today=today,
        now_time=now_time,
        send_enabled=send_enabled,
        dry_run=dry_run,
        force=force,
        ignore_time=ignore_time,
        checked_count=checked_count,
        due_count=due_count,
        delivery_count=delivery_count,
        already_sent_count=already_sent_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        deliveries=deliveries,
        due_items=due_items,
        skipped_sample=skipped[:10],
        extra_debug={
            "account_id_filter": account_id,
            "timezone": DEFAULT_TIMEZONE,
            "limit": limit,
        },
    )

    return jsonify(response), 200


__all__ = ["cron_bp", "bp"]
