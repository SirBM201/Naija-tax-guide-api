from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request

from app.core.supabase_client import get_supabase_client
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
ROUTE_VERSION = "cron_route_v2_user_payout_flow"


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


def _unique_account_ids_from_rewards(status: str = "approved") -> List[str]:
    sb = get_supabase_client(admin=True)
    resp = (
        sb.table("referral_rewards")
        .select("account_id")
        .eq("status", status)
        .execute()
    )
    rows = resp.data or []

    seen = set()
    account_ids: List[str] = []
    for row in rows:
        aid = str(row.get("account_id") or "").strip()
        if aid and aid not in seen:
            seen.add(aid)
            account_ids.append(aid)
    return account_ids


def _pick_account_ids(body: Dict[str, Any]) -> List[str]:
    raw = body.get("account_ids")
    if raw is None:
        raw = request.args.getlist("account_id")

    if raw is None:
        single = (body.get("account_id") or request.args.get("account_id") or "").strip()
        return [single] if single else []

    values = raw if isinstance(raw, list) else [raw]

    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


@bp.post("/cron/referrals/mature")
def cron_referrals_mature():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized", "route_version": ROUTE_VERSION}), 401

    body = _parse_json()
    reward_ids = body.get("reward_ids") or request.args.getlist("reward_id") or []
    if not isinstance(reward_ids, list):
        reward_ids = [reward_ids]

    account_id = (body.get("account_id") or request.args.get("account_id") or "").strip() or None
    limit = _safe_int(body.get("limit") or request.args.get("limit") or 2000, 2000)
    force = _truthy(str(body.get("force") if "force" in body else request.args.get("force")))

    try:
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
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "cron_referrals_mature_failed",
                "root_cause": repr(exc),
                "debug": {
                    "account_id": account_id,
                    "reward_ids": reward_ids,
                    "limit": limit,
                    "force": force,
                },
            }
        ), 500


@bp.post("/cron/referrals/payout-batch")
def cron_referrals_payout_batch():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized", "route_version": ROUTE_VERSION}), 401

    body = _parse_json()
    force = _truthy(str(body.get("force") if "force" in body else request.args.get("force")))
    limit = _safe_int(body.get("limit") or request.args.get("limit") or 5000, 5000)
    requested_account_ids = _pick_account_ids(body)

    if not payout_enabled():
        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "skipped": True,
                "reason": "payout_disabled",
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

        if requested_account_ids:
            account_ids = requested_account_ids
        else:
            account_ids = _unique_account_ids_from_rewards(status="approved")

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
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "cron_referrals_payout_batch_failed",
                "root_cause": repr(exc),
                "debug": {
                    "force": force,
                    "limit": limit,
                    "requested_account_ids": requested_account_ids,
                },
            }
        ), 500
