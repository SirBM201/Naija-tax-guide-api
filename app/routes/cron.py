from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.referral_service import mature_pending_rewards

bp = Blueprint("cron", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


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


def _cron_secret() -> str:
    return (os.getenv("CRON_SECRET") or os.getenv("ADMIN_CRON_SECRET") or "").strip()


def _payout_enabled() -> bool:
    return _truthy(os.getenv("REFERRAL_PAYOUT_ENABLED") or "1")


def _payout_provider() -> str:
    return (os.getenv("REFERRAL_PAYOUT_PROVIDER") or "paystack").strip().lower()


def _min_payout_amount() -> Decimal:
    return _to_decimal(os.getenv("REFERRAL_MIN_PAYOUT_AMOUNT") or "2000", Decimal("2000"))


def _payout_currency() -> str:
    return (os.getenv("REFERRAL_REWARD_CURRENCY") or "NGN").strip().upper()


def _payout_days() -> List[int]:
    raw = (os.getenv("REFERRAL_PAYOUT_DAYS") or "15,30").strip()
    out: List[int] = []
    for part in raw.split(","):
        try:
            day = int(part.strip())
            if 1 <= day <= 31:
                out.append(day)
        except Exception:
            continue
    return out or [15, 30]


def _cron_authorized() -> bool:
    secret = _cron_secret()
    if not secret:
        return False
    incoming = (request.headers.get("X-Cron-Secret") or request.args.get("cron_secret") or "").strip()
    return bool(incoming) and incoming == secret


def _approved_rewards_for_account(account_id: str) -> List[Dict[str, Any]]:
    resp = (
        _sb()
        .table("referral_rewards")
        .select("*")
        .eq("account_id", account_id)
        .eq("status", "approved")
        .order("created_at", desc=False)
        .execute()
    )
    return getattr(resp, "data", None) or []


def _payout_account_for_user(account_id: str) -> Dict[str, Any] | None:
    resp = (
        _sb()
        .table("referral_payout_accounts")
        .select("*")
        .eq("account_id", account_id)
        .eq("is_verified", True)
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def _all_accounts_with_approved_rewards() -> List[str]:
    resp = (
        _sb()
        .table("referral_rewards")
        .select("account_id")
        .eq("status", "approved")
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    seen: set[str] = set()
    account_ids: List[str] = []
    for row in rows:
        account_id = str(row.get("account_id") or "").strip()
        if account_id and account_id not in seen:
            seen.add(account_id)
            account_ids.append(account_id)
    return account_ids


def _sum_rewards(rows: List[Dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for row in rows:
        total += _to_decimal(row.get("reward_amount"))
    return total


def _create_pending_payout(account_id: str, amount: Decimal) -> Dict[str, Any] | None:
    payload = {
        "account_id": account_id,
        "amount": str(amount),
        "currency": _payout_currency(),
        "provider": _payout_provider(),
        "status": "pending",
        "requested_at": _now_iso(),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    resp = _sb().table("referral_payouts").insert(payload).execute()
    rows = getattr(resp, "data", None) or []
    return rows[0] if rows else None


def _mark_rewards_paid(reward_rows: List[Dict[str, Any]]) -> None:
    for row in reward_rows:
        reward_id = str(row.get("id") or "").strip()
        if not reward_id:
            continue
        _sb().table("referral_rewards").update(
            {
                "status": "paid",
                "paid_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        ).eq("id", reward_id).execute()


def _mark_payout_processing(payout_id: str) -> None:
    _sb().table("referral_payouts").update(
        {
            "status": "processing",
            "processed_at": _now_iso(),
            "updated_at": _now_iso(),
        }
    ).eq("id", payout_id).execute()


def _is_payout_window_today() -> bool:
    today_day = _now().day
    return today_day in _payout_days()


@bp.post("/cron/referrals/mature")
def cron_referrals_mature():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    result = mature_pending_rewards(limit=2000)
    return jsonify({"ok": True, "result": result}), 200


@bp.post("/cron/referrals/payout-batch")
def cron_referrals_payout_batch():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    if not _payout_enabled():
        return jsonify({"ok": True, "skipped": True, "reason": "payout_disabled"}), 200

    mature_pending_rewards(limit=5000)

    if not _is_payout_window_today():
        return jsonify(
            {
                "ok": True,
                "skipped": True,
                "reason": "not_payout_window_today",
                "allowed_days": _payout_days(),
                "today": _now().day,
            }
        ), 200

    min_amount = _min_payout_amount()
    account_ids = _all_accounts_with_approved_rewards()

    prepared: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for account_id in account_ids:
        payout_account = _payout_account_for_user(account_id)
        if not payout_account:
            skipped.append({"account_id": account_id, "reason": "missing_verified_payout_account"})
            continue

        reward_rows = _approved_rewards_for_account(account_id)
        if not reward_rows:
            skipped.append({"account_id": account_id, "reason": "no_approved_rewards"})
            continue

        total = _sum_rewards(reward_rows)
        if total < min_amount:
            skipped.append(
                {
                    "account_id": account_id,
                    "reason": "below_minimum_payout_amount",
                    "amount": str(total),
                    "minimum": str(min_amount),
                }
            )
            continue

        payout_row = _create_pending_payout(account_id, total)
        if not payout_row:
            skipped.append({"account_id": account_id, "reason": "failed_to_create_payout_row"})
            continue

        payout_id = str(payout_row.get("id") or "").strip()
        if payout_id:
            _mark_payout_processing(payout_id)

        _mark_rewards_paid(reward_rows)

        prepared.append(
            {
                "account_id": account_id,
                "payout": payout_row,
                "reward_count": len(reward_rows),
                "amount": str(total),
            }
        )

    return jsonify(
        {
            "ok": True,
            "payout_provider": _payout_provider(),
            "currency": _payout_currency(),
            "minimum_payout_amount": str(min_amount),
            "prepared_count": len(prepared),
            "skipped_count": len(skipped),
            "prepared": prepared,
            "skipped": skipped,
        }
    ), 200
