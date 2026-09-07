from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request, session

from app.services.payout_service import (
    PayoutValidationError,
    get_payout_account,
    payout_eligibility,
    request_payout,
    upsert_payout_account,
)
from app.services.payout_window_service import enforce_payout_window, payout_window_status
from app.services.referral_service import (
    compute_approved_payout_balance,
    ensure_referral_profile,
    get_referral_summary,
    list_payouts_for_account,
    list_referrals_for_referrer,
    list_rewards_for_account,
)
from app.services.web_auth_service import get_account_id_from_request

bp = Blueprint("referrals", __name__)
logger = logging.getLogger(__name__)
ROUTE_VERSION = "2026-09-07-v1-08-payout-window-integrity"


def _auth_account_id() -> tuple[Optional[str], Dict[str, Any]]:
    user_id = session.get("user_id")
    if user_id:
        return user_id, {"auth_method": "session"}
    account_id, debug = get_account_id_from_request(request)
    if account_id:
        return account_id, debug
    return None, {"auth_method": "failed"}


def _limit_arg(default: int = 50, minimum: int = 1, maximum: int = 500) -> int:
    try:
        return max(minimum, min(int((request.args.get("limit") or default)), maximum))
    except Exception:
        return default


def _json_body() -> Dict[str, Any]:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _clean_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _optional_amount(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception as exc:
        raise PayoutValidationError("Amount must be a valid number.") from exc


def _unauthorized(debug):
    return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401


@bp.get("/referrals/me")
def referral_me():
    account_id, debug = _auth_account_id()
    if not account_id:
        return _unauthorized(debug)
    try:
        profile = ensure_referral_profile(account_id)
        summary = get_referral_summary(account_id)
        payout_balance = compute_approved_payout_balance(account_id)
        payout_account = get_payout_account(account_id)
        eligibility = payout_eligibility(account_id)
        return jsonify({"ok": True, "route_version": ROUTE_VERSION, "account_id": account_id, "referral_code": profile.get("referral_code"), "referral_link": profile.get("referral_link"), "profile": profile, "summary": summary, "total_referrals": (summary or {}).get("total_referrals", 0), "total_earned": (summary or {}).get("total_earned", 0), "approved_payout_balance": str(payout_balance), "payout_account": payout_account, "payout_eligibility": eligibility, "payout_window": payout_window_status(), "eligible": (eligibility or {}).get("eligible", False), "minimum_amount": (eligibility or {}).get("minimum_amount", 0), "current_balance": float(payout_balance or 0), "debug": {"auth": debug}}), 200
    except Exception as exc:
        logger.exception("referral_me failed account_id=%s", account_id)
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "referral_me_failed", "root_cause": repr(exc), "account_id": account_id}), 500


@bp.get("/referrals/history")
def referral_history():
    account_id, debug = _auth_account_id()
    if not account_id:
        return _unauthorized(debug)
    try:
        rows = list_referrals_for_referrer(account_id, limit=_limit_arg())
        return jsonify({"ok": True, "route_version": ROUTE_VERSION, "account_id": account_id, "count": len(rows), "rows": rows}), 200
    except Exception as exc:
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "referral_history_failed", "root_cause": repr(exc)}), 500


@bp.get("/referrals/rewards")
def referral_rewards():
    account_id, debug = _auth_account_id()
    if not account_id:
        return _unauthorized(debug)
    try:
        rows = list_rewards_for_account(account_id, limit=_limit_arg())
        return jsonify({"ok": True, "route_version": ROUTE_VERSION, "account_id": account_id, "count": len(rows), "rows": rows}), 200
    except Exception as exc:
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "referral_rewards_failed", "root_cause": repr(exc)}), 500


@bp.get("/referrals/payouts")
def referral_payouts():
    account_id, debug = _auth_account_id()
    if not account_id:
        return _unauthorized(debug)
    try:
        rows = list_payouts_for_account(account_id, limit=_limit_arg())
        return jsonify({"ok": True, "route_version": ROUTE_VERSION, "account_id": account_id, "count": len(rows), "rows": rows, "payout_window": payout_window_status()}), 200
    except Exception as exc:
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "referral_payouts_failed", "root_cause": repr(exc)}), 500


@bp.get("/referrals/payout-account")
def referral_payout_account_get():
    account_id, debug = _auth_account_id()
    if not account_id:
        return _unauthorized(debug)
    try:
        payout_account = get_payout_account(account_id)
        return jsonify({"ok": True, "route_version": ROUTE_VERSION, "account_id": account_id, "payout_account": payout_account, "has_account": payout_account is not None}), 200
    except Exception as exc:
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "referral_payout_account_get_failed", "root_cause": repr(exc)}), 500


@bp.post("/referrals/payout-account")
def referral_payout_account_upsert():
    account_id, debug = _auth_account_id()
    if not account_id:
        return _unauthorized(debug)
    body = _json_body()
    try:
        payout_account = upsert_payout_account(account_id=account_id, provider=_clean_text(body.get("provider")) or "paystack", bank_code=_clean_text(body.get("bank_code")), bank_name=_clean_text(body.get("bank_name")), account_name=_clean_text(body.get("account_name")), account_number=_clean_text(body.get("account_number")), account_number_masked=_clean_text(body.get("account_number_masked")), recipient_code=_clean_text(body.get("recipient_code")), currency=_clean_text(body.get("currency")), is_verified=bool(body.get("is_verified") is True))
        return jsonify({"ok": True, "route_version": ROUTE_VERSION, "account_id": account_id, "payout_account": payout_account, "payout_eligibility": payout_eligibility(account_id)}), 200
    except (PayoutValidationError, ValueError) as exc:
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "invalid_payout_account_payload", "root_cause": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "referral_payout_account_upsert_failed", "root_cause": repr(exc)}), 500


@bp.get("/referrals/payout-eligibility")
def referral_payout_eligibility():
    account_id, debug = _auth_account_id()
    if not account_id:
        return _unauthorized(debug)
    try:
        eligibility = payout_eligibility(account_id)
        window = payout_window_status()
        eligibility["payout_window"] = window
        eligibility["eligible_now"] = bool(eligibility.get("eligible")) and bool(window.get("open"))
        return jsonify({"ok": True, "route_version": ROUTE_VERSION, "account_id": account_id, "eligible": eligibility.get("eligible_now", False), "minimum_amount": eligibility.get("minimum_amount", 0), "current_balance": eligibility.get("available_amount", 0), "eligibility": eligibility}), 200
    except (PayoutValidationError, ValueError) as exc:
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "invalid_payout_eligibility_request", "root_cause": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "referral_payout_eligibility_failed", "root_cause": repr(exc)}), 500


@bp.post("/referrals/payout-request")
def referral_payout_request():
    account_id, debug = _auth_account_id()
    if not account_id:
        return _unauthorized(debug)
    body = _json_body()
    try:
        window = enforce_payout_window()
        result = request_payout(account_id=account_id, amount=_optional_amount(body.get("amount")), provider=_clean_text(body.get("provider")), provider_reference=_clean_text(body.get("provider_reference")), provider_transfer_code=_clean_text(body.get("provider_transfer_code")))
        return jsonify({"ok": True, "route_version": ROUTE_VERSION, "account_id": account_id, "payout_window": window, **result}), 200
    except (PayoutValidationError, ValueError) as exc:
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "invalid_payout_request", "root_cause": str(exc), "payout_window": payout_window_status(), "account_id": account_id}), 400
    except Exception as exc:
        logger.exception("referral_payout_request failed account_id=%s", account_id)
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "referral_payout_request_failed", "root_cause": repr(exc), "account_id": account_id}), 500
