from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request

from app.services.payout_service import prepare_scheduled_payouts, payout_days
from app.services.referral_service import mature_pending_rewards

bp = Blueprint("cron", __name__)


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cron_secret() -> str:
    return (os.getenv("CRON_SECRET") or os.getenv("ADMIN_CRON_SECRET") or "").strip()


def _cron_authorized() -> bool:
    secret = _cron_secret()
    if not secret:
        return False
    incoming = (request.headers.get("X-Cron-Secret") or request.args.get("cron_secret") or "").strip()
    return bool(incoming) and incoming == secret


def _is_payout_window_today() -> bool:
    return _now().day in payout_days()


def _parse_limit(default: int = 5000) -> int:
    raw = (request.args.get("limit") or "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
        return max(1, min(n, 10000))
    except Exception:
        return default


def _dry_run() -> bool:
    return _truthy(request.args.get("dry_run") or request.headers.get("X-Dry-Run"))


@bp.post("/cron/referrals/mature")
def cron_referrals_mature():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    result = mature_pending_rewards(limit=_parse_limit(5000))
    return jsonify({"ok": True, "result": result}), 200


@bp.post("/cron/referrals/payout-batch")
def cron_referrals_payout_batch():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    matured = mature_pending_rewards(limit=_parse_limit(5000))

    if not _is_payout_window_today():
        return jsonify(
            {
                "ok": True,
                "skipped": True,
                "reason": "not_payout_window_today",
                "allowed_days": payout_days(),
                "today": _now().day,
                "matured": matured,
            }
        ), 200

    if _dry_run():
        preview = prepare_scheduled_payouts()
        preview["dry_run"] = True
        preview["notes"] = (
            "Dry run only. No payout transfer should be sent from this endpoint. "
            "This batch only prepares pending payout rows for the 15th/30th window."
        )
        return jsonify(preview), 200

    result = prepare_scheduled_payouts()
    result["matured"] = matured
    result["notes"] = (
        "Pending payout rows were prepared for the current payout window. "
        "Rewards were not marked paid here. They should only be marked paid after confirmed payout processing."
    )
    return jsonify(result), 200
