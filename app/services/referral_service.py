from __future__ import annotations

import os
import random
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.core.supabase_client import supabase


# =========================================================
# INTERNAL HELPERS
# =========================================================

def _sb():
    return supabase() if callable(supabase) else supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_code(value: str | None) -> str:
    return "".join(ch for ch in str(value or "").strip().upper() if ch.isalnum())


def _frontend_base_url() -> str:
    return (
        os.getenv("FRONTEND_APP_URL")
        or os.getenv("NEXT_PUBLIC_APP_URL")
        or os.getenv("APP_PUBLIC_URL")
        or ""
    ).rstrip("/")


def _referral_prefix() -> str:
    return _clean_code(os.getenv("REFERRAL_CODE_PREFIX") or "NTG")


def _referral_code_length() -> int:
    raw = str(os.getenv("REFERRAL_CODE_RANDOM_LENGTH") or "6").strip()
    try:
        n = int(raw)
        return n if n >= 4 else 6
    except Exception:
        return 6


def _completed_status() -> str:
    # Use "qualified" temporarily only if your DB still rejects "rewarded".
    return str(os.getenv("REFERRAL_COMPLETED_STATUS") or "rewarded").strip().lower()


def _reward_currency() -> str:
    return str(os.getenv("REFERRAL_REWARD_CURRENCY") or "NGN").strip().upper()


def _choice(chars: str, n: int) -> str:
    return "".join(random.choice(chars) for _ in range(n))


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value is None:
            return default
        return Decimal(str(value))
    except Exception:
        return default


def _response_data(resp: Any) -> List[Dict[str, Any]]:
    if resp is None:
        return []
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def _first(resp: Any) -> Optional[Dict[str, Any]]:
    rows = _response_data(resp)
    return rows[0] if rows else None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


# =========================================================
# CONFIG / REWARD RULES
# =========================================================

DEFAULT_PLAN_REWARD_MAP: Dict[str, Decimal] = {
    "monthly": Decimal(os.getenv("REFERRAL_REWARD_MONTHLY") or "1000"),
    "quarterly": Decimal(os.getenv("REFERRAL_REWARD_QUARTERLY") or "2500"),
    "yearly": Decimal(os.getenv("REFERRAL_REWARD_YEARLY") or "5000"),
}


@dataclass
class ReferralBootstrapResult:
    account_id: str
    own_profile: Dict[str, Any]
    captured_referral: Optional[Dict[str, Any]]
    skipped_reason: Optional[str] = None


# =========================================================
# LOW-LEVEL READ HELPERS
# =========================================================

def get_referral_profile_by_account_id(account_id: str) -> Optional[Dict[str, Any]]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return None

    resp = (
        _sb()
        .table("referral_profiles")
        .select("*")
        .eq("account_id", account_id)
        .limit(1)
        .execute()
    )
    return _first(resp)


def get_referral_profile_by_code(referral_code: str) -> Optional[Dict[str, Any]]:
    code = _clean_code(referral_code)
    if not code:
        return None

    resp = (
        _sb()
        .table("referral_profiles")
        .select("*")
        .eq("referral_code", code)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    return _first(resp)


def get_pending_or_active_referral_for_referred_account(referred_account_id: str) -> Optional[Dict[str, Any]]:
    referred_account_id = str(referred_account_id or "").strip()
    if not referred_account_id:
        return None

    resp = (
        _sb()
        .table("referrals")
        .select("*")
        .eq("referred_account_id", referred_account_id)
        .limit(1)
        .execute()
    )
    return _first(resp)


def get_reward_rows_for_referral(referral_id: str) -> List[Dict[str, Any]]:
    referral_id = str(referral_id or "").strip()
    if not referral_id:
        return []

    resp = (
        _sb()
        .table("referral_rewards")
        .select("*")
        .eq("referral_id", referral_id)
        .execute()
    )
    return _response_data(resp)


# =========================================================
# REFERRAL PROFILE CREATION
# =========================================================

def build_referral_link(referral_code: str) -> str:
    base = _frontend_base_url()
    code = _clean_code(referral_code)
    if not code:
        return ""
    if not base:
        return f"/signup?ref={code}"
    return f"{base}/signup?ref={code}"


def _generate_candidate_referral_code() -> str:
    prefix = _referral_prefix()
    length = _referral_code_length()
    suffix = _choice(string.ascii_uppercase + string.digits, length)
    return f"{prefix}{suffix}"


def generate_unique_referral_code(max_attempts: int = 50) -> str:
    for _ in range(max_attempts):
        candidate = _generate_candidate_referral_code()
        exists = get_referral_profile_by_code(candidate)
        if not exists:
            return candidate
    raise RuntimeError("Unable to generate a unique referral code after multiple attempts.")


def ensure_referral_profile(account_id: str) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        raise ValueError("account_id is required")

    existing = get_referral_profile_by_account_id(account_id)
    if existing:
        return existing

    code = generate_unique_referral_code()
    link = build_referral_link(code)
    now_iso = _now_iso()

    payload = {
        "account_id": account_id,
        "referral_code": code,
        "referral_link": link,
        "is_active": True,
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    resp = _sb().table("referral_profiles").insert(payload).execute()
    created = _first(resp)
    if created:
        return created

    # In race cases, fetch again.
    again = get_referral_profile_by_account_id(account_id)
    if again:
        return again

    raise RuntimeError("Failed to create referral profile.")


# =========================================================
# REFERRAL CAPTURE / ACCOUNT BOOTSTRAP
# =========================================================

def create_pending_referral(
    *,
    referrer_account_id: str,
    referred_account_id: str,
    referral_code: str,
    source: str = "signup",
) -> Dict[str, Any]:
    referrer_account_id = str(referrer_account_id or "").strip()
    referred_account_id = str(referred_account_id or "").strip()
    code = _clean_code(referral_code)

    if not referrer_account_id:
        raise ValueError("referrer_account_id is required")
    if not referred_account_id:
        raise ValueError("referred_account_id is required")
    if not code:
        raise ValueError("referral_code is required")
    if referrer_account_id == referred_account_id:
        raise ValueError("Self-referral is not allowed")

    existing = get_pending_or_active_referral_for_referred_account(referred_account_id)
    if existing:
        return existing

    now_iso = _now_iso()
    payload = {
        "referrer_account_id": referrer_account_id,
        "referred_account_id": referred_account_id,
        "referral_code": code,
        "status": "pending",
        "source": source,
        "signup_at": now_iso,
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    resp = _sb().table("referrals").insert(payload).execute()
    created = _first(resp)
    if created:
        return created

    again = get_pending_or_active_referral_for_referred_account(referred_account_id)
    if again:
        return again

    raise RuntimeError("Failed to create pending referral row.")


def bootstrap_new_account_for_referrals(
    *,
    account_id: str,
    incoming_referral_code: str | None = None,
    source: str = "signup",
) -> ReferralBootstrapResult:
    """
    Call this immediately after a new account row is created or after first account resolution.
    It will:
      1. ensure the new account has its own referral profile
      2. optionally capture the referral that brought the new user in
    """
    account_id = str(account_id or "").strip()
    if not account_id:
        raise ValueError("account_id is required")

    own_profile = ensure_referral_profile(account_id)

    incoming_referral_code = _clean_code(incoming_referral_code)
    if not incoming_referral_code:
        return ReferralBootstrapResult(
            account_id=account_id,
            own_profile=own_profile,
            captured_referral=None,
            skipped_reason="no_incoming_referral_code",
        )

    referrer_profile = get_referral_profile_by_code(incoming_referral_code)
    if not referrer_profile:
        return ReferralBootstrapResult(
            account_id=account_id,
            own_profile=own_profile,
            captured_referral=None,
            skipped_reason="invalid_referral_code",
        )

    referrer_account_id = str(referrer_profile.get("account_id") or "").strip()
    if not referrer_account_id:
        return ReferralBootstrapResult(
            account_id=account_id,
            own_profile=own_profile,
            captured_referral=None,
            skipped_reason="invalid_referrer_profile",
        )

    if referrer_account_id == account_id:
        return ReferralBootstrapResult(
            account_id=account_id,
            own_profile=own_profile,
            captured_referral=None,
            skipped_reason="self_referral_blocked",
        )

    captured = create_pending_referral(
        referrer_account_id=referrer_account_id,
        referred_account_id=account_id,
        referral_code=incoming_referral_code,
        source=source,
    )

    return ReferralBootstrapResult(
        account_id=account_id,
        own_profile=own_profile,
        captured_referral=captured,
        skipped_reason=None,
    )


# =========================================================
# REWARD CALCULATION
# =========================================================

def get_referral_reward_amount_for_plan(plan_code: str | None) -> Decimal:
    code = str(plan_code or "").strip().lower()
    if not code:
        return Decimal("0")

    # Exact map first
    if code in DEFAULT_PLAN_REWARD_MAP:
        return DEFAULT_PLAN_REWARD_MAP[code]

    # Friendly fallbacks
    if "month" in code:
        return DEFAULT_PLAN_REWARD_MAP["monthly"]
    if "quarter" in code:
        return DEFAULT_PLAN_REWARD_MAP["quarterly"]
    if "year" in code or "annual" in code:
        return DEFAULT_PLAN_REWARD_MAP["yearly"]

    fallback = os.getenv("REFERRAL_REWARD_DEFAULT") or "1000"
    return _to_decimal(fallback, Decimal("1000"))


# =========================================================
# QUALIFICATION / REWARD LEDGER
# =========================================================

def qualify_referral_after_successful_payment(
    *,
    paying_account_id: str,
    payment_reference: str,
    plan_code: str | None = None,
) -> Dict[str, Any]:
    """
    Call this only after a verified first successful subscription payment.
    Returns a structured result describing what happened.
    """
    paying_account_id = str(paying_account_id or "").strip()
    payment_reference = str(payment_reference or "").strip()

    if not paying_account_id:
        raise ValueError("paying_account_id is required")
    if not payment_reference:
        raise ValueError("payment_reference is required")

    referral = get_pending_or_active_referral_for_referred_account(paying_account_id)
    if not referral:
        return {
            "ok": True,
            "qualified": False,
            "reason": "no_referral_found",
        }

    referral_id = str(referral.get("id") or "").strip()
    current_status = str(referral.get("status") or "").strip().lower()

    existing_rewards = get_reward_rows_for_referral(referral_id)
    if existing_rewards:
        return {
            "ok": True,
            "qualified": False,
            "reason": "reward_already_exists",
            "referral_id": referral_id,
            "reward_rows": existing_rewards,
        }

    if current_status in {"disqualified", "expired"}:
        return {
            "ok": True,
            "qualified": False,
            "reason": f"referral_status_{current_status}",
            "referral_id": referral_id,
        }

    reward_amount = get_referral_reward_amount_for_plan(plan_code)
    if reward_amount <= 0:
        return {
            "ok": True,
            "qualified": False,
            "reason": "reward_amount_not_positive",
            "referral_id": referral_id,
        }

    referrer_account_id = str(referral.get("referrer_account_id") or "").strip()
    now_iso = _now_iso()

    # Step 1: move referral to qualified
    _sb().table("referrals").update(
        {
            "status": "qualified",
            "qualified_at": now_iso,
            "updated_at": now_iso,
        }
    ).eq("id", referral_id).execute()

    # Step 2: create reward row
    reward_payload = {
        "referral_id": referral_id,
        "account_id": referrer_account_id,
        "reward_type": "cash",
        "reward_amount": str(reward_amount),
        "currency": _reward_currency(),
        "status": "pending",
        "plan_code": plan_code,
        "payment_reference": payment_reference,
        "earned_at": now_iso,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    reward_resp = _sb().table("referral_rewards").insert(reward_payload).execute()
    reward_row = _first(reward_resp)

    # Step 3: optionally move referral to completed status
    target_status = _completed_status()
    if target_status in {"rewarded", "qualified"}:
        _sb().table("referrals").update(
            {
                "status": target_status,
                "updated_at": now_iso,
            }
        ).eq("id", referral_id).execute()

    final_referral = get_pending_or_active_referral_for_referred_account(paying_account_id)

    return {
        "ok": True,
        "qualified": True,
        "reason": "reward_created",
        "referral_id": referral_id,
        "reward": reward_row,
        "referral": final_referral,
    }


def disqualify_referral(
    *,
    referred_account_id: str,
    reason: str,
) -> Optional[Dict[str, Any]]:
    referred_account_id = str(referred_account_id or "").strip()
    reason = str(reason or "").strip() or "manual_disqualification"

    if not referred_account_id:
        raise ValueError("referred_account_id is required")

    referral = get_pending_or_active_referral_for_referred_account(referred_account_id)
    if not referral:
        return None

    now_iso = _now_iso()
    referral_id = str(referral.get("id") or "").strip()

    _sb().table("referrals").update(
        {
            "status": "disqualified",
            "disqualified_at": now_iso,
            "disqualify_reason": reason,
            "updated_at": now_iso,
        }
    ).eq("id", referral_id).execute()

    return get_pending_or_active_referral_for_referred_account(referred_account_id)


# =========================================================
# USER SUMMARY / HISTORY
# =========================================================

def get_referral_summary(account_id: str) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        raise ValueError("account_id is required")

    profile = ensure_referral_profile(account_id)

    referrals_resp = (
        _sb()
        .table("referrals")
        .select("*")
        .eq("referrer_account_id", account_id)
        .order("created_at", desc=True)
        .execute()
    )
    referrals = _response_data(referrals_resp)

    rewards_resp = (
        _sb()
        .table("referral_rewards")
        .select("*")
        .eq("account_id", account_id)
        .order("created_at", desc=True)
        .execute()
    )
    rewards = _response_data(rewards_resp)

    payouts_resp = (
        _sb()
        .table("referral_payouts")
        .select("*")
        .eq("account_id", account_id)
        .order("created_at", desc=True)
        .execute()
    )
    payouts = _response_data(payouts_resp)

    total_referrals = len(referrals)
    qualified_count = sum(1 for row in referrals if str(row.get("status") or "") in {"qualified", "rewarded"})
    pending_count = sum(1 for row in referrals if str(row.get("status") or "") == "pending")
    disqualified_count = sum(1 for row in referrals if str(row.get("status") or "") == "disqualified")

    pending_rewards = Decimal("0")
    approved_rewards = Decimal("0")
    paid_rewards = Decimal("0")
    reversed_rewards = Decimal("0")

    for row in rewards:
        amount = _to_decimal(row.get("reward_amount"))
        status = str(row.get("status") or "").strip().lower()
        if status == "pending":
            pending_rewards += amount
        elif status == "approved":
            approved_rewards += amount
        elif status == "paid":
            paid_rewards += amount
        elif status == "reversed":
            reversed_rewards += amount

    available_balance = pending_rewards + approved_rewards

    return {
        "profile": profile,
        "totals": {
            "total_referrals": total_referrals,
            "qualified_referrals": qualified_count,
            "pending_referrals": pending_count,
            "disqualified_referrals": disqualified_count,
            "pending_rewards": str(pending_rewards),
            "approved_rewards": str(approved_rewards),
            "paid_rewards": str(paid_rewards),
            "reversed_rewards": str(reversed_rewards),
            "available_balance": str(available_balance),
            "currency": _reward_currency(),
            "payout_count": len(payouts),
        },
        "recent_referrals": referrals[:20],
        "recent_rewards": rewards[:20],
        "recent_payouts": payouts[:20],
    }


def list_referrals_for_referrer(account_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return []

    limit = max(1, min(_safe_int(limit, 100), 500))
    resp = (
        _sb()
        .table("referrals")
        .select("*")
        .eq("referrer_account_id", account_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return _response_data(resp)


def list_rewards_for_account(account_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return []

    limit = max(1, min(_safe_int(limit, 100), 500))
    resp = (
        _sb()
        .table("referral_rewards")
        .select("*")
        .eq("account_id", account_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return _response_data(resp)


def list_payouts_for_account(account_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return []

    limit = max(1, min(_safe_int(limit, 100), 500))
    resp = (
        _sb()
        .table("referral_payouts")
        .select("*")
        .eq("account_id", account_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return _response_data(resp)
