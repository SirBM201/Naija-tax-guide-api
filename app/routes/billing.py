from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple
import json
import logging

from flask import Blueprint, jsonify, request, session

from app.core.supabase_client import supabase
from app.services.plans_service import get_plan, list_plans
from app.services.web_auth_service import get_account_id_from_request
from app.services.auth_service import get_current_user
from app.services.paystack_service import (
    create_reference,
    initialize_transaction,
    verify_transaction,
    verify_webhook_signature,
)
from app.services.credits_service import (
    init_credits_for_plan,
    get_credit_balance_details,
    get_daily_usage,
)
from app.services.subscription_guard import get_subscription_snapshot
from app.services.referral_service import ensure_referral_profile, qualify_referral_after_successful_payment
from app.services.channel_post_payment_service import notify_channel_payment_success

bp = Blueprint("billing", __name__)
logger = logging.getLogger(__name__)
ROUTE_VERSION = "billing_webhook_v4_raw_json_fix"


def _sb():
    return supabase() if callable(supabase) else supabase


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _safe_json() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _clip(v: Any, n: int = 400) -> str:
    s = str(v or "")
    return s if len(s) <= n else s[:n] + "...<truncated>"


def _safe_dt(v: Any) -> Optional[datetime]:
    try:
        if not v:
            return None
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _fail(*, error: str, root_cause: Any = None, extra: Dict[str, Any] | None = None, status: int = 400):
    out: Dict[str, Any] = {"ok": False, "error": error}
    if root_cause is not None:
        out["root_cause"] = root_cause
    if extra:
        out.update(extra)
    return jsonify(out), status


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


def _get_account_row(account_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    account_id here is canonical accounts.account_id from web auth.
    """
    account_id = (account_id or "").strip()
    if not account_id:
        return None, {"error": "account_id_required", "root_cause": "missing_account_id"}

    try:
        q = (
            _sb()
            .table("accounts")
            .select("id,account_id,email,provider,provider_user_id,display_name,created_at,updated_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
        if rows:
            return rows[0], None
    except Exception as e:
        return None, {
            "error": "account_lookup_failed",
            "root_cause": f"lookup by account_id failed: {type(e).__name__}: {_clip(e)}",
        }

    try:
        q = (
            _sb()
            .table("accounts")
            .select("id,account_id,email,provider,provider_user_id,display_name,created_at,updated_at")
            .eq("id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
        if rows:
            return rows[0], None
    except Exception as e:
        return None, {
            "error": "account_lookup_failed",
            "root_cause": f"lookup by id failed: {type(e).__name__}: {_clip(e)}",
        }

    return None, {"error": "account_not_found", "root_cause": "no accounts row matched provided account_id"}


def _resolve_checkout_email(account_id: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    row, err = _get_account_row(account_id)
    if err:
        return None, err

    email = (row.get("email") or "").strip().lower()
    if "@" in email:
        return email, None

    provider = (row.get("provider") or "").strip().lower()
    provider_user_id = (row.get("provider_user_id") or "").strip().lower()
    if provider == "web" and "@" in provider_user_id:
        return provider_user_id, None

    return None, {
        "error": "checkout_email_missing",
        "root_cause": "No valid email found on accounts.email or provider_user_id",
        "details": {
            "account_id": account_id,
            "provider": provider,
            "provider_user_id": provider_user_id,
            "email": email,
        },
        "fix": "Ensure accounts.email is populated for this authenticated account.",
    }


def _get_subscription_row(account_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    account_id = (account_id or "").strip()
    if not account_id:
        return None, {"error": "account_id_required", "root_cause": "missing_account_id"}

    try:
        q = (
            _sb()
            .table("user_subscriptions")
            .select("*")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
        return (rows[0] if rows else None), None
    except Exception as e:
        return None, {
            "error": "subscription_lookup_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
        }


def _subscription_is_active_now(sub: Optional[Dict[str, Any]]) -> bool:
    if not sub:
        return False

    status = str(sub.get("status") or "").strip().lower()
    is_active = bool(sub.get("is_active"))
    expires_at = _safe_dt(sub.get("expires_at"))
    grace_until = _safe_dt(sub.get("grace_until"))

    now = _now()

    if not is_active or status != "active":
        return False

    if expires_at and now < expires_at:
        return True

    if grace_until and now < grace_until:
        return True

    return expires_at is None


def _plan_sort_tuple(plan: Dict[str, Any]) -> Tuple[int, int]:
    """
    Compare plans in a deterministic way:
    1. higher price is considered higher tier
    2. if price matches, longer duration is considered higher tier
    """
    price = int(plan.get("price") or 0)
    duration = int(plan.get("duration_days") or 0)
    return (price, duration)


def _compare_plan_tier(current_plan: Dict[str, Any], target_plan: Dict[str, Any]) -> int:
    """
    Returns:
      1  -> target is higher tier than current
      0  -> same tier
      -1 -> target is lower tier than current
    """
    a = _plan_sort_tuple(current_plan)
    b = _plan_sort_tuple(target_plan)

    if b > a:
        return 1
    if b < a:
        return -1
    return 0


def _derive_change_mode(current_plan_code: Optional[str], target_plan_code: str) -> str:
    current_plan_code = (current_plan_code or "").strip().lower()
    target_plan_code = (target_plan_code or "").strip().lower()

    if not current_plan_code:
        return "new_purchase"

    current_plan = get_plan(current_plan_code)
    target_plan = get_plan(target_plan_code)

    if not current_plan or not target_plan:
        return "unknown"

    cmp = _compare_plan_tier(current_plan, target_plan)
    if cmp > 0:
        return "upgrade_now"
    if cmp < 0:
        return "downgrade_at_period_end"
    return "same_plan"


def _same_active_plan_guard(account_id: str, requested_plan_code: str) -> Optional[Tuple[Any, int]]:
    sub, err = _get_subscription_row(account_id)
    if err:
        return None

    if not sub:
        return None

    current_plan_code = (sub.get("plan_code") or "").strip().lower()
    requested_plan_code = (requested_plan_code or "").strip().lower()
    same_plan = current_plan_code and current_plan_code == requested_plan_code

    if same_plan and _subscription_is_active_now(sub):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "same_active_plan_exists",
                    "root_cause": "requested_plan_matches_current_active_plan",
                    "fix": "Use billing page to review the current subscription instead of purchasing the same active plan again.",
                    "details": {
                        "account_id": account_id,
                        "current_subscription": {
                            "id": sub.get("id"),
                            "plan_code": sub.get("plan_code"),
                            "status": sub.get("status"),
                            "is_active": sub.get("is_active"),
                            "expires_at": sub.get("expires_at"),
                            "provider": sub.get("provider"),
                            "provider_ref": sub.get("provider_ref"),
                            "pending_plan_code": sub.get("pending_plan_code"),
                            "pending_starts_at": sub.get("pending_starts_at"),
                        },
                    },
                }
            ),
            409,
        )

    return None


def _build_subscription_summary(sub: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not sub:
        return {
            "has_subscription": False,
            "is_active_now": False,
            "has_pending_change": False,
            "current_plan_code": None,
            "pending_plan_code": None,
            "pending_starts_at": None,
        }

    return {
        "has_subscription": True,
        "is_active_now": _subscription_is_active_now(sub),
        "has_pending_change": bool(sub.get("pending_plan_code")),
        "current_plan_code": sub.get("plan_code"),
        "pending_plan_code": sub.get("pending_plan_code"),
        "pending_starts_at": sub.get("pending_starts_at"),
        "status": sub.get("status"),
        "is_active": sub.get("is_active"),
        "started_at": sub.get("started_at"),
        "expires_at": sub.get("expires_at"),
        "current_period_end": sub.get("current_period_end"),
        "provider": sub.get("provider"),
        "provider_ref": sub.get("provider_ref"),
    }


def _init_plan_credits_safe(account_id: str, plan_code: str) -> Dict[str, Any]:
    """
    Best-effort credit initialization after successful plan activation.
    Does not throw.
    """
    try:
        res = init_credits_for_plan(account_id, plan_code)
        return res if isinstance(res, dict) else {"ok": False, "error": "credit_init_unknown_result"}
    except Exception as e:
        return {
            "ok": False,
            "error": "credit_init_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "details": {"account_id": account_id, "plan_code": plan_code},
        }


def _upsert_user_subscription(
    *,
    account_id: str,
    plan_code: str,
    duration_days: int,
    provider: str,
    provider_ref: str,
) -> Dict[str, Any]:
    """
    user_subscriptions uses canonical account_id (accounts.account_id).
    This is used when a paid checkout succeeds and the new plan becomes active immediately.
    """
    now = _now()
    expires = now + timedelta(days=int(duration_days))
    now_iso = now.isoformat()
    exp_iso = expires.isoformat()

    existing = (
        _sb()
        .table("user_subscriptions")
        .select("id,account_id,plan_code,status,is_active,expires_at,pending_plan_code,pending_starts_at")
        .eq("account_id", account_id)
        .limit(1)
        .execute()
    )
    rows = getattr(existing, "data", None) or []

    patch = {
        "plan_code": plan_code,
        "status": "active",
        "is_active": True,
        "started_at": now_iso,
        "expires_at": exp_iso,
        "current_period_end": exp_iso,
        "provider": provider,
        "provider_ref": provider_ref,
        "pending_plan_code": None,
        "pending_starts_at": None,
        "updated_at": now_iso,
    }

    if rows:
        sub_id = rows[0]["id"]
        upd = _sb().table("user_subscriptions").update(patch).eq("id", sub_id).execute()
        out = getattr(upd, "data", None) or []
        return out[0] if out else {"id": sub_id, "account_id": account_id, **patch}

    ins = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": "active",
        "is_active": True,
        "started_at": now_iso,
        "expires_at": exp_iso,
        "current_period_end": exp_iso,
        "provider": provider,
        "provider_ref": provider_ref,
        "pending_plan_code": None,
        "pending_starts_at": None,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    created = _sb().table("user_subscriptions").insert(ins).execute()
    out = getattr(created, "data", None) or []
    return out[0] if out else ins


def _schedule_downgrade(
    *,
    account_id: str,
    target_plan_code: str,
    current_sub: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    sub_id = str(current_sub.get("id") or "").strip()
    if not sub_id:
        return None, {"error": "subscription_id_missing", "root_cause": "current_subscription_missing_id"}

    target_plan_code = (target_plan_code or "").strip().lower()
    existing_pending = (current_sub.get("pending_plan_code") or "").strip().lower()
    pending_starts_at = current_sub.get("pending_starts_at")
    current_period_end = current_sub.get("current_period_end") or current_sub.get("expires_at")

    if existing_pending == target_plan_code and pending_starts_at:
        return None, {
            "error": "downgrade_already_scheduled",
            "root_cause": "same_pending_plan_already_exists",
            "details": {
                "pending_plan_code": existing_pending,
                "pending_starts_at": pending_starts_at,
            },
        }

    if not current_period_end:
        return None, {
            "error": "current_period_end_missing",
            "root_cause": "cannot_schedule_downgrade_without_current_period_end",
        }

    patch = {
        "pending_plan_code": target_plan_code,
        "pending_starts_at": current_period_end,
        "updated_at": _now_iso(),
    }

    try:
        upd = _sb().table("user_subscriptions").update(patch).eq("id", sub_id).execute()
        out = getattr(upd, "data", None) or []
        row = out[0] if out else {**current_sub, **patch}
        return row, None
    except Exception as e:
        return None, {
            "error": "downgrade_schedule_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
        }


def _clear_pending_change(
    *,
    sub_id: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not sub_id:
        return None, {"error": "subscription_id_missing", "root_cause": "missing_sub_id"}

    patch = {
        "pending_plan_code": None,
        "pending_starts_at": None,
        "updated_at": _now_iso(),
    }

    try:
        upd = _sb().table("user_subscriptions").update(patch).eq("id", sub_id).execute()
        out = getattr(upd, "data", None) or []
        row = out[0] if out else patch
        return row, None
    except Exception as e:
        return None, {
            "error": "pending_change_clear_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
        }


def _start_checkout_for_plan_change(
    *,
    account_id: str,
    plan_code: str,
    change_mode: str,
    current_plan_code: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    plan = get_plan(plan_code)
    if not plan or not plan.get("active", True):
        return None, {"error": "plan_not_found", "root_cause": f"unknown_or_inactive_plan:{plan_code}"}

    price_ngn = int(plan.get("price") or 0)
    if price_ngn <= 0:
        return None, {"error": "plan_price_missing", "root_cause": f"invalid_price_for_plan:{plan_code}"}

    email, email_err = _resolve_checkout_email(account_id)
    if email_err or not email:
        return None, {
            "error": "checkout_email_missing",
            "root_cause": (email_err or {}).get("root_cause"),
            "details": (email_err or {}).get("details"),
            "fix": (email_err or {}).get("fix"),
        }

    reference = create_reference("NTG")
    metadata = {
        "product": "naija_tax_guide",
        "plan_code": plan_code,
        "account_id": account_id,
        "email": email,
        "change_mode": change_mode,
        "current_plan_code": (current_plan_code or "").strip().lower() or None,
    }

    try:
        ps = initialize_transaction(
            email=email,
            amount_kobo=price_ngn * 100,
            reference=reference,
            metadata=metadata,
        )
    except Exception as e:
        return None, {
            "error": "paystack_init_failed",
            "root_cause": repr(e),
            "details": {
                "account_id": account_id,
                "email": email,
                "plan_code": plan_code,
                "change_mode": change_mode,
            },
        }

    data = (ps or {}).get("data") or {}
    return {
        "ok": True,
        "action": "checkout_started",
        "reference": reference,
        "authorization_url": data.get("authorization_url"),
        "access_code": data.get("access_code"),
        "plan": plan,
        "account_id": account_id,
        "email": email,
        "change_mode": change_mode,
    }, None


def _payment_already_applied(*, account_id: str, plan_code: str, reference: str) -> bool:
    account_id = (account_id or "").strip()
    plan_code = (plan_code or "").strip().lower()
    reference = (reference or "").strip()

    if not (account_id and plan_code and reference):
        return False

    try:
        q = (
            _sb()
            .table("user_subscriptions")
            .select("id,account_id,plan_code,status,is_active,provider_ref")
            .eq("account_id", account_id)
            .eq("plan_code", plan_code)
            .eq("provider_ref", reference)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
        if not rows:
            return False

        row = rows[0] or {}
        status = str(row.get("status") or "").strip().lower()
        return bool(row.get("is_active")) and status == "active"
    except Exception:
        return False


def _extract_reference(data: Dict[str, Any]) -> str:
    return (data.get("reference") or "").strip()


def _extract_status(data: Dict[str, Any]) -> str:
    return str(data.get("status") or "").strip().lower()


def _extract_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    md = data.get("metadata")
    return md if isinstance(md, dict) else {}


def _get_account_id_from_session() -> Optional[str]:
    """Get account ID from Flask session first, then fallback to token auth."""
    # First try Flask session
    user_id = session.get("user_id")
    if user_id:
        logger.info(f"Account ID from session: {user_id}")
        return user_id
    
    # Fallback to token/cookie auth
    account_id, debug = get_account_id_from_request(request)
    if account_id:
        logger.info(f"Account ID from token/cookie: {account_id}")
        return account_id
    
    return None


# -------------------- ROUTES --------------------


@bp.get("/billing/plans")
def billing_plans():
    active_only = (request.args.get("active_only") or "1").strip() != "0"
    plans = list_plans(active_only=active_only)
    return jsonify({"ok": True, "plans": plans}), 200


@bp.get("/billing/plans/<plan_code>")
def billing_plan(plan_code: str):
    p = get_plan(plan_code)
    if not p:
        return jsonify({"ok": False, "error": "plan_not_found"}), 404
    return jsonify({"ok": True, "plan": p}), 200


def _monthly_ai_used_safe(account_id: str) -> int:
    try:
        from app.repositories.monthly_usage_repo import get_monthly_ai_usage
        return int(get_monthly_ai_usage(account_id) or 0)
    except Exception:
        return 0


def _normalized_billing_payload(
    *,
    account_id: str,
    sub: Optional[Dict[str, Any]],
    checkout_email: Optional[str],
    email_err: Optional[Dict[str, Any]],
    debug: Dict[str, Any],
    db_warning: Optional[str] = None,
    sub_err: Optional[Dict[str, Any]] = None,
    credit_details: Optional[Dict[str, Any]] = None,
    usage_today: Optional[Dict[str, Any]] = None,
    guard: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    guard = guard or get_subscription_snapshot(account_id)
    plan_code = (sub or {}).get("plan_code") or (guard or {}).get("plan_code")
    plan = get_plan(plan_code) if plan_code else None
    summary = _build_subscription_summary(sub)
    active_now = bool(summary.get("is_active_now")) or bool(((guard or {}).get("access") or {}).get("allowed"))
    status = (sub or {}).get("status") or (((guard or {}).get("access") or {}).get("status"))
    provider = (sub or {}).get("provider") or "paystack"
    provider_ref = (sub or {}).get("provider_ref")
    daily_usage_count = int(((usage_today or {}).get("count") or (usage_today or {}).get("daily_usage") or 0) or 0)
    included_credits = int((plan or {}).get("credits") or 0)
    daily_answers_limit = int((plan or {}).get("daily_answers_limit") or (guard or {}).get("daily_answers_limit") or 0)
    monthly_ai_used = _monthly_ai_used_safe(account_id)

    return {
        "ok": True,
        "account_id": account_id,
        "subscription": sub,
        "subscription_summary": summary,
        "checkout_email": checkout_email,
        "checkout_email_error": email_err,
        "db_warning": db_warning,
        "subscription_error": sub_err,
        "debug": debug,
        "guard": guard,
        "plan_code": plan_code,
        "plan_name": (plan or {}).get("name") or plan_code,
        "status": status,
        "active": active_now,
        "starts_at": (sub or {}).get("started_at") or (sub or {}).get("starts_at"),
        "started_at": (sub or {}).get("started_at") or (sub or {}).get("starts_at"),
        "expires_at": (sub or {}).get("expires_at"),
        "current_period_end": (sub or {}).get("current_period_end") or (sub or {}).get("expires_at"),
        "pending_plan_code": (sub or {}).get("pending_plan_code"),
        "pending_starts_at": (sub or {}).get("pending_starts_at"),
        "payment_reference": provider_ref,
        "last_payment_reference": provider_ref,
        "payment_method": provider,
        "provider": provider,
        "provider_name": provider.title() if provider else None,
        "auto_renew": False,
        "included_credits": included_credits,
        "ai_used_month": monthly_ai_used,
        "credit_balance": int(((credit_details or {}).get("balance") or 0) or 0),
        "credit_exists": bool((credit_details or {}).get("exists")),
        "credit_updated_at": (credit_details or {}).get("updated_at"),
        "daily_usage_count": daily_usage_count,
        "daily_answers_limit": daily_answers_limit,
    }


def _plan_name_from_code(plan_code: Optional[str]) -> Optional[str]:
    code = (plan_code or "").strip().lower()
    if not code:
        return None
    plan = get_plan(code)
    return (plan or {}).get("name") or code


def _event_preference_score(event_type: str, status: str) -> int:
    event = (event_type or "").strip().lower()
    state = (status or "").strip().lower()
    if event == "charge.success" and state == "success":
        return 300
    if event == "charge.success":
        return 250
    if event == "verify" and state == "success":
        return 200
    if event == "verify":
        return 150
    return 100


def _normalize_payment_history_row(
    *,
    row: Dict[str, Any],
    account_id: str,
    checkout_email: Optional[str],
) -> Optional[Dict[str, Any]]:
    payload = row.get("payload") if isinstance(row, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    metadata = _extract_metadata(data)
    metadata_account_id = (metadata.get("account_id") or "").strip()
    metadata_email = (metadata.get("email") or "").strip().lower()
    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    customer_email = (customer.get("email") or "").strip().lower()
    expected_email = (checkout_email or "").strip().lower()

    reference = (row.get("reference") or data.get("reference") or "").strip()
    if not reference:
        return None

    if metadata_account_id and metadata_account_id != account_id:
        return None

    if not metadata_account_id and expected_email:
        if metadata_email and metadata_email != expected_email:
            return None
        if customer_email and customer_email != expected_email:
            return None

    event_type = (row.get("event_type") or "").strip().lower()
    status = _extract_status(data) or ("success" if event_type == "charge.success" else "")
    amount_kobo = data.get("amount")
    try:
        amount_ngn = int(round(float(amount_kobo or 0) / 100.0))
    except Exception:
        amount_ngn = 0

    paid_at = (
        data.get("paid_at")
        or data.get("transaction_date")
        or data.get("created_at")
        or row.get("created_at")
    )

    plan_code = (metadata.get("plan_code") or "").strip().lower() or None
    gateway_response = data.get("gateway_response") or data.get("message") or None

    return {
        "reference": reference,
        "event_type": event_type or "unknown",
        "status": status or "unknown",
        "amount_ngn": amount_ngn,
        "currency": (data.get("currency") or "NGN"),
        "paid_at": paid_at,
        "created_at": row.get("created_at"),
        "plan_code": plan_code,
        "plan_name": _plan_name_from_code(plan_code),
        "payment_method": (data.get("channel") or "paystack"),
        "channel_type": (metadata.get("channel_type") or "").strip().lower() or None,
        "gateway_response": gateway_response,
        "source": "paystack_events",
        "_score": _event_preference_score(event_type, status),
    }


def _load_payment_history(
    *,
    account_id: str,
    checkout_email: Optional[str],
    current_sub: Optional[Dict[str, Any]] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 10), 50))
    rows = []
    db_warning = None

    try:
        res = (
            _sb()
            .table("paystack_events")
            .select("event_id,event_type,reference,payload,created_at")
            .order("created_at", desc=True)
            .limit(250)
            .execute()
        )
        rows = getattr(res, "data", None) or []
    except Exception as e:
        db_warning = f"{type(e).__name__}: {_clip(e)}"

    deduped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        normalized = _normalize_payment_history_row(
            row=row,
            account_id=account_id,
            checkout_email=checkout_email,
        )
        if not normalized:
            continue
        reference = normalized["reference"]
        existing = deduped.get(reference)
        if not existing:
            deduped[reference] = normalized
            continue
        existing_score = int(existing.get("_score") or 0)
        incoming_score = int(normalized.get("_score") or 0)
        existing_date = str(existing.get("paid_at") or existing.get("created_at") or "")
        incoming_date = str(normalized.get("paid_at") or normalized.get("created_at") or "")
        if incoming_score > existing_score or (incoming_score == existing_score and incoming_date > existing_date):
            deduped[reference] = normalized

    current_reference = ((current_sub or {}).get("provider_ref") or "").strip()
    if current_reference and current_reference not in deduped:
        deduped[current_reference] = {
            "reference": current_reference,
            "event_type": "subscription_snapshot",
            "status": (current_sub or {}).get("status") or "active",
            "amount_ngn": int((get_plan((current_sub or {}).get("plan_code") or "") or {}).get("price") or 0),
            "currency": "NGN",
            "paid_at": (current_sub or {}).get("started_at") or (current_sub or {}).get("updated_at"),
            "created_at": (current_sub or {}).get("created_at"),
            "plan_code": (current_sub or {}).get("plan_code"),
            "plan_name": _plan_name_from_code((current_sub or {}).get("plan_code")),
            "payment_method": (current_sub or {}).get("provider") or "paystack",
            "channel_type": None,
            "gateway_response": "Visible from active subscription snapshot.",
            "source": "user_subscriptions",
            "_score": 50,
        }

    history_rows = sorted(
        deduped.values(),
        key=lambda item: str(item.get("paid_at") or item.get("created_at") or ""),
        reverse=True,
    )[:limit]

    cleaned_rows = []
    for item in history_rows:
        x = dict(item)
        x.pop("_score", None)
        cleaned_rows.append(x)

    latest_success = next(
        (item for item in cleaned_rows if str(item.get("status") or "").lower() == "success"),
        None,
    )

    return {
        "ok": True,
        "count": len(cleaned_rows),
        "rows": cleaned_rows,
        "latest_success": latest_success,
        "db_warning": db_warning,
    }


@bp.get("/billing/me")
@bp.get("/billing/subscription")
def billing_me():
    # Try session auth first
    account_id = _get_account_id_from_session()
    
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": {"auth_method": "session_failed"}}), 401

    sub = None
    db_warning = None
    try:
        q = (
            _sb()
            .table("user_subscriptions")
            .select("*")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
        sub = rows[0] if rows else None
    except Exception as e:
        db_warning = repr(e)

    checkout_email, email_err = _resolve_checkout_email(account_id)
    credit_details = get_credit_balance_details(account_id)
    usage_today = get_daily_usage(account_id)
    guard = get_subscription_snapshot(account_id)

    payload = _normalized_billing_payload(
        account_id=account_id,
        sub=sub,
        checkout_email=checkout_email,
        email_err=email_err,
        debug={"auth_method": "session", "account_id": account_id},
        db_warning=db_warning,
        credit_details=credit_details,
        usage_today=usage_today,
        guard=guard,
    )
    history = _load_payment_history(
        account_id=account_id,
        checkout_email=checkout_email,
        current_sub=sub,
        limit=8,
    )
    payload["payment_history"] = history
    latest_success = history.get("latest_success") if isinstance(history, dict) else None
    if latest_success and not payload.get("payment_reference"):
        payload["payment_reference"] = latest_success.get("reference")
        payload["last_payment_reference"] = latest_success.get("reference")
    if latest_success and not payload.get("payment_method"):
        payload["payment_method"] = latest_success.get("payment_method")
    @bp.get("/billing/debug-state")
def billing_debug_state():
    account_id = _get_account_id_from_session()
    
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    return jsonify({
        "ok": True,
        "account_id": account_id,
        "subscription_guard_snapshot": {
            "access": {"allowed": True},
            "plan_code": "free",
            "daily_answers_limit": 0
        },
        "credit_balance": {"balance": 0, "used": 0},
        "daily_usage_today": {"count": 0},
        "whatsapp_linked": False,
        "telegram_linked": False,
        "whatsapp_verified": False,
        "telegram_verified": False
    }), 200
    return jsonify(payload),
