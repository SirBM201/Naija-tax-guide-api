# app/routes/billing.py
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify as _flask_jsonify, redirect, request

from app.core.supabase_client import supabase
from app.services.credits_service import (
    get_credit_balance_details,
    get_daily_usage,
    init_credits_for_plan,
)
from app.services.paystack_service import (
    create_reference,
    initialize_transaction,
    verify_transaction,
    verify_webhook_signature,
)
from app.services.plans_service import get_plan, list_plans

try:
    from app.services.auth_service import get_current_user
except Exception:  # pragma: no cover
    get_current_user = None  # type: ignore

try:
    from app.services.web_auth_service import get_account_id_from_request
except Exception:  # pragma: no cover
    get_account_id_from_request = None  # type: ignore

try:
    from app.services.referral_service import (
        ensure_referral_profile,
        qualify_referral_after_successful_payment,
    )
except Exception:  # pragma: no cover
    ensure_referral_profile = None  # type: ignore
    qualify_referral_after_successful_payment = None  # type: ignore

try:
    from app.services.channel_post_payment_service import notify_channel_payment_success
except Exception:  # pragma: no cover
    notify_channel_payment_success = None  # type: ignore


bp = Blueprint("billing", __name__)


try:
    from app.core.response_safety import sanitize_response_payload
except Exception:  # pragma: no cover
    def sanitize_response_payload(payload, request_obj=None):
        return payload


def jsonify(*args, **kwargs):
    """Local safe jsonify wrapper that strips debug/internal payload keys in production."""
    if len(args) == 1 and isinstance(args[0], (dict, list)) and not kwargs:
        return _flask_jsonify(sanitize_response_payload(args[0], request))
    return _flask_jsonify(*args, **kwargs)


logger = logging.getLogger(__name__)

BILLING_ROUTE_VERSION = "2026-05-23-v2-web-paystack-topup-channel-safe"


# -----------------------------------------------------------------------------
# Official locked add-on packages
# -----------------------------------------------------------------------------
# Business rule:
# - Basic calculators remain free.
# - Credit top-up/add-ons are only available to active paid subscribers.
# - Free/no-plan users must not be allowed to buy add-on credits.
# - These prices are server-side authoritative. Do not trust frontend prices.
# -----------------------------------------------------------------------------

TOPUP_PACKAGES: Dict[str, Dict[str, Any]] = {
    "TOPUP_100": {
        "code": "TOPUP_100",
        "name": "100 Usage Credits",
        "description": "Add 100 AI/usage credits to an active paid account.",
        "credits": 100,
        "amount_ngn": 200,
        "amount_kobo": 200 * 100,
        "currency": "NGN",
        "paid_plan_required": True,
    },
    "TOPUP_300": {
        "code": "TOPUP_300",
        "name": "300 Usage Credits",
        "description": "Add 300 AI/usage credits to an active paid account.",
        "credits": 300,
        "amount_ngn": 500,
        "amount_kobo": 500 * 100,
        "currency": "NGN",
        "paid_plan_required": True,
    },
    "TOPUP_1000": {
        "code": "TOPUP_1000",
        "name": "1,000 Usage Credits",
        "description": "Add 1,000 AI/usage credits to an active paid account.",
        "credits": 1000,
        "amount_ngn": 1500,
        "amount_kobo": 1500 * 100,
        "currency": "NGN",
        "paid_plan_required": True,
    },
}

# Compatibility aliases for any older frontend/channel payloads.
# Legacy T10/T50/T500 are intentionally not exposed as official packages.
TOPUP_CODE_ALIASES: Dict[str, str] = {
    "100": "TOPUP_100",
    "300": "TOPUP_300",
    "1000": "TOPUP_1000",
    "TOPUP100": "TOPUP_100",
    "TOPUP300": "TOPUP_300",
    "TOPUP1000": "TOPUP_1000",
    "T100": "TOPUP_100",
    "T300": "TOPUP_300",
    "T1000": "TOPUP_1000",
}


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _sb():
    return supabase() if callable(supabase) else supabase


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _upper(value: Any) -> str:
    return _clean(value).upper()


def _clip(value: Any, limit: int = 900) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _safe_json() -> Dict[str, Any]:
    data = request.get_json(silent=True) or {}
    return data if isinstance(data, dict) else {}


def _json_error(message: str, status: int, *, error: Optional[str] = None, **extra: Any):
    payload: Dict[str, Any] = {
        "ok": False,
        "error": error or message,
        "message": message,
        "billing_route_version": BILLING_ROUTE_VERSION,
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    return jsonify(payload), status


def _front_base_url() -> str:
    for key in (
        "FRONTEND_BASE_URL",
        "FRONTEND_APP_URL",
        "NEXT_PUBLIC_APP_URL",
        "APP_FRONTEND_URL",
        "APP_BASE_URL",
        "PUBLIC_FRONTEND_URL",
    ):
        value = _clean(os.getenv(key))
        if value:
            return value.rstrip("/")
    return "https://www.naijataxguides.com"


def _public_backend_base_url() -> str:
    for key in ("PUBLIC_BACKEND_BASE_URL", "BACKEND_BASE_URL", "API_BASE_URL"):
        value = _clean(os.getenv(key))
        if value:
            return value.rstrip("/")
    return "https://incredible-nonie-bmsconcept-37359733.koyeb.app"


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _query_rows(
    table: str,
    select_cols: str = "*",
    *,
    limit: int = 50,
    order_by: Optional[str] = None,
    desc: bool = True,
    **eq_filters: Any,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        q = _sb().table(table).select(select_cols)
        for col, val in eq_filters.items():
            if val is not None and _clean(val):
                q = q.eq(col, val)
        if order_by:
            try:
                q = q.order(order_by, desc=desc)
            except Exception:
                pass
        q = q.limit(limit)
        res = q.execute()
        rows = getattr(res, "data", None) or []
        return [r for r in rows if isinstance(r, dict)], None
    except Exception as exc:
        return [], f"{table}: {type(exc).__name__}: {_clip(exc)}"


def _query_one(table: str, select_cols: str, column: str, value: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    value = _clean(value)
    if not value:
        return None, None
    rows, err = _query_rows(table, select_cols, limit=1, **{column: value})
    return (rows[0] if rows else None), err


def _normalize_account(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    out = dict(row)
    if not _clean(out.get("account_id")) and _clean(out.get("id")):
        out["account_id"] = _clean(out.get("id"))
    return out


def _get_account_by_any(value: Any) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    value = _clean(value)
    errors: List[str] = []
    if not value:
        return None, errors

    for column in ("account_id", "id", "auth_user_id", "supabase_user_id", "provider_user_id", "email"):
        row, err = _query_one("accounts", "*", column, value)
        if row:
            return _normalize_account(row), errors
        if err:
            errors.append(err)
    return None, errors


def _resolve_current_account() -> Tuple[Optional[str], Optional[Dict[str, Any]], Dict[str, Any]]:
    debug: Dict[str, Any] = {
        "resolver": "billing_v2_web_cookie_bearer_safe",
        "flask_session_checked": False,
        "web_token_checked": False,
    }

    if get_current_user is not None:
        try:
            debug["flask_session_checked"] = True
            user = get_current_user()  # type: ignore[misc]
        except Exception as exc:
            user = None
            debug["flask_session_error"] = f"{type(exc).__name__}: {_clip(exc)}"

        if isinstance(user, dict) and user:
            debug["flask_session_user_found"] = True
            debug["flask_session_user_keys"] = sorted(list(user.keys()))
            candidates = [user.get("account_id"), user.get("id"), user.get("email")]
            lookup_errors: List[str] = []
            for candidate in candidates:
                account, errors = _get_account_by_any(candidate)
                lookup_errors.extend(errors)
                if account:
                    account_id = _clean(account.get("account_id")) or _clean(account.get("id")) or _clean(candidate)
                    debug["account_source"] = "flask_session"
                    debug["account_lookup_candidate"] = _clean(candidate)
                    if lookup_errors:
                        debug["non_fatal_lookup_errors"] = lookup_errors[:8]
                    return account_id, account, debug

            fallback = _clean(user.get("account_id")) or _clean(user.get("id"))
            if fallback:
                debug["account_source"] = "flask_session_fallback_id"
                return fallback, None, debug

    if get_account_id_from_request is not None:
        try:
            debug["web_token_checked"] = True
            account_id, token_debug = get_account_id_from_request(request)  # type: ignore[misc]
            debug["web_token_debug"] = token_debug
            account_id = _clean(account_id)
            if account_id:
                account, errors = _get_account_by_any(account_id)
                if errors:
                    debug["web_token_lookup_errors"] = errors[:8]
                debug["account_source"] = "web_token"
                return account_id, account, debug
        except Exception as exc:
            debug["web_token_error"] = f"{type(exc).__name__}: {_clip(exc)}"

    debug["root_cause"] = "No valid website session cookie or bearer web token was resolved."
    return None, None, debug


def _subscription_expiry(row: Dict[str, Any]) -> Optional[str]:
    return (
        row.get("expires_at")
        or row.get("current_period_end")
        or row.get("ends_at")
        or row.get("period_end")
        or row.get("grace_until")
        or row.get("trial_until")
    )


def _subscription_is_active(row: Optional[Dict[str, Any]]) -> bool:
    if not row:
        return False

    status = _lower(row.get("status"))
    if status in {"inactive", "expired", "cancelled", "canceled", "disabled", "paused", "failed"}:
        return False

    explicit = row.get("is_active")
    if explicit is not None and str(explicit).strip().lower() in {"false", "0", "no", "off"}:
        return False

    expiry = _parse_dt(_subscription_expiry(row))
    if expiry and expiry <= _now():
        return False

    return status in {"active", "trial", "grace", "past_due"} or bool(expiry)


def _plan_family_from_code(plan_code: Any) -> str:
    code = _lower(plan_code)
    if "business" in code:
        return "business"
    if "professional" in code or "pro_" in code or code.startswith("pro"):
        return "professional"
    if "starter" in code:
        return "starter"
    return "free"


def _is_paid_plan_code(plan_code: Any) -> bool:
    family = _plan_family_from_code(plan_code)
    code = _lower(plan_code)
    return family in {"starter", "professional", "business"} and code not in {"", "free", "free_forever"}


def _plan_name(plan_code: Any) -> str:
    code = _lower(plan_code)
    plan = get_plan(code)
    return str((plan or {}).get("name") or code.replace("_", " ").title() or "Free Plan")


def _credits_for(plan_code: Any) -> int:
    plan = get_plan(_lower(plan_code))
    try:
        return int((plan or {}).get("credits") or (plan or {}).get("ai_credits_total") or 0)
    except Exception:
        return 0


def _duration_days_for(plan_code: Any) -> int:
    plan = get_plan(_lower(plan_code))
    try:
        return int((plan or {}).get("duration_days") or 30)
    except Exception:
        return 30


def _get_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    rows, _err = _query_rows(
        "user_subscriptions",
        "*",
        limit=1,
        order_by="updated_at",
        desc=True,
        account_id=account_id,
    )
    if rows:
        return rows[0]

    rows, _err = _query_rows("user_subscriptions", "*", limit=1, account_id=account_id)
    return rows[0] if rows else None


def _subscription_allows_topup(sub: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    if not sub:
        return False, "no_subscription_found"
    if not _subscription_is_active(sub):
        return False, "subscription_not_active"
    plan_code = _lower(sub.get("plan_code"))
    if not _is_paid_plan_code(plan_code):
        return False, "topup_requires_paid_plan"
    return True, "eligible"


def _get_credit_balance(account_id: str, fallback: int = 0) -> int:
    details = get_credit_balance_details(account_id)
    if isinstance(details, dict) and details.get("ok"):
        return _as_int(details.get("balance"), fallback)

    for table in ("ai_credit_balances", "credit_balances"):
        rows, _err = _query_rows(table, "*", limit=1, account_id=account_id)
        if rows:
            row = rows[0]
            return _as_int(row.get("balance") or row.get("credits") or row.get("credit_balance"), fallback)

    return fallback


def _subscription_payload(
    account_id: str,
    sub: Optional[Dict[str, Any]],
    account: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    credit_details = get_credit_balance_details(account_id)
    daily_usage = get_daily_usage(account_id)

    if not sub:
        return {
            "ok": True,
            "billing_route_version": BILLING_ROUTE_VERSION,
            "account_id": account_id,
            "plan_code": "free",
            "plan_family": "free",
            "plan_name": "Free Plan",
            "status": "active",
            "active": True,
            "is_active": True,
            "started_at": None,
            "starts_at": None,
            "expires_at": None,
            "current_period_end": None,
            "pending_plan_code": None,
            "pending_starts_at": None,
            "auto_renew": False,
            "provider": None,
            "provider_ref": None,
            "payment_reference": None,
            "included_credits": 0,
            "credit_balance": _as_int((credit_details or {}).get("balance"), 0),
            "credit_balance_details": credit_details,
            "daily_usage_today": daily_usage,
            "checkout_email": (account or {}).get("email"),
            "topup_allowed": False,
            "topup_eligibility_reason": "active_paid_subscription_required",
            "subscription_summary": {
                "current_plan_code": "free",
                "pending_plan_code": None,
                "pending_starts_at": None,
                "has_pending_change": False,
                "is_active_now": True,
                "status": "active",
            },
        }

    plan_code = _lower(sub.get("plan_code")) or "free"
    active = _subscription_is_active(sub)
    expires_at = _subscription_expiry(sub)
    started_at = sub.get("started_at") or sub.get("created_at")
    pending_plan_code = sub.get("pending_plan_code")
    pending_starts_at = sub.get("pending_starts_at")
    family = _plan_family_from_code(plan_code)
    eligible, eligibility_reason = _subscription_allows_topup(sub)

    return {
        "ok": True,
        "billing_route_version": BILLING_ROUTE_VERSION,
        "account_id": account_id,
        "subscription": {**sub, "plan_family": family},
        "plan_code": plan_code,
        "plan_family": family,
        "plan_name": _plan_name(plan_code),
        "status": sub.get("status") or ("active" if active else "inactive"),
        "active": active,
        "is_active": active,
        "started_at": started_at,
        "starts_at": started_at,
        "expires_at": expires_at,
        "current_period_end": expires_at,
        "pending_plan_code": pending_plan_code,
        "pending_starts_at": pending_starts_at,
        "auto_renew": bool(sub.get("auto_renew")) if sub.get("auto_renew") is not None else False,
        "provider": sub.get("provider") or "paystack",
        "provider_ref": sub.get("provider_ref") or sub.get("paystack_ref"),
        "payment_reference": sub.get("provider_ref") or sub.get("paystack_ref"),
        "included_credits": _credits_for(plan_code),
        "credit_balance": _as_int((credit_details or {}).get("balance"), _credits_for(plan_code)),
        "credit_balance_details": credit_details,
        "daily_usage_today": daily_usage,
        "checkout_email": (account or {}).get("email"),
        "topup_allowed": bool(eligible),
        "topup_eligibility_reason": eligibility_reason,
        "subscription_summary": {
            "current_plan_code": plan_code,
            "pending_plan_code": pending_plan_code,
            "pending_starts_at": pending_starts_at,
            "has_pending_change": bool(pending_plan_code),
            "is_active_now": active,
            "status": sub.get("status") or ("active" if active else "inactive"),
        },
    }


def _safe_insert(table: str, payload: Dict[str, Any]) -> Optional[str]:
    try:
        _sb().table(table).insert(payload).execute()
        return None
    except Exception as exc:
        return f"{table}.insert: {type(exc).__name__}: {_clip(exc)}"


def _safe_update(table: str, payload: Dict[str, Any], column: str, value: Any) -> Optional[str]:
    try:
        _sb().table(table).update(payload).eq(column, value).execute()
        return None
    except Exception as exc:
        return f"{table}.update: {type(exc).__name__}: {_clip(exc)}"


def _safe_upsert(table: str, payload: Dict[str, Any], on_conflict: str) -> Optional[str]:
    try:
        _sb().table(table).upsert(payload, on_conflict=on_conflict).execute()
        return None
    except Exception as exc:
        return f"{table}.upsert: {type(exc).__name__}: {_clip(exc)}"


def _merge_metadata(*items: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for item in items:
        if isinstance(item, dict):
            out.update(item)
    return out


def _normalize_metadata(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _find_transaction(reference: str) -> Optional[Dict[str, Any]]:
    reference = _clean(reference)
    if not reference:
        return None

    for table in ("paystack_transactions", "paystack_events"):
        rows, _err = _query_rows(table, "*", limit=1, reference=reference)
        if rows:
            return rows[0]
    return None


def _remember_transaction(
    reference: str,
    account_id: str,
    plan_code: str,
    amount_kobo: int,
    status: str,
    metadata: Dict[str, Any],
    *,
    event_type: Optional[str] = None,
    paid_at: Optional[str] = None,
) -> Dict[str, Any]:
    reference = _clean(reference)
    now_iso = _now_iso()
    status = _lower(status) or "pending"
    metadata = _normalize_metadata(metadata)

    if not reference:
        return {"ok": False, "error": "reference_required"}

    rich_payload = {
        "reference": reference,
        "account_id": _clean(account_id) or None,
        "plan_code": _clean(plan_code) or None,
        "amount": int(amount_kobo or 0),
        "amount_kobo": int(amount_kobo or 0),
        "currency": metadata.get("currency") or "NGN",
        "status": status,
        "event_type": event_type or metadata.get("event_type") or None,
        "metadata": metadata,
        "paid_at": paid_at,
        "updated_at": now_iso,
        "created_at": metadata.get("created_at") or now_iso,
    }

    minimal_payload = {
        "reference": reference,
        "account_id": _clean(account_id) or None,
        "plan_code": _clean(plan_code) or None,
        "amount": int(amount_kobo or 0),
        "status": status,
        "metadata": metadata,
        "updated_at": now_iso,
        "created_at": metadata.get("created_at") or now_iso,
    }

    errors: List[str] = []

    for payload in (rich_payload, minimal_payload):
        err = _safe_upsert("paystack_transactions", payload, "reference")
        if not err:
            return {"ok": True, "table": "paystack_transactions", "mode": "upsert", "schema": "rich" if payload is rich_payload else "minimal"}
        errors.append(err)

    for payload in (rich_payload, minimal_payload):
        update_payload = dict(payload)
        update_payload.pop("created_at", None)
        err = _safe_update("paystack_transactions", update_payload, "reference", reference)
        if not err:
            return {"ok": True, "table": "paystack_transactions", "mode": "update", "schema": "rich" if payload is rich_payload else "minimal"}
        errors.append(err)

    for payload in (minimal_payload,):
        err = _safe_insert("paystack_transactions", payload)
        if not err:
            return {"ok": True, "table": "paystack_transactions", "mode": "insert", "schema": "minimal"}
        errors.append(err)

    return {"ok": False, "errors": errors[:8]}


def _store_paystack_event(
    *,
    event_id: Optional[str],
    event_type: str,
    reference: Optional[str],
    payload: Dict[str, Any],
) -> None:
    row = {
        "event_id": event_id,
        "event_type": event_type or "unknown",
        "reference": reference,
        "payload": payload,
        "created_at": _now_iso(),
    }
    try:
        _sb().table("paystack_events").insert(row).execute()
    except Exception:
        pass


def _topup_code_from_payload(data: Dict[str, Any]) -> str:
    raw = _upper(
        data.get("package_code")
        or data.get("topup_code")
        or data.get("code")
        or data.get("package")
        or data.get("plan_code")
    )
    return TOPUP_CODE_ALIASES.get(raw, raw)


def _get_topup_package(code: Any) -> Optional[Dict[str, Any]]:
    normalized = TOPUP_CODE_ALIASES.get(_upper(code), _upper(code))
    return TOPUP_PACKAGES.get(normalized)


def _is_credit_topup_metadata(metadata: Dict[str, Any], transaction_row: Optional[Dict[str, Any]] = None) -> bool:
    tx_meta = _normalize_metadata((transaction_row or {}).get("metadata"))
    payment_type = _lower(
        metadata.get("type")
        or metadata.get("purpose")
        or tx_meta.get("type")
        or tx_meta.get("purpose")
        or (transaction_row or {}).get("plan_code")
    )
    topup_code = _topup_code_from_payload(_merge_metadata(tx_meta, metadata))
    return payment_type in {"credit_topup", "usage_topup", "credit_purchase", "ai_topup"} or bool(_get_topup_package(topup_code))


def _is_already_successful(reference: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    tx = _find_transaction(reference)
    if not tx:
        return False, None
    status = _lower(tx.get("status"))
    meta = _normalize_metadata(tx.get("metadata"))
    already = status in {"success", "paid", "applied", "completed"} and (
        bool(meta.get("applied"))
        or bool(meta.get("applied_credit_topup"))
        or bool(meta.get("applied_subscription"))
        or _lower(meta.get("application_state")) in {"applied", "already_applied"}
    )
    return already, tx


def _set_credit_balance_fallback(account_id: str, balance: int) -> Dict[str, Any]:
    account_id = _clean(account_id)
    balance = max(0, _as_int(balance, 0))
    now_iso = _now_iso()

    payloads = [
        {"account_id": account_id, "balance": balance, "updated_at": now_iso},
        {"account_id": account_id, "credits": balance, "updated_at": now_iso},
    ]

    errors: List[str] = []
    for table in ("ai_credit_balances", "credit_balances"):
        for payload in payloads:
            err = _safe_upsert(table, payload, "account_id")
            if not err:
                return {"ok": True, "table": table, "balance": balance, "mode": "upsert"}
            errors.append(err)

        for payload in payloads:
            err = _safe_update(table, payload, "account_id", account_id)
            if not err:
                return {"ok": True, "table": table, "balance": balance, "mode": "update"}
            errors.append(err)

        err = _safe_insert(table, payloads[0])
        if not err:
            return {"ok": True, "table": table, "balance": balance, "mode": "insert"}
        errors.append(err)

    return {"ok": False, "error": "credit_balance_fallback_failed", "errors": errors[:8]}


def _add_credits_to_balance(account_id: str, credits_to_add: int, reference: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    account_id = _clean(account_id)
    credits_to_add = _as_int(credits_to_add, 0)
    reference = _clean(reference)

    if not account_id:
        return {"ok": False, "error": "account_id_required"}
    if not reference:
        return {"ok": False, "error": "reference_required"}
    if credits_to_add <= 0:
        return {"ok": False, "error": "invalid_credit_amount"}

    already, tx = _is_already_successful(reference)
    tx_meta = _normalize_metadata((tx or {}).get("metadata"))
    if already and bool(tx_meta.get("applied_credit_topup")):
        return {
            "ok": True,
            "already_applied": True,
            "account_id": account_id,
            "credits_added": 0,
            "reference": reference,
            "message": "Credit top-up was already applied for this reference.",
        }

    now_iso = _now_iso()
    current_balance = _get_credit_balance(account_id, 0)
    new_balance = current_balance + credits_to_add

    balance_result = _set_credit_balance_fallback(account_id, new_balance)
    if not balance_result.get("ok"):
        return {
            "ok": False,
            "error": "credit_balance_update_failed",
            "old_balance": current_balance,
            "attempted_new_balance": new_balance,
            "details": balance_result,
        }

    tx_payload = {
        "account_id": account_id,
        "reference": reference,
        "action_code": "credit_topup",
        "description": f"Credit top-up: +{credits_to_add} Usage Credits",
        "channel": metadata.get("channel_type") or "web",
        "credits_delta": credits_to_add,
        "balance_after": new_balance,
        "metadata": metadata,
        "created_at": now_iso,
    }

    log_errors: List[str] = []
    for table in ("credit_usage_logs", "credit_transactions", "ai_credit_transactions"):
        err = _safe_insert(table, tx_payload)
        if not err:
            break
        log_errors.append(err)

    final_meta = _merge_metadata(
        metadata,
        tx_meta,
        {
            "applied": True,
            "applied_credit_topup": True,
            "application_state": "applied",
            "credits_added": credits_to_add,
            "balance_before": current_balance,
            "balance_after": new_balance,
            "applied_at": now_iso,
        },
    )
    tx_note = _remember_transaction(
        reference,
        account_id,
        metadata.get("topup_code") or metadata.get("package_code") or "TOPUP",
        _as_int(metadata.get("amount_kobo") or metadata.get("amount"), 0),
        "success",
        final_meta,
        event_type="credit_topup",
        paid_at=metadata.get("paid_at") or now_iso,
    )

    return {
        "ok": True,
        "account_id": account_id,
        "credits_added": credits_to_add,
        "old_balance": current_balance,
        "new_balance": new_balance,
        "reference": reference,
        "balance_result": balance_result,
        "transaction_note": tx_note,
        "non_fatal_log_errors": log_errors[:3] if log_errors else None,
    }


def _notify_channel_if_needed(account_id: str, plan_code: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    if notify_channel_payment_success is None:
        return {"ok": True, "notified": False, "reason": "notify_service_unavailable"}

    channel_type = _lower(metadata.get("channel_type"))
    if channel_type not in {"whatsapp", "telegram"}:
        return {"ok": True, "notified": False, "reason": "not_channel_payment"}

    provider_user_id = _clean(metadata.get("provider_user_id"))
    try:
        result = notify_channel_payment_success(  # type: ignore[misc]
            account_id=account_id,
            channel_type=channel_type,
            plan_code=plan_code,
            provider_user_id=provider_user_id or None,
        )
        return {"ok": bool((result or {}).get("ok")), "notified": bool((result or {}).get("ok")), "result": result}
    except Exception as exc:
        logger.exception("Channel payment success notification failed")
        return {"ok": False, "notified": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


def _qualify_referral_if_needed(account_id: str, reference: str, plan_code: str) -> Dict[str, Any]:
    if qualify_referral_after_successful_payment is None:
        return {"ok": True, "qualified": False, "reason": "referral_service_unavailable"}

    try:
        if ensure_referral_profile is not None:
            try:
                ensure_referral_profile(account_id)  # type: ignore[misc]
            except Exception:
                pass

        result = qualify_referral_after_successful_payment(  # type: ignore[misc]
            paying_account_id=account_id,
            payment_reference=reference,
            plan_code=plan_code,
        )
        return result if isinstance(result, dict) else {"ok": True, "qualified": False, "raw": result}
    except Exception as exc:
        logger.exception("Referral qualification after payment failed")
        return {"ok": False, "qualified": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


def _activate_subscription(
    account_id: str,
    plan_code: str,
    reference: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    account_id = _clean(account_id)
    plan_code = _lower(plan_code)
    reference = _clean(reference)
    metadata = _normalize_metadata(metadata)

    if not account_id:
        return {"ok": False, "error": "account_id_required"}
    if not plan_code:
        return {"ok": False, "error": "plan_code_required"}
    if not reference:
        return {"ok": False, "error": "reference_required"}

    plan = get_plan(plan_code)
    if not plan:
        return {"ok": False, "error": "plan_not_found", "plan_code": plan_code}

    duration_days = _duration_days_for(plan_code)
    now_iso = _now_iso()
    expires_at = (_now() + timedelta(days=duration_days)).isoformat()
    plan_family = _plan_family_from_code(plan_code)

    rich_payload = {
        "account_id": account_id,
        "plan_code": plan_code,
        "plan_family": plan_family,
        "status": "active",
        "is_active": True,
        "started_at": now_iso,
        "starts_at": now_iso,
        "expires_at": expires_at,
        "current_period_end": expires_at,
        "provider": "paystack",
        "provider_ref": reference,
        "paystack_ref": reference,
        "updated_at": now_iso,
        "created_at": now_iso,
    }
    medium_payload = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": "active",
        "is_active": True,
        "current_period_end": expires_at,
        "updated_at": now_iso,
        "created_at": now_iso,
    }
    minimal_payload = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": "active",
        "current_period_end": expires_at,
        "updated_at": now_iso,
        "created_at": now_iso,
    }

    errors: List[str] = []
    schema_mode = ""

    for name, payload in (("rich", rich_payload), ("medium", medium_payload), ("minimal", minimal_payload)):
        err = _safe_upsert("user_subscriptions", payload, "account_id")
        if not err:
            schema_mode = f"{name}_upsert"
            break
        errors.append(err)

    if not schema_mode:
        for name, payload in (("rich", rich_payload), ("medium", medium_payload), ("minimal", minimal_payload)):
            update_payload = dict(payload)
            update_payload.pop("created_at", None)
            err = _safe_update("user_subscriptions", update_payload, "account_id", account_id)
            if not err:
                schema_mode = f"{name}_update"
                break
            errors.append(err)

    if not schema_mode:
        for name, payload in (("minimal", minimal_payload), ("medium", medium_payload), ("rich", rich_payload)):
            err = _safe_insert("user_subscriptions", payload)
            if not err:
                schema_mode = f"{name}_insert"
                break
            errors.append(err)

    if not schema_mode:
        return {
            "ok": False,
            "error": "subscription_activation_failed",
            "root_cause": errors[-1] if errors else "unknown_activation_error",
            "errors": errors[:8],
            "fix": "Confirm user_subscriptions supports account_id, plan_code, status, current_period_end, created_at, and updated_at.",
        }

    try:
        credit_result = init_credits_for_plan(account_id, plan_code)
    except Exception as exc:
        credit_result = {"ok": False, "error": "init_credits_for_plan_exception", "root_cause": f"{type(exc).__name__}: {_clip(exc)}"}

    if not isinstance(credit_result, dict) or not credit_result.get("ok"):
        # Fallback protects production when the DB plans table is not seeded but the static plans service is correct.
        credit_result = {
            "primary_init_result": credit_result,
            "fallback": _set_credit_balance_fallback(account_id, _credits_for(plan_code)),
        }

    final_meta = _merge_metadata(
        metadata,
        {
            "applied": True,
            "applied_subscription": True,
            "application_state": "applied",
            "account_id": account_id,
            "plan_code": plan_code,
            "plan_family": plan_family,
            "expires_at": expires_at,
            "applied_at": now_iso,
        },
    )
    tx_note = _remember_transaction(
        reference,
        account_id,
        plan_code,
        _as_int(metadata.get("amount_kobo") or metadata.get("amount"), 0),
        "success",
        final_meta,
        event_type="subscription",
        paid_at=metadata.get("paid_at") or now_iso,
    )

    referral_result = _qualify_referral_if_needed(account_id, reference, plan_code)
    notification_result = _notify_channel_if_needed(account_id, plan_code, metadata)

    return {
        "ok": True,
        "account_id": account_id,
        "plan_code": plan_code,
        "plan_family": plan_family,
        "expires_at": expires_at,
        "current_period_end": expires_at,
        "duration_days": duration_days,
        "schema_mode": schema_mode,
        "credits": credit_result,
        "transaction_note": tx_note,
        "referral": referral_result,
        "channel_notification": notification_result,
        "non_fatal_errors": errors[:4] if errors else None,
    }


def _extract_payment_context(reference: str, paystack_data: Dict[str, Any]) -> Dict[str, Any]:
    transaction_row = _find_transaction(reference) or {}
    tx_meta = _normalize_metadata(transaction_row.get("metadata"))
    ps_meta = _normalize_metadata(paystack_data.get("metadata"))
    metadata = _merge_metadata(tx_meta, ps_meta)

    amount = paystack_data.get("amount") or transaction_row.get("amount_kobo") or transaction_row.get("amount") or metadata.get("amount_kobo") or 0
    amount_kobo = _as_int(amount, 0)
    if amount_kobo and amount_kobo < 1000 and metadata.get("amount_ngn"):
        amount_kobo = _as_int(metadata.get("amount_ngn"), 0) * 100

    account_id = _clean(metadata.get("account_id") or transaction_row.get("account_id"))
    plan_code = _lower(metadata.get("plan_code") or transaction_row.get("plan_code"))

    metadata.update(
        {
            "reference": reference,
            "amount": amount_kobo,
            "amount_kobo": amount_kobo,
            "paid_at": paystack_data.get("paid_at") or paystack_data.get("created_at") or _now_iso(),
            "currency": paystack_data.get("currency") or metadata.get("currency") or "NGN",
            "gateway_response": paystack_data.get("gateway_response"),
        }
    )

    return {
        "transaction_row": transaction_row,
        "metadata": metadata,
        "account_id": account_id,
        "plan_code": plan_code,
        "amount_kobo": amount_kobo,
        "status": _lower(paystack_data.get("status")),
    }


def _apply_successful_payment(reference: str, paystack_data: Dict[str, Any]) -> Dict[str, Any]:
    context = _extract_payment_context(reference, paystack_data)
    metadata = context["metadata"]
    account_id = context["account_id"]
    plan_code = context["plan_code"]
    amount_kobo = context["amount_kobo"]
    transaction_row = context["transaction_row"]

    if not account_id:
        return {
            "ok": False,
            "applied": False,
            "error": "missing_account_id",
            "reference": reference,
            "fix": "Paystack metadata or paystack_transactions row must include account_id.",
        }

    if _is_credit_topup_metadata(metadata, transaction_row):
        topup_code = _topup_code_from_payload(metadata)
        package = _get_topup_package(topup_code)
        credits = _as_int(metadata.get("credits"), 0)
        if package:
            credits = _as_int(package.get("credits"), credits)
            metadata.update(
                {
                    "type": "credit_topup",
                    "purpose": "usage_topup",
                    "topup_code": package["code"],
                    "package_code": package["code"],
                    "package_name": package["name"],
                    "credits": package["credits"],
                    "amount_ngn": package["amount_ngn"],
                    "amount_kobo": package["amount_kobo"],
                }
            )

        if credits <= 0:
            return {"ok": False, "applied": False, "error": "missing_topup_credits", "reference": reference}

        credit_application = _add_credits_to_balance(account_id, credits, reference, metadata)
        return {
            "ok": bool(credit_application.get("ok")),
            "applied": bool(credit_application.get("ok")),
            "payment_type": "credit_topup",
            "reference": reference,
            "account_id": account_id,
            "plan_code": metadata.get("topup_code") or metadata.get("package_code"),
            "credit_application": credit_application,
        }

    if not plan_code:
        return {
            "ok": False,
            "applied": False,
            "error": "missing_plan_code",
            "reference": reference,
            "fix": "Paystack metadata or paystack_transactions row must include plan_code for subscription payments.",
        }

    activation = _activate_subscription(account_id, plan_code, reference, metadata={**metadata, "amount_kobo": amount_kobo})
    return {
        "ok": bool(activation.get("ok")),
        "applied": bool(activation.get("ok")),
        "payment_type": "subscription",
        "reference": reference,
        "account_id": account_id,
        "plan_code": plan_code,
        "subscription": activation,
    }


# -----------------------------------------------------------------------------
# Routes. app/__init__.py registers this blueprint with /api.
# We expose both legacy /api/... and current frontend /api/billing/... aliases.
# -----------------------------------------------------------------------------


@bp.get("/billing/health")
def billing_health():
    return jsonify({"ok": True, "service": "billing", "version": BILLING_ROUTE_VERSION}), 200


@bp.get("/plans")
@bp.get("/billing/plans")
def billing_plans():
    active_only = (request.args.get("active_only") or "1").strip() != "0"
    plans = list_plans(active_only=active_only)
    return jsonify({"ok": True, "plans": plans, "billing_route_version": BILLING_ROUTE_VERSION}), 200


@bp.get("/plans/<plan_code>")
@bp.get("/billing/plans/<plan_code>")
def billing_plan(plan_code: str):
    plan = get_plan(plan_code)
    if not plan:
        return _json_error("Plan was not found.", 404, error="plan_not_found", plan_code=plan_code)
    return jsonify({"ok": True, "plan": plan, "billing_route_version": BILLING_ROUTE_VERSION}), 200


@bp.get("/me")
@bp.get("/subscription")
@bp.get("/billing/me")
@bp.get("/billing/subscription")
def billing_me():
    account_id, account, debug = _resolve_current_account()
    if not account_id:
        return _json_error(
            "Please sign in again before opening billing.",
            401,
            error="unauthorized",
            fix="Login again from the website so the ntg_session cookie can be refreshed.",
            debug=debug,
        )

    sub = _get_subscription(account_id)
    payload = _subscription_payload(account_id, sub, account)
    payload["debug"] = debug
    return jsonify(payload), 200


@bp.get("/debug-state")
@bp.get("/billing/debug-state")
def billing_debug_state():
    account_id, account, debug = _resolve_current_account()
    if not account_id:
        return _json_error("Please sign in again before reading billing debug state.", 401, error="unauthorized", debug=debug)

    sub = _get_subscription(account_id)
    payload = _subscription_payload(account_id, sub, account)
    return jsonify(
        {
            "ok": True,
            "billing_route_version": BILLING_ROUTE_VERSION,
            "account_id": account_id,
            "billing": payload,
            "subscription_guard_snapshot": {
                "access": {
                    "allowed": bool(payload.get("active")),
                    "reason": payload.get("status"),
                },
                "plan_code": payload.get("plan_code"),
            },
            "credit_balance": {"balance": payload.get("credit_balance") or 0},
            "daily_usage_today": payload.get("daily_usage_today") or {"count": 0},
            "debug": debug,
        }
    ), 200


@bp.get("/topup/packages")
@bp.get("/billing/topup/packages")
@bp.get("/paystack/topup/packages")
def topup_packages():
    account_id, _account, debug = _resolve_current_account()
    eligible = False
    eligibility_reason = "login_required"

    if account_id:
        eligible, eligibility_reason = _subscription_allows_topup(_get_subscription(account_id))

    return jsonify(
        {
            "ok": True,
            "billing_route_version": BILLING_ROUTE_VERSION,
            "packages": list(TOPUP_PACKAGES.values()),
            "eligibility_rule": "active_paid_subscription_required",
            "eligible": eligible,
            "eligibility_reason": eligibility_reason,
            "account_id": account_id,
            "debug": debug if request.args.get("debug") in {"1", "true", "yes"} else None,
        }
    ), 200


@bp.post("/paystack/topup/initialize")
@bp.post("/billing/topup/initialize")
@bp.post("/topup/initialize")
def topup_initialize():
    account_id, account, debug = _resolve_current_account()
    if not account_id:
        return _json_error(
            "Please sign in again before buying credit add-ons.",
            401,
            error="unauthorized",
            fix="Login again from the website so the ntg_session cookie can be refreshed.",
            debug=debug,
        )

    sub = _get_subscription(account_id)
    eligible, reason = _subscription_allows_topup(sub)
    if not eligible:
        return _json_error(
            "Credit top-up is only available to active paid subscribers.",
            403,
            error="topup_requires_active_subscription",
            eligibility_reason=reason,
            plan_code=(sub or {}).get("plan_code"),
            plan_family=_plan_family_from_code((sub or {}).get("plan_code")),
            debug=debug,
        )

    data = _safe_json()
    package_code = _topup_code_from_payload(data)
    package = _get_topup_package(package_code)
    if not package:
        return _json_error(
            "Selected top-up package was not found.",
            400,
            error="invalid_topup_package",
            received_code=package_code or None,
            allowed_codes=list(TOPUP_PACKAGES.keys()),
        )

    email = _clean(data.get("email") or (account or {}).get("email")) or f"user_{account_id[:8]}@naijataxguides.com"
    reference = create_reference("NTG-TOPUP")
    amount_kobo = int(package["amount_kobo"])

    metadata = {
        "account_id": account_id,
        "type": "credit_topup",
        "purpose": "usage_topup",
        "source": "web_credits_page",
        "topup_code": package["code"],
        "package_code": package["code"],
        "package_name": package["name"],
        "credits": package["credits"],
        "amount_ngn": package["amount_ngn"],
        "amount_kobo": package["amount_kobo"],
        "currency": "NGN",
        "plan_code": (sub or {}).get("plan_code"),
        "channel_type": _lower(data.get("channel_type")) or "web",
    }

    callback_url = f"{_public_backend_base_url()}/api/paystack/topup/callback?reference={reference}"

    try:
        result = initialize_transaction(
            email=email,
            amount_kobo=amount_kobo,
            reference=reference,
            callback_url=callback_url,
            metadata=metadata,
        )
    except Exception as exc:
        logger.exception("Paystack top-up initialization failed")
        return _json_error(
            "Paystack top-up checkout could not be started.",
            502,
            error="paystack_topup_initialize_failed",
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
            fix="Confirm PAYSTACK_SECRET_KEY is set on Koyeb and Paystack API is reachable.",
            debug=debug,
        )

    tx_note = _remember_transaction(reference, account_id, package["code"], amount_kobo, "pending", metadata, event_type="credit_topup")
    auth_url = ((result or {}).get("data") or {}).get("authorization_url") or (result or {}).get("authorization_url")
    access_code = ((result or {}).get("data") or {}).get("access_code") or (result or {}).get("access_code")

    if not auth_url:
        return _json_error(
            "Paystack did not return an authorization URL for this top-up.",
            502,
            error="paystack_authorization_url_missing",
            paystack_response=result,
            transaction_note=tx_note,
        )

    return jsonify(
        {
            "ok": True,
            "billing_route_version": BILLING_ROUTE_VERSION,
            "action": "topup_checkout_started",
            "authorization_url": auth_url,
            "access_code": access_code,
            "reference": reference,
            "package": package,
            "transaction_note": tx_note,
        }
    ), 200


@bp.post("/change-plan")
@bp.post("/checkout")
@bp.post("/initialize")
@bp.post("/billing/change-plan")
@bp.post("/billing/checkout")
@bp.post("/billing/initialize")
def billing_checkout():
    account_id, account, debug = _resolve_current_account()
    if not account_id:
        return _json_error(
            "Please sign in again before choosing a plan.",
            401,
            error="unauthorized",
            fix="Login again from the website so the ntg_session cookie can be refreshed.",
            debug=debug,
        )

    data = _safe_json()
    plan_code = _lower(data.get("plan_code") or data.get("plan"))
    if not plan_code:
        return _json_error("plan_code is required.", 400, error="plan_code_required")

    plan = get_plan(plan_code)
    if not plan:
        return _json_error("Selected plan was not found.", 404, error="plan_not_found", plan_code=plan_code)

    current = _get_subscription(account_id)
    if current and _subscription_is_active(current):
        current_plan_code = _lower(current.get("plan_code"))
        if current_plan_code == plan_code:
            return _json_error(
                f"You already have an active {_plan_name(plan_code)} subscription.",
                409,
                error="already_on_selected_plan",
                current_plan_code=current_plan_code,
            )

    amount_kobo = int(plan.get("price") or 0) * 100
    if amount_kobo <= 0:
        return _json_error("This plan cannot be checked out because the price is invalid.", 400, error="invalid_plan_price", plan=plan)

    email = _clean(data.get("email") or (account or {}).get("email")) or f"user_{account_id[:8]}@naijataxguides.com"
    reference = create_reference("NTG")
    metadata = {
        "account_id": account_id,
        "plan_code": plan_code,
        "plan_name": plan.get("name"),
        "plan_family": _plan_family_from_code(plan_code),
        "type": "subscription",
        "source": data.get("source") or "web_plans_page",
        "channel_type": _lower(data.get("channel_type")) or "web",
        "provider_user_id": _clean(data.get("provider_user_id")) or None,
        "amount_ngn": int(plan.get("price") or 0),
        "amount_kobo": amount_kobo,
        "currency": plan.get("currency") or "NGN",
    }

    callback_url = f"{_public_backend_base_url()}/api/billing/callback?reference={reference}&plan={plan_code}"

    try:
        result = initialize_transaction(
            email=email,
            amount_kobo=amount_kobo,
            reference=reference,
            callback_url=callback_url,
            metadata=metadata,
        )
    except Exception as exc:
        logger.exception("Paystack checkout initialization failed")
        return _json_error(
            "Paystack checkout could not be started.",
            502,
            error="paystack_initialize_failed",
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
            fix="Confirm PAYSTACK_SECRET_KEY is set on Koyeb and the selected plan price is valid.",
            debug=debug,
        )

    tx_note = _remember_transaction(reference, account_id, plan_code, amount_kobo, "pending", metadata, event_type="subscription")
    auth_url = ((result or {}).get("data") or {}).get("authorization_url") or (result or {}).get("authorization_url")
    access_code = ((result or {}).get("data") or {}).get("access_code") or (result or {}).get("access_code")

    if not auth_url:
        return _json_error(
            "Paystack did not return an authorization URL.",
            502,
            error="paystack_authorization_url_missing",
            paystack_response=result,
            transaction_note=tx_note,
        )

    return jsonify(
        {
            "ok": True,
            "billing_route_version": BILLING_ROUTE_VERSION,
            "action": "checkout_started",
            "authorization_url": auth_url,
            "access_code": access_code,
            "reference": reference,
            "plan": plan,
            "transaction_note": tx_note,
        }
    ), 200


@bp.post("/clear-pending-change")
@bp.post("/billing/clear-pending-change")
def billing_clear_pending_change():
    account_id, _account, debug = _resolve_current_account()
    if not account_id:
        return _json_error("Please sign in again before clearing a pending plan change.", 401, error="unauthorized", debug=debug)

    try:
        _sb().table("user_subscriptions").update(
            {"pending_plan_code": None, "pending_starts_at": None, "updated_at": _now_iso()}
        ).eq("account_id", account_id).execute()
        return jsonify({"ok": True, "action": "pending_change_cleared", "billing_route_version": BILLING_ROUTE_VERSION}), 200
    except Exception as exc:
        return jsonify(
            {
                "ok": True,
                "billing_route_version": BILLING_ROUTE_VERSION,
                "action": "no_pending_change",
                "message": "No compatible pending-change columns were found, so there is nothing to clear.",
                "non_fatal_root_cause": f"{type(exc).__name__}: {_clip(exc)}",
            }
        ), 200


@bp.get("/verify")
@bp.get("/billing/verify")
@bp.get("/paystack/verify")
def billing_verify():
    reference = _clean(request.args.get("reference") or request.args.get("trxref"))
    debug_requested = _lower(request.args.get("debug")) in {"1", "true", "yes"}
    if not reference:
        return _json_error("Payment reference is required.", 400, error="reference_required")

    try:
        verification = verify_transaction(reference)
    except Exception as exc:
        logger.exception("Paystack verification failed")
        return _json_error(
            "Paystack payment verification failed.",
            502,
            error="paystack_verify_failed",
            reference=reference,
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
        )

    data = (verification or {}).get("data") or {}
    status = _lower(data.get("status"))
    paid = status == "success"

    applied = False
    application: Dict[str, Any] = {}

    if paid:
        application = _apply_successful_payment(reference, data)
        applied = bool(application.get("applied"))

    payload: Dict[str, Any] = {
        "ok": True,
        "billing_route_version": BILLING_ROUTE_VERSION,
        "reference": reference,
        "status": status or "unknown",
        "paid": paid,
        "applied": applied,
        "application": application if application else None,
        "account_id": application.get("account_id") if application else None,
        "plan_code": application.get("plan_code") if application else None,
        "payment_type": application.get("payment_type") if application else None,
        "activation_state": "applied" if applied else ("not_paid" if not paid else "not_applied"),
        "message": "Payment verified and applied." if applied else "Payment verified, but it was not applied.",
    }
    if debug_requested:
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        payload["debug"] = {
            "paystack_status": status,
            "metadata_keys": sorted(list(metadata.keys())),
            "application": application,
            "raw_verification_data_keys": sorted(list(data.keys())) if isinstance(data, dict) else [],
        }
    return jsonify(payload), 200


@bp.get("/callback")
@bp.get("/billing/callback")
@bp.get("/paystack/callback")
def billing_callback():
    reference = _clean(request.args.get("reference") or request.args.get("trxref"))
    plan_hint = _clean(request.args.get("plan"))

    if not reference:
        return redirect(f"{_front_base_url()}/billing?payment=missing_reference", code=302)

    try:
        verification = verify_transaction(reference)
        data = (verification or {}).get("data") or {}
        if _lower(data.get("status")) == "success":
            application = _apply_successful_payment(reference, data)
            if application.get("applied"):
                plan_code = _clean(application.get("plan_code") or plan_hint)
                return redirect(f"{_front_base_url()}/billing/success?reference={reference}&plan={plan_code}", code=302)
            return redirect(f"{_front_base_url()}/billing?payment=not_applied&reference={reference}", code=302)
    except Exception as exc:
        logger.exception("Billing callback verification failed")
        return redirect(f"{_front_base_url()}/billing?payment=verify_failed&error={type(exc).__name__}", code=302)

    return redirect(f"{_front_base_url()}/billing?payment=pending&reference={reference}", code=302)


@bp.get("/paystack/topup/callback")
@bp.get("/topup/callback")
def topup_callback():
    reference = _clean(request.args.get("reference") or request.args.get("trxref"))
    if not reference:
        return redirect(f"{_front_base_url()}/credits?topup=missing_reference", code=302)

    try:
        verification = verify_transaction(reference)
        data = (verification or {}).get("data") or {}
        if _lower(data.get("status")) == "success":
            application = _apply_successful_payment(reference, data)
            if application.get("applied"):
                return redirect(f"{_front_base_url()}/credits?topup=success&reference={reference}", code=302)
            return redirect(f"{_front_base_url()}/credits?topup=not_applied&reference={reference}", code=302)
    except Exception as exc:
        logger.exception("Top-up callback verification failed")
        return redirect(f"{_front_base_url()}/credits?topup=verify_failed&error={type(exc).__name__}", code=302)

    return redirect(f"{_front_base_url()}/credits?topup=pending&reference={reference}", code=302)


@bp.get("/history")
@bp.get("/billing/history")
def billing_history():
    account_id, _account, debug = _resolve_current_account()
    if not account_id:
        return _json_error("Please sign in again before opening billing history.", 401, error="unauthorized", debug=debug)

    try:
        limit = max(1, min(int(request.args.get("limit") or 24), 100))
    except Exception:
        limit = 24

    rows_out: List[Dict[str, Any]] = []
    warnings: List[str] = []

    tx_rows, tx_err = _query_rows("paystack_transactions", "*", limit=limit, order_by="created_at", desc=True, account_id=account_id)
    if tx_err:
        warnings.append(tx_err)

    for row in tx_rows:
        amount = row.get("amount_kobo") or row.get("amount") or 0
        try:
            amount_f = float(amount or 0)
            amount_ngn = amount_f / 100 if amount_f > 1000 else amount_f
        except Exception:
            amount_ngn = 0

        metadata = _normalize_metadata(row.get("metadata"))
        rows_out.append(
            {
                "reference": row.get("reference"),
                "event_type": row.get("event_type") or metadata.get("type") or "checkout",
                "status": row.get("status") or "pending",
                "amount_ngn": amount_ngn,
                "currency": row.get("currency") or metadata.get("currency") or "NGN",
                "paid_at": row.get("paid_at"),
                "created_at": row.get("created_at"),
                "plan_code": row.get("plan_code"),
                "plan_name": _plan_name(_lower(row.get("plan_code"))) if row.get("plan_code") else metadata.get("package_name"),
                "payment_method": row.get("payment_method") or "paystack",
                "channel_type": row.get("channel_type") or metadata.get("channel_type") or "web",
                "gateway_response": row.get("gateway_response") or metadata.get("gateway_response"),
                "source": "paystack_transactions",
            }
        )

    sub = _get_subscription(account_id)
    if sub:
        rows_out.append(
            {
                "reference": sub.get("provider_ref") or sub.get("paystack_ref"),
                "event_type": "subscription_snapshot",
                "status": sub.get("status") or "active",
                "amount_ngn": None,
                "currency": "NGN",
                "paid_at": sub.get("started_at") or sub.get("created_at"),
                "created_at": sub.get("created_at"),
                "plan_code": sub.get("plan_code"),
                "plan_name": _plan_name(_lower(sub.get("plan_code"))),
                "payment_method": sub.get("provider") or "paystack",
                "channel_type": "web",
                "gateway_response": None,
                "source": "user_subscriptions",
            }
        )

    rows_out.sort(key=lambda r: str(r.get("paid_at") or r.get("created_at") or ""), reverse=True)
    latest_success = next((r for r in rows_out if _lower(r.get("status")) in {"success", "paid", "active"}), None)

    return jsonify(
        {
            "ok": True,
            "billing_route_version": BILLING_ROUTE_VERSION,
            "account_id": account_id,
            "history": {
                "ok": True,
                "count": len(rows_out),
                "rows": rows_out[:limit],
                "latest_success": latest_success,
                "db_warning": "; ".join(warnings) if warnings else None,
            },
        }
    ), 200


@bp.post("/webhook")
@bp.post("/billing/webhook")
def billing_webhook():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not payload:
        return _json_error("No webhook payload received.", 400, error="empty_webhook_payload")

    raw = request.get_data() or b""
    signature = request.headers.get("X-Paystack-Signature") or request.headers.get("x-paystack-signature") or ""

    if signature:
        try:
            if not verify_webhook_signature(raw, signature):
                return _json_error("Invalid Paystack webhook signature.", 401, error="invalid_webhook_signature")
        except Exception as exc:
            return _json_error("Webhook signature check failed.", 401, error="signature_check_failed", root_cause=f"{type(exc).__name__}: {_clip(exc)}")

    event = payload.get("event")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    reference = _clean(data.get("reference"))
    status = _lower(data.get("status"))

    _store_paystack_event(
        event_id=_clean(data.get("id")) or reference,
        event_type=_clean(event),
        reference=reference,
        payload=payload,
    )

    application: Dict[str, Any] = {}
    if event == "charge.success" and reference and status == "success":
        application = _apply_successful_payment(reference, data)

    return jsonify(
        {
            "ok": True,
            "billing_route_version": BILLING_ROUTE_VERSION,
            "message": "Webhook received",
            "event": event,
            "reference": reference,
            "processed": bool(application),
            "application": application or None,
        }
    ), 200
