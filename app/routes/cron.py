from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request

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
from app.services.referral_service import mature_pending_rewards

bp = Blueprint("cron", __name__)


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def _cron_authorized() -> bool:
    secret = _cron_secret()
    if not secret:
        return False
    incoming = (request.headers.get("X-Cron-Secret") or request.args.get("cron_secret") or "").strip()
    return bool(incoming) and incoming == secret


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


def _is_payout_window_today() -> bool:
    return _now().day in _payout_days()


def _parse_json() -> Dict[str, Any]:
    try:
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


@bp.post("/cron/referrals/mature")
def cron_referrals_mature():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = _parse_json()
    reward_ids = body.get("reward_ids") or request.args.getlist("reward_id") or []
    if not isinstance(reward_ids, list):
        reward_ids = [reward_ids]

    account_id = (body.get("account_id") or request.args.get("account_id") or "").strip() or None
    limit = _safe_int(body.get("limit") or request.args.get("limit") or 2000, 2000)
    force = _truthy(str(body.get("force") if "force" in body else request.args.get("force")))

    result = mature_pending_rewards(
        account_id=account_id,
        reward_ids=reward_ids,
        limit=limit,
        force=force,
    )
    return jsonify({"ok": True, "result": result}), 200


@bp.post("/cron/referrals/payout-batch")
def cron_referrals_payout_batch():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    if not payout_enabled():
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

    from app.core.supabase_client import supabase
    sb = supabase() if callable(supabase) else supabase
    resp = (
        sb.table("referral_rewards")
        .select("account_id")
        .eq("status", "approved")
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    seen = set()
    account_ids: List[str] = []
    for row in rows:
        aid = str(row.get("account_id") or "").strip()
        if aid and aid not in seen:
            seen.add(aid)
            account_ids.append(aid)

    prepared: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    minimum = min_payout_amount()

    for account_id in account_ids:
        payout_account = get_payout_account(account_id)
        if not payout_account or not bool(payout_account.get("is_verified")):
            skipped.append({"account_id": account_id, "reason": "missing_verified_payout_account"})
            continue

        existing = get_pending_or_processing_payout(account_id)
        if existing:
            skipped.append({"account_id": account_id, "reason": "existing_pending_or_processing_payout", "payout": existing})
            continue

        amount = approved_balance_for_account(account_id)
        if amount < minimum:
            skipped.append({
                "account_id": account_id,
                "reason": "below_minimum_payout_amount",
                "amount": str(amount),
                "minimum": str(minimum),
            })
            continue

        payout = create_payout_row(
            account_id=account_id,
            amount=_to_decimal(amount),
            currency=payout_currency(),
            provider=payout_provider(),
            status="pending",
        )
        prepared.append({
            "account_id": account_id,
            "amount": str(amount),
            "payout": payout,
        })

    return jsonify({
        "ok": True,
        "payout_provider": payout_provider(),
        "currency": payout_currency(),
        "minimum_payout_amount": str(minimum),
        "prepared_count": len(prepared),
        "skipped_count": len(skipped),
        "prepared": prepared,
        "skipped": skipped,
    }), 200
