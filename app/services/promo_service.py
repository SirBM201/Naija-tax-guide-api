# app/services/promo_service.py
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

from app.core.supabase_client import supabase

PROMO_SERVICE_VERSION = "2026-05-30-batch35B-promo-checkout-discount-reward"


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_dt().isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _normalize_code(value: Any) -> str:
    code = _clean(value).upper()
    code = re.sub(r"[^A-Z0-9_-]+", "", code)
    return code[:80]


def _response_data(resp: Any):
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def _first(resp: Any) -> Optional[Dict[str, Any]]:
    rows = _response_data(resp)
    return rows[0] if rows else None


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value is None:
            return default
        return Decimal(str(value))
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _parse_dt(value: Any):
    if not value:
        return None
    try:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except Exception:
        return None


def _hold_days() -> int:
    raw = str(os.getenv("PROMO_REWARD_HOLD_DAYS") or os.getenv("REFERRAL_REWARD_HOLD_DAYS") or "14").strip()
    try:
        n = int(raw)
        return n if n >= 0 else 14
    except Exception:
        return 14


def _initial_reward_status() -> str:
    return _lower(os.getenv("PROMO_REWARD_INITIAL_STATUS") or os.getenv("REFERRAL_REWARD_INITIAL_STATUS") or "pending") or "pending"


def _currency() -> str:
    return _clean(os.getenv("PROMO_REWARD_CURRENCY") or os.getenv("REFERRAL_REWARD_CURRENCY") or "NGN").upper() or "NGN"


def _frontend_base_url() -> str:
    for key in (
        "FRONTEND_BASE_URL",
        "FRONTEND_APP_URL",
        "NEXT_PUBLIC_APP_URL",
        "APP_PUBLIC_URL",
        "APP_BASE_URL",
    ):
        value = _clean(os.getenv(key))
        if value:
            return value.rstrip("/")
    return "https://www.naijataxguides.com"


def _backend_base_url() -> str:
    for key in ("BACKEND_BASE_URL", "PUBLIC_BACKEND_BASE_URL", "API_BASE_URL", "KOYEB_PUBLIC_URL"):
        value = _clean(os.getenv(key))
        if value:
            return value.rstrip("/")
    return "https://incredible-nonie-bmsconcept-37359733.koyeb.app"


def _whatsapp_bot_phone() -> str:
    raw = (
        os.getenv("WHATSAPP_BOT_PHONE_NUMBER")
        or os.getenv("WHATSAPP_BUSINESS_PHONE_NUMBER")
        or os.getenv("WHATSAPP_PHONE_NUMBER")
        or "2347034941158"
    )
    digits = re.sub(r"\D+", "", str(raw or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def _telegram_bot_username() -> str:
    raw = (
        os.getenv("TELEGRAM_BOT_USERNAME")
        or os.getenv("TG_BOT_USERNAME")
        or "naija_tax_guide_bot"
    )
    username = str(raw or "").strip().lstrip("@")
    username = re.sub(r"[^A-Za-z0-9_]+", "", username)
    return username or "naija_tax_guide_bot"


def _request_ip(request_obj: Any = None) -> str:
    if request_obj is None:
        return ""
    forwarded = request_obj.headers.get("x-forwarded-for") or request_obj.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request_obj.remote_addr or ""


def _safe_update(table: str, payload: Dict[str, Any], column: str, value: Any) -> Dict[str, Any]:
    try:
        resp = _sb().table(table).update(payload).eq(column, value).execute()
        return {"ok": True, "row": _first(resp)}
    except Exception as exc:
        return {"ok": False, "error": f"{table}.update: {type(exc).__name__}: {repr(exc)[:700]}"}


def _safe_insert(table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        resp = _sb().table(table).insert(payload).execute()
        return {"ok": True, "row": _first(resp)}
    except Exception as exc:
        return {"ok": False, "error": f"{table}.insert: {type(exc).__name__}: {repr(exc)[:700]}"}


def get_promo_code_by_code(code: Any) -> Optional[Dict[str, Any]]:
    promo_code = _normalize_code(code)
    if not promo_code:
        return None

    resp = (
        _sb()
        .table("promo_codes")
        .select("*")
        .eq("code", promo_code)
        .limit(1)
        .execute()
    )
    return _first(resp)


def validate_promo_code(code: Any) -> Dict[str, Any]:
    promo_code = _normalize_code(code)
    if not promo_code:
        return {"ok": False, "valid": False, "error": "promo_code_required"}

    row = get_promo_code_by_code(promo_code)
    if not row:
        return {"ok": True, "valid": False, "reason": "promo_code_not_found", "code": promo_code}

    status = _clean(row.get("status")).lower() or "inactive"
    if status != "active":
        return {"ok": True, "valid": False, "reason": f"promo_code_{status}", "code": promo_code, "promo": row}

    now = _now_dt()

    starts_at = _parse_dt(row.get("starts_at"))
    if starts_at and now < starts_at:
        return {"ok": True, "valid": False, "reason": "promo_code_not_started", "code": promo_code, "promo": row}

    expires_at = _parse_dt(row.get("expires_at"))
    if expires_at and now > expires_at:
        return {"ok": True, "valid": False, "reason": "promo_code_expired", "code": promo_code, "promo": row}

    max_uses = row.get("max_uses")
    if max_uses is not None and _to_int(max_uses, 0) > 0:
        if _to_int(row.get("used_count"), 0) >= _to_int(max_uses, 0):
            return {"ok": True, "valid": False, "reason": "promo_code_usage_limit_reached", "code": promo_code, "promo": row}

    return {"ok": True, "valid": True, "code": promo_code, "promo": row}


def build_promo_links(code: Any) -> Dict[str, str]:
    promo_code = _normalize_code(code)
    frontend = _frontend_base_url()
    backend = _backend_base_url()
    signup = f"{frontend}/signup?promo={quote_plus(promo_code)}"
    promo_hub = f"{frontend}/promo/{quote_plus(promo_code)}"
    short_hub = f"{frontend}/p/{quote_plus(promo_code)}"

    whatsapp_text = quote_plus(f"START PROMO {promo_code}")
    whatsapp = f"https://wa.me/{_whatsapp_bot_phone()}?text={whatsapp_text}"
    telegram = f"https://t.me/{_telegram_bot_username()}?start=promo_{quote_plus(promo_code)}"

    return {
        "code": promo_code,
        "promo_hub": promo_hub,
        "smart": promo_hub,
        "short": short_hub,
        "signup": signup,
        "website": signup,
        "whatsapp": whatsapp,
        "telegram": telegram,
        "track_website": f"{backend}/api/promo/track-and-go/{quote_plus(promo_code)}/website",
        "track_whatsapp": f"{backend}/api/promo/track-and-go/{quote_plus(promo_code)}/whatsapp",
        "track_telegram": f"{backend}/api/promo/track-and-go/{quote_plus(promo_code)}/telegram",
    }


def track_promo_event(
    *,
    promo_code: Any,
    event_type: str,
    selected_platform: str | None = None,
    account_id: str | None = None,
    landing_url: str | None = None,
    request_obj: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    code = _normalize_code(promo_code)
    if not code:
        return {"ok": False, "error": "promo_code_required"}

    try:
        payload = {
            "promo_code": code,
            "event_type": _clean(event_type) or "promo_event",
            "selected_platform": _clean(selected_platform) or None,
            "account_id": _clean(account_id) or None,
            "landing_url": _clean(landing_url) or None,
            "user_agent": request_obj.headers.get("user-agent") if request_obj is not None else None,
            "ip_address": _request_ip(request_obj),
            "metadata": metadata or {},
            "created_at": _now_iso(),
        }
        if not payload.get("account_id"):
            payload.pop("account_id", None)

        resp = _sb().table("promo_events").insert(payload).execute()
        return {"ok": True, "data": _response_data(resp)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {repr(exc)[:600]}"}


def get_promo_redemption_by_account_id(account_id: str) -> Optional[Dict[str, Any]]:
    account_id = _clean(account_id)
    if not account_id:
        return None

    resp = (
        _sb()
        .table("promo_redemptions")
        .select("*")
        .eq("account_id", account_id)
        .limit(1)
        .execute()
    )
    return _first(resp)


def get_promo_reward_by_payment_reference(payment_reference: str) -> Optional[Dict[str, Any]]:
    payment_reference = _clean(payment_reference)
    if not payment_reference:
        return None

    try:
        resp = (
            _sb()
            .table("promo_rewards")
            .select("*")
            .eq("payment_reference", payment_reference)
            .limit(1)
            .execute()
        )
        return _first(resp)
    except Exception:
        return None


def _get_referral_row_by_referred_account_id(account_id: str) -> Optional[Dict[str, Any]]:
    account_id = _clean(account_id)
    if not account_id:
        return None

    try:
        resp = (
            _sb()
            .table("referrals")
            .select("*")
            .eq("referred_account_id", account_id)
            .limit(1)
            .execute()
        )
        return _first(resp)
    except Exception:
        return None


def bootstrap_account_promo_state(
    *,
    account_id: str,
    promo_code: str | None = None,
    source: str = "signup",
) -> Dict[str, Any]:
    account_id = _clean(account_id)
    code = _normalize_code(promo_code)

    if not account_id:
        return {"ok": False, "captured": False, "error": "account_id_required"}
    if not code:
        return {"ok": True, "captured": False, "reason": "no_promo_code"}

    existing_promo = get_promo_redemption_by_account_id(account_id)
    if existing_promo:
        return {
            "ok": True,
            "captured": False,
            "reason": "promo_already_attached_to_account",
            "redemption": existing_promo,
        }

    existing_referral = _get_referral_row_by_referred_account_id(account_id)
    if existing_referral:
        return {
            "ok": True,
            "captured": False,
            "reason": "referral_already_attached_to_account",
            "referral": existing_referral,
        }

    validation = validate_promo_code(code)
    if not validation.get("valid"):
        return {
            "ok": True,
            "captured": False,
            "reason": validation.get("reason") or validation.get("error") or "invalid_promo_code",
            "code": code,
            "validation": validation,
        }

    promo = validation["promo"]
    now_iso = _now_iso()

    payload = {
        "promo_code_id": promo.get("id"),
        "promo_code": code,
        "account_id": account_id,
        "status": "pending",
        "source": _clean(source) or "signup",
        "benefit_type": promo.get("benefit_type") or "percent_discount",
        "discount_percent": str(_to_decimal(promo.get("discount_percent"), Decimal("0"))),
        "discount_amount_ngn": str(_to_decimal(promo.get("discount_amount_ngn"), Decimal("0"))),
        "bonus_credits": _to_int(promo.get("bonus_credits"), 0),
        "reward_type": promo.get("reward_type") or "cash",
        "reward_amount_ngn": str(_to_decimal(promo.get("reward_amount_ngn"), Decimal("0"))),
        "reward_percent": str(_to_decimal(promo.get("reward_percent"), Decimal("0"))),
        "reward_status": None,
        "signup_at": now_iso,
        "metadata": {
            "promo_name": promo.get("name"),
            "owner_name": promo.get("owner_name"),
            "promo_type": promo.get("promo_type"),
            "service_version": PROMO_SERVICE_VERSION,
        },
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    try:
        resp = _sb().table("promo_redemptions").insert(payload).execute()
        redemption = _first(resp) or payload

        try:
            _sb().table("promo_codes").update(
                {
                    "used_count": _to_int(promo.get("used_count"), 0) + 1,
                    "updated_at": now_iso,
                }
            ).eq("id", promo.get("id")).execute()
        except Exception:
            pass

        return {
            "ok": True,
            "captured": True,
            "code": code,
            "promo": promo,
            "redemption": redemption,
        }
    except Exception as exc:
        again = get_promo_redemption_by_account_id(account_id)
        if again:
            return {
                "ok": True,
                "captured": False,
                "reason": "promo_already_attached_to_account",
                "redemption": again,
            }

        return {
            "ok": False,
            "captured": False,
            "error": "promo_redemption_insert_failed",
            "root_cause": f"{type(exc).__name__}: {repr(exc)[:700]}",
        }


def calculate_promo_checkout_preview(
    *,
    account_id: str,
    plan_code: str,
    original_amount_kobo: int,
) -> Dict[str, Any]:
    """
    Checkout discount helper.

    Rule:
    - Promo is not entered at payment.
    - Discount applies only if a pending/applied promo_redemption exists for this account.
    - For safety, maximum percentage discount is capped at 50%.
    """
    redemption = get_promo_redemption_by_account_id(account_id)
    original = max(0, int(original_amount_kobo or 0))

    if not redemption:
        return {
            "ok": True,
            "applies": False,
            "reason": "no_promo_redemption",
            "original_amount_kobo": original,
            "discount_amount_kobo": 0,
            "final_amount_kobo": original,
        }

    status = _clean(redemption.get("status")).lower()
    if status not in {"pending", "applied"}:
        return {
            "ok": True,
            "applies": False,
            "reason": f"promo_redemption_status_{status}",
            "redemption": redemption,
            "original_amount_kobo": original,
            "discount_amount_kobo": 0,
            "final_amount_kobo": original,
        }

    discount = 0
    percent = _to_decimal(redemption.get("discount_percent"), Decimal("0"))
    fixed_ngn = _to_decimal(redemption.get("discount_amount_ngn"), Decimal("0"))

    if percent > 0:
        if percent > 50:
            percent = Decimal("50")
        discount = int((Decimal(original) * percent / Decimal("100")).quantize(Decimal("1")))

    if fixed_ngn > 0:
        discount = max(discount, int(fixed_ngn * 100))

    if discount >= original:
        discount = max(0, original - 100)

    final = max(0, original - discount)

    return {
        "ok": True,
        "applies": discount > 0,
        "reason": "promo_applies" if discount > 0 else "no_discount_value",
        "redemption": redemption,
        "plan_code": plan_code,
        "promo_code": redemption.get("promo_code"),
        "original_amount_kobo": original,
        "discount_amount_kobo": discount,
        "final_amount_kobo": final,
    }


def record_promo_checkout_started(
    *,
    account_id: str,
    payment_reference: str,
    plan_code: str,
    original_amount_kobo: int,
    discount_amount_kobo: int,
    final_amount_kobo: int,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    account_id = _clean(account_id)
    payment_reference = _clean(payment_reference)
    plan_code = _clean(plan_code).lower()

    if not account_id:
        return {"ok": False, "updated": False, "error": "account_id_required"}
    if not payment_reference:
        return {"ok": False, "updated": False, "error": "payment_reference_required"}

    redemption = get_promo_redemption_by_account_id(account_id)
    if not redemption:
        return {"ok": True, "updated": False, "reason": "no_promo_redemption"}

    status = _clean(redemption.get("status")).lower()
    if status not in {"pending", "applied"}:
        return {"ok": True, "updated": False, "reason": f"promo_redemption_status_{status}", "redemption": redemption}

    redemption_id = _clean(redemption.get("id"))
    now_iso = _now_iso()
    old_meta = redemption.get("metadata") if isinstance(redemption.get("metadata"), dict) else {}

    payload = {
        "status": "applied",
        "plan_code": plan_code,
        "payment_reference": payment_reference,
        "original_amount_kobo": int(original_amount_kobo or 0),
        "discount_amount_kobo": int(discount_amount_kobo or 0),
        "final_amount_kobo": int(final_amount_kobo or 0),
        "checkout_started_at": redemption.get("checkout_started_at") or now_iso,
        "metadata": {
            **old_meta,
            "checkout": {
                "payment_reference": payment_reference,
                "plan_code": plan_code,
                "original_amount_kobo": int(original_amount_kobo or 0),
                "discount_amount_kobo": int(discount_amount_kobo or 0),
                "final_amount_kobo": int(final_amount_kobo or 0),
            },
            "billing_metadata": metadata or {},
            "service_version": PROMO_SERVICE_VERSION,
        },
        "updated_at": now_iso,
    }

    result = _safe_update("promo_redemptions", payload, "id", redemption_id)
    return {
        "ok": bool(result.get("ok")),
        "updated": bool(result.get("ok")),
        "redemption_id": redemption_id,
        "promo_code": redemption.get("promo_code"),
        "result": result,
    }


def qualify_promo_after_successful_payment(
    *,
    paying_account_id: str,
    payment_reference: str,
    plan_code: str | None = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Mark a signup promo as paid/qualified and create a pending promo reward.

    Idempotent protection:
    - If a promo reward already exists for payment_reference, no duplicate is created.
    - If no promo_redemption exists for the paying account, it returns safely.
    """
    paying_account_id = _clean(paying_account_id)
    payment_reference = _clean(payment_reference)
    plan_code = _clean(plan_code).lower() or None
    metadata = metadata or {}

    if not paying_account_id:
        return {"ok": False, "qualified": False, "error": "paying_account_id_required"}
    if not payment_reference:
        return {"ok": False, "qualified": False, "error": "payment_reference_required"}

    existing_reward = get_promo_reward_by_payment_reference(payment_reference)
    if existing_reward:
        return {
            "ok": True,
            "qualified": False,
            "already_qualified": True,
            "reason": "promo_reward_already_exists_for_payment",
            "reward": existing_reward,
        }

    redemption = get_promo_redemption_by_account_id(paying_account_id)
    if not redemption:
        return {"ok": True, "qualified": False, "reason": "no_promo_redemption"}

    status = _clean(redemption.get("status")).lower()
    if status in {"disqualified", "expired", "reversed", "refunded", "cancelled", "canceled"}:
        return {"ok": True, "qualified": False, "reason": f"promo_redemption_status_{status}", "redemption": redemption}

    promo_code = _normalize_code(redemption.get("promo_code"))
    promo = get_promo_code_by_code(promo_code) or {}
    now = _now_dt()
    now_iso = now.isoformat()

    original_amount_kobo = _to_int(metadata.get("original_amount_kobo") or redemption.get("original_amount_kobo") or metadata.get("amount_kobo"), 0)
    discount_amount_kobo = _to_int(metadata.get("discount_amount_kobo") or redemption.get("discount_amount_kobo"), 0)
    final_amount_kobo = _to_int(metadata.get("final_amount_kobo") or redemption.get("final_amount_kobo") or metadata.get("amount_kobo"), 0)

    reward_amount = _to_decimal(redemption.get("reward_amount_ngn") or promo.get("reward_amount_ngn"), Decimal("0"))
    reward_percent = _to_decimal(redemption.get("reward_percent") or promo.get("reward_percent"), Decimal("0"))

    if reward_amount <= 0 and reward_percent > 0 and final_amount_kobo > 0:
        reward_amount = (Decimal(final_amount_kobo) / Decimal("100") * reward_percent / Decimal("100")).quantize(Decimal("0.01"))

    hold_days = _hold_days()
    available_at = (now + timedelta(days=hold_days)).isoformat()

    beneficiary_account_id = _clean(promo.get("owner_account_id")) or None
    beneficiary_name = _clean(promo.get("owner_name")) or _clean(promo.get("name")) or promo_code

    redemption_update = {
        "status": "qualified",
        "plan_code": plan_code,
        "payment_reference": payment_reference,
        "original_amount_kobo": original_amount_kobo,
        "discount_amount_kobo": discount_amount_kobo,
        "final_amount_kobo": final_amount_kobo,
        "paid_at": metadata.get("paid_at") or now_iso,
        "qualified_at": now_iso,
        "reward_status": _initial_reward_status() if reward_amount > 0 else "no_reward_amount",
        "updated_at": now_iso,
    }

    redemption_update_result = _safe_update("promo_redemptions", redemption_update, "id", redemption.get("id"))

    reward_row = None
    reward_insert_result: Dict[str, Any] = {"ok": True, "skipped": True, "reason": "no_reward_amount"}

    if reward_amount > 0:
        reward_payload = {
            "promo_redemption_id": redemption.get("id"),
            "promo_code_id": redemption.get("promo_code_id") or promo.get("id"),
            "promo_code": promo_code,
            "account_id": beneficiary_account_id,
            "beneficiary_account_id": beneficiary_account_id,
            "beneficiary_name": beneficiary_name,
            "paying_account_id": paying_account_id,
            "reward_type": redemption.get("reward_type") or promo.get("reward_type") or "cash",
            "reward_amount": str(reward_amount),
            "reward_amount_ngn": str(reward_amount),
            "reward_percent": str(reward_percent),
            "currency": _currency(),
            "status": _initial_reward_status(),
            "hold_days": hold_days,
            "plan_code": plan_code,
            "payment_reference": payment_reference,
            "original_amount_kobo": original_amount_kobo,
            "discount_amount_kobo": discount_amount_kobo,
            "final_amount_kobo": final_amount_kobo,
            "earned_at": now_iso,
            "available_at": available_at,
            "metadata": {
                "promo_name": promo.get("name"),
                "promo_owner_name": promo.get("owner_name"),
                "promo_type": promo.get("promo_type"),
                "paying_account_id": paying_account_id,
                "service_version": PROMO_SERVICE_VERSION,
            },
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        reward_insert_result = _safe_insert("promo_rewards", reward_payload)
        reward_row = reward_insert_result.get("row")

        if reward_insert_result.get("ok"):
            _safe_update(
                "promo_redemptions",
                {"reward_created_at": now_iso, "reward_status": _initial_reward_status(), "updated_at": now_iso},
                "id",
                redemption.get("id"),
            )

    paid_count_note: Dict[str, Any] = {"ok": True, "updated": False, "reason": "promo_code_not_found"}
    if promo.get("id"):
        current_count = _to_int(promo.get("paid_conversion_count"), 0)
        paid_count_note = _safe_update(
            "promo_codes",
            {"paid_conversion_count": current_count + 1, "updated_at": now_iso},
            "id",
            promo.get("id"),
        )

    return {
        "ok": True,
        "qualified": True,
        "promo_code": promo_code,
        "redemption_id": redemption.get("id"),
        "reward": reward_row,
        "reward_insert_result": reward_insert_result,
        "redemption_update": redemption_update_result,
        "paid_conversion_count_update": paid_count_note,
        "hold_days": hold_days,
        "initial_reward_status": _initial_reward_status(),
    }
