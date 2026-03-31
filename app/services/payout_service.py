from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.core.supabase_client import supabase


def _sb():
    return supabase() if callable(supabase) else supabase


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


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


def _rows(resp: Any) -> List[Dict[str, Any]]:
    if resp is None:
        return []
    data = getattr(resp, "data", None)
    return data if isinstance(data, list) else []


def _first(resp: Any) -> Optional[Dict[str, Any]]:
    rows = _rows(resp)
    return rows[0] if rows else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _sum_reward_amount(rows: List[Dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for row in rows:
        total += _to_decimal(row.get("reward_amount"))
    return total


def payout_enabled() -> bool:
    return _truthy(os.getenv("REFERRAL_PAYOUT_ENABLED") or "1")


def payout_provider() -> str:
    return (os.getenv("REFERRAL_PAYOUT_PROVIDER") or "paystack").strip().lower()


def payout_currency() -> str:
    return (os.getenv("REFERRAL_REWARD_CURRENCY") or "NGN").strip().upper()


def min_payout_amount() -> Decimal:
    return _to_decimal(os.getenv("REFERRAL_MIN_PAYOUT_AMOUNT") or "2000", Decimal("2000"))


def payout_auto_release() -> bool:
    return _truthy(os.getenv("REFERRAL_PAYOUT_AUTO_RELEASE") or "0")


def get_payout_account(account_id: str) -> Optional[Dict[str, Any]]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return None

    resp = (
        _sb()
        .table("referral_payout_accounts")
        .select("*")
        .eq("account_id", account_id)
        .limit(1)
        .execute()
    )
    return _first(resp)


def upsert_payout_account(
    *,
    account_id: str,
    provider: str = "paystack",
    bank_code: str | None = None,
    bank_name: str | None = None,
    account_name: str | None = None,
    account_number_masked: str | None = None,
    recipient_code: str | None = None,
    currency: str | None = None,
    is_verified: bool = False,
) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        raise ValueError("account_id is required")

    payload = {
        "account_id": account_id,
        "provider": (provider or payout_provider()).strip().lower(),
        "bank_code": bank_code,
        "bank_name": bank_name,
        "account_name": account_name,
        "account_number_masked": account_number_masked,
        "recipient_code": recipient_code,
        "currency": (currency or payout_currency()).strip().upper(),
        "is_verified": bool(is_verified),
        "updated_at": _now_iso(),
    }

    existing = get_payout_account(account_id)
    if existing:
        resp = (
            _sb()
            .table("referral_payout_accounts")
            .update(payload)
            .eq("account_id", account_id)
            .execute()
        )
        row = _first(resp)
        if row:
            return row
        again = get_payout_account(account_id)
        if again:
            return again
        raise RuntimeError("Failed to update payout account")

    payload["created_at"] = _now_iso()
    resp = _sb().table("referral_payout_accounts").insert(payload).execute()
    row = _first(resp)
    if row:
        return row

    again = get_payout_account(account_id)
    if again:
        return again

    raise RuntimeError("Failed to create payout account")


def list_payout_rows_for_account(account_id: str, limit: int = 100) -> List[Dict[str, Any]]:
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
    return _rows(resp)


def list_payout_queue(statuses: Optional[List[str]] = None, limit: int = 200) -> List[Dict[str, Any]]:
    limit = max(1, min(_safe_int(limit, 200), 1000))
    query = _sb().table("referral_payouts").select("*").order("created_at", desc=False).limit(limit)
    statuses = [str(s or "").strip().lower() for s in (statuses or []) if str(s or "").strip()]
    if len(statuses) == 1:
        query = query.eq("status", statuses[0])
    elif len(statuses) > 1:
        query = query.in_("status", statuses)
    resp = query.execute()
    return _rows(resp)


def get_payout_row(payout_id: str) -> Optional[Dict[str, Any]]:
    payout_id = str(payout_id or "").strip()
    if not payout_id:
        return None
    resp = _sb().table("referral_payouts").select("*").eq("id", payout_id).limit(1).execute()
    return _first(resp)


def get_pending_or_processing_payout(account_id: str) -> Optional[Dict[str, Any]]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return None

    for status in ("pending", "processing"):
        resp = (
            _sb()
            .table("referral_payouts")
            .select("*")
            .eq("account_id", account_id)
            .eq("status", status)
            .limit(1)
            .execute()
        )
        row = _first(resp)
        if row:
            return row
    return None


def create_payout_row(
    *,
    account_id: str,
    amount: Decimal,
    currency: str | None = None,
    provider: str | None = None,
    provider_reference: str | None = None,
    provider_transfer_code: str | None = None,
    status: str = "pending",
) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        raise ValueError("account_id is required")
    if amount <= 0:
        raise ValueError("amount must be greater than zero")

    now_iso = _now_iso()
    payload = {
        "account_id": account_id,
        "amount": str(amount),
        "currency": (currency or payout_currency()).strip().upper(),
        "provider": (provider or payout_provider()).strip().lower(),
        "provider_reference": provider_reference,
        "provider_transfer_code": provider_transfer_code,
        "status": status,
        "requested_at": now_iso,
        "processed_at": None,
        "paid_at": None,
        "failed_at": None,
        "failure_reason": None,
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    resp = _sb().table("referral_payouts").insert(payload).execute()
    row = _first(resp)
    if row:
        return row
    raise RuntimeError("Failed to create payout row")


def update_payout_status(
    *,
    payout_id: str,
    status: str,
    provider_reference: str | None = None,
    provider_transfer_code: str | None = None,
    failure_reason: str | None = None,
) -> Optional[Dict[str, Any]]:
    payout_id = str(payout_id or "").strip()
    if not payout_id:
        raise ValueError("payout_id is required")

    current = get_payout_row(payout_id)
    if not current:
        raise ValueError("payout not found")

    now_iso = _now_iso()
    patch: Dict[str, Any] = {
        "status": status,
        "provider_reference": provider_reference if provider_reference is not None else current.get("provider_reference"),
        "provider_transfer_code": provider_transfer_code if provider_transfer_code is not None else current.get("provider_transfer_code"),
        "updated_at": now_iso,
    }

    if status == "processing":
        patch["processed_at"] = now_iso
        patch["failed_at"] = None
        patch["failure_reason"] = None
    elif status == "paid":
        patch["processed_at"] = current.get("processed_at") or now_iso
        patch["paid_at"] = current.get("paid_at") or now_iso
        patch["failed_at"] = None
        patch["failure_reason"] = None
    elif status == "failed":
        patch["failed_at"] = now_iso
        patch["failure_reason"] = failure_reason or current.get("failure_reason") or "admin_marked_failed"
    else:
        patch["failure_reason"] = failure_reason

    resp = _sb().table("referral_payouts").update(patch).eq("id", payout_id).execute()
    return _first(resp)


def list_approved_rewards_for_account(account_id: str) -> List[Dict[str, Any]]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return []

    resp = (
        _sb()
        .table("referral_rewards")
        .select("*")
        .eq("account_id", account_id)
        .eq("status", "approved")
        .order("created_at", desc=False)
        .execute()
    )
    return _rows(resp)


def list_paid_rewards_for_account(account_id: str) -> List[Dict[str, Any]]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return []

    resp = (
        _sb()
        .table("referral_rewards")
        .select("*")
        .eq("account_id", account_id)
        .eq("status", "paid")
        .order("created_at", desc=False)
        .execute()
    )
    return _rows(resp)


def approved_balance_for_account(account_id: str) -> Decimal:
    return _sum_reward_amount(list_approved_rewards_for_account(account_id))


def payout_eligibility(account_id: str) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        raise ValueError("account_id is required")

    if not payout_enabled():
        return {"ok": True, "eligible": False, "reason": "payout_disabled"}

    payout_account = get_payout_account(account_id)
    if not payout_account:
        return {"ok": True, "eligible": False, "reason": "missing_payout_account"}

    if not bool(payout_account.get("is_verified")):
        return {
            "ok": True,
            "eligible": False,
            "reason": "payout_account_not_verified",
            "payout_account": payout_account,
        }

    pending_or_processing = get_pending_or_processing_payout(account_id)
    if pending_or_processing:
        return {
            "ok": True,
            "eligible": False,
            "reason": "existing_pending_or_processing_payout",
            "payout": pending_or_processing,
        }

    balance = approved_balance_for_account(account_id)
    minimum = min_payout_amount()

    if balance < minimum:
        return {
            "ok": True,
            "eligible": False,
            "reason": "below_minimum_payout_amount",
            "approved_balance": str(balance),
            "minimum_required": str(minimum),
        }

    return {
        "ok": True,
        "eligible": True,
        "approved_balance": str(balance),
        "minimum_required": str(minimum),
        "currency": payout_currency(),
        "payout_account": payout_account,
    }


def request_payout(account_id: str) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        raise ValueError("account_id is required")

    eligibility = payout_eligibility(account_id)
    if not eligibility.get("eligible"):
        return {"ok": True, "requested": False, "eligibility": eligibility}

    balance = _to_decimal(eligibility.get("approved_balance"), Decimal("0"))
    payout_row = create_payout_row(
        account_id=account_id,
        amount=balance,
        currency=payout_currency(),
        provider=payout_provider(),
        status="pending",
    )

    return {"ok": True, "requested": True, "eligibility": eligibility, "payout": payout_row}


def _select_rewards_for_settlement(account_id: str, target_amount: Decimal) -> List[Dict[str, Any]]:
    rows = list_approved_rewards_for_account(account_id)
    selected: List[Dict[str, Any]] = []
    running = Decimal("0")
    for row in rows:
        selected.append(row)
        running += _to_decimal(row.get("reward_amount"))
        if running >= target_amount:
            break
    return selected


def _mark_reward_rows_paid(reward_rows: List[Dict[str, Any]]) -> int:
    count = 0
    now_iso = _now_iso()
    for row in reward_rows:
        reward_id = str(row.get("id") or "").strip()
        if not reward_id:
            continue
        resp = (
            _sb()
            .table("referral_rewards")
            .update({"status": "paid", "paid_at": now_iso, "updated_at": now_iso})
            .eq("id", reward_id)
            .eq("status", "approved")
            .execute()
        )
        if _first(resp):
            count += 1
    return count


def admin_mark_payout_processing(
    payout_id: str,
    *,
    provider_reference: str | None = None,
    provider_transfer_code: str | None = None,
) -> Dict[str, Any]:
    payout = get_payout_row(payout_id)
    if not payout:
        raise ValueError("payout not found")

    status = str(payout.get("status") or "").strip().lower()
    if status == "paid" or payout.get("paid_at"):
        raise ValueError("paid payouts cannot be moved back to processing")

    if status not in {"pending", "failed", "processing"}:
        raise ValueError("payout is not processable")

    updated = update_payout_status(
        payout_id=payout_id,
        status="processing",
        provider_reference=provider_reference,
        provider_transfer_code=provider_transfer_code,
        failure_reason=None,
    )
    return {"ok": True, "payout": updated or get_payout_row(payout_id)}


def admin_mark_payout_paid(
    payout_id: str,
    *,
    provider_reference: str | None = None,
    provider_transfer_code: str | None = None,
) -> Dict[str, Any]:
    payout = get_payout_row(payout_id)
    if not payout:
        raise ValueError("payout not found")

    status = str(payout.get("status") or "").strip().lower()
    if status == "paid" or payout.get("paid_at"):
        raise ValueError("payout is already settled as paid")

    if status not in {"pending", "processing"}:
        raise ValueError("only pending or processing payouts can be marked paid")

    account_id = str(payout.get("account_id") or "").strip()
    amount = _to_decimal(payout.get("amount"))
    if not account_id or amount <= 0:
        raise ValueError("invalid payout row")

    selected_rewards = _select_rewards_for_settlement(account_id, amount)
    selected_total = _sum_reward_amount(selected_rewards)

    if selected_total < amount:
        paid_total = _sum_reward_amount(list_paid_rewards_for_account(account_id))
        if paid_total >= amount:
            raise ValueError("underlying reward rows are already settled as paid")
        raise ValueError("approved rewards are below payout amount")

    paid_count = _mark_reward_rows_paid(selected_rewards)
    updated = update_payout_status(
        payout_id=payout_id,
        status="paid",
        provider_reference=provider_reference,
        provider_transfer_code=provider_transfer_code,
        failure_reason=None,
    )

    return {
        "ok": True,
        "payout": updated or get_payout_row(payout_id),
        "reward_rows_marked_paid": paid_count,
        "reward_amount_total": str(selected_total),
    }


def admin_mark_payout_failed(
    payout_id: str,
    *,
    failure_reason: str | None = None,
    provider_reference: str | None = None,
    provider_transfer_code: str | None = None,
) -> Dict[str, Any]:
    payout = get_payout_row(payout_id)
    if not payout:
        raise ValueError("payout not found")

    status = str(payout.get("status") or "").strip().lower()
    if status == "paid" or payout.get("paid_at"):
        raise ValueError("paid payouts cannot be marked failed")

    updated = update_payout_status(
        payout_id=payout_id,
        status="failed",
        provider_reference=provider_reference,
        provider_transfer_code=provider_transfer_code,
        failure_reason=(failure_reason or "admin_marked_failed").strip(),
    )
    return {"ok": True, "payout": updated or get_payout_row(payout_id)}
