from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from app.services.payout_service import (
    PayoutValidationError,
    get_payout_account,
    payout_eligibility,
    request_payout,
    upsert_payout_account,
)
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
ROUTE_VERSION = "referrals_route_v4_payout_alignment"


def _auth_account_id() -> tuple[Optional[str], Dict[str, Any]]:
    return get_account_id_from_request(request)


def _limit_arg(default: int = 50, minimum: int = 1, maximum: int = 500) -> int:
    raw = (request.args.get("limit") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return max(minimum, min(value, maximum))
    except Exception:
        return default


def _json_body() -> Dict[str, Any]:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_amount(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception as exc:
        raise PayoutValidationError("Amount must be a valid number.") from exc


@bp.get("/referrals/me")
def referral_me():
    account_id, debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    try:
        profile = ensure_referral_profile(account_id)
        summary = get_referral_summary(account_id)
        payout_balance = compute_approved_payout_balance(account_id)
        payout_account = get_payout_account(account_id)
        eligibility = payout_eligibility(account_id)

        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "account_id": account_id,
                "profile": profile,
                "summary": summary,
                "approved_payout_balance": str(payout_balance),
                "payout_account": payout_account,
                "payout_eligibility": eligibility,
                "debug": {"auth": debug},
            }
        ), 200
    except Exception as exc:
        logger.exception("[%s] referral_me failed account_id=%s", ROUTE_VERSION, account_id)
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "referral_me_failed",
                "root_cause": repr(exc),
                "fix": "Check referral_profiles table structure, payout account table, and summary logic.",
                "account_id": account_id,
                "debug": {"auth": debug},
            }
        ), 500


@bp.get("/referrals/history")
def referral_history():
    account_id, debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    try:
        rows = list_referrals_for_referrer(account_id, limit=_limit_arg())
        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "account_id": account_id,
                "count": len(rows),
                "rows": rows,
                "debug": {"auth": debug},
            }
        ), 200
    except Exception as exc:
        logger.exception("[%s] referral_history failed account_id=%s", ROUTE_VERSION, account_id)
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "referral_history_failed",
                "root_cause": repr(exc),
                "fix": "Check referrals table structure and list_referrals_for_referrer logic.",
                "account_id": account_id,
                "debug": {"auth": debug},
            }
        ), 500


@bp.get("/referrals/rewards")
def referral_rewards():
    account_id, debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    try:
        rows = list_rewards_for_account(account_id, limit=_limit_arg())
        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "account_id": account_id,
                "count": len(rows),
                "rows": rows,
                "debug": {"auth": debug},
            }
        ), 200
    except Exception as exc:
        logger.exception("[%s] referral_rewards failed account_id=%s", ROUTE_VERSION, account_id)
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "referral_rewards_failed",
                "root_cause": repr(exc),
                "fix": "Check referral_rewards table structure and list_rewards_for_account logic.",
                "account_id": account_id,
                "debug": {"auth": debug},
            }
        ), 500


@bp.get("/referrals/payouts")
def referral_payouts():
    account_id, debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    try:
        rows = list_payouts_for_account(account_id, limit=_limit_arg())
        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "account_id": account_id,
                "count": len(rows),
                "rows": rows,
                "debug": {"auth": debug},
            }
        ), 200
    except Exception as exc:
        logger.exception("[%s] referral_payouts failed account_id=%s", ROUTE_VERSION, account_id)
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "referral_payouts_failed",
                "root_cause": repr(exc),
                "fix": "Check referral_payouts table structure and list_payouts_for_account logic.",
                "account_id": account_id,
                "debug": {"auth": debug},
            }
        ), 500


@bp.get("/referrals/payout-account")
def referral_payout_account_get():
    account_id, debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    try:
        payout_account = get_payout_account(account_id)
        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "account_id": account_id,
                "payout_account": payout_account,
                "debug": {"auth": debug},
            }
        ), 200
    except Exception as exc:
        logger.exception("[%s] referral_payout_account_get failed account_id=%s", ROUTE_VERSION, account_id)
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "referral_payout_account_get_failed",
                "root_cause": repr(exc),
                "account_id": account_id,
                "debug": {"auth": debug},
            }
        ), 500


@bp.post("/referrals/payout-account")
def referral_payout_account_upsert():
    account_id, debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    body = _json_body()

    try:
        payout_account = upsert_payout_account(
            account_id=account_id,
            provider=_clean_text(body.get("provider")) or "paystack",
            bank_code=_clean_text(body.get("bank_code")),
            bank_name=_clean_text(body.get("bank_name")),
            account_name=_clean_text(body.get("account_name")),
            account_number=_clean_text(body.get("account_number")),
            account_number_masked=_clean_text(body.get("account_number_masked")),
            recipient_code=_clean_text(body.get("recipient_code")),
            currency=_clean_text(body.get("currency")),
            is_verified=bool(body.get("is_verified") is True),
            metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
        )
        eligibility = payout_eligibility(account_id)

        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "account_id": account_id,
                "payout_account": payout_account,
                "payout_eligibility": eligibility,
                "debug": {"auth": debug},
            }
        ), 200
    except PayoutValidationError as exc:
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "invalid_payout_account_payload",
                "root_cause": str(exc),
                "account_id": account_id,
                "debug": {"auth": debug, "body": body},
            }
        ), 400
    except ValueError as exc:
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "invalid_payout_account_payload",
                "root_cause": str(exc),
                "account_id": account_id,
                "debug": {"auth": debug, "body": body},
            }
        ), 400
    except Exception as exc:
        logger.exception("[%s] referral_payout_account_upsert failed account_id=%s", ROUTE_VERSION, account_id)
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "referral_payout_account_upsert_failed",
                "root_cause": repr(exc),
                "account_id": account_id,
                "debug": {"auth": debug, "body": body},
            }
        ), 500


@bp.get("/referrals/payout-eligibility")
def referral_payout_eligibility():
    account_id, debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    try:
        eligibility = payout_eligibility(account_id)
        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "account_id": account_id,
                "eligibility": eligibility,
                "debug": {"auth": debug},
            }
        ), 200
    except PayoutValidationError as exc:
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "invalid_payout_eligibility_request",
                "root_cause": str(exc),
                "account_id": account_id,
                "debug": {"auth": debug},
            }
        ), 400
    except Exception as exc:
        logger.exception("[%s] referral_payout_eligibility failed account_id=%s", ROUTE_VERSION, account_id)
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "referral_payout_eligibility_failed",
                "root_cause": repr(exc),
                "account_id": account_id,
                "debug": {"auth": debug},
            }
        ), 500


@bp.post("/referrals/payout-request")
def referral_payout_request():
    account_id, debug = _auth_account_id()
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    body = _json_body()

    try:
        result = request_payout(
            account_id=account_id,
            amount=_optional_amount(body.get("amount")),
            provider=_clean_text(body.get("provider")),
            provider_reference=_clean_text(body.get("provider_reference")),
            provider_transfer_code=_clean_text(body.get("provider_transfer_code")),
            metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
        )

        return jsonify(
            {
                "ok": True,
                "route_version": ROUTE_VERSION,
                "account_id": account_id,
                **result,
                "debug": {"auth": debug, "body": body},
            }
        ), 200
    except PayoutValidationError as exc:
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "invalid_payout_request",
                "root_cause": str(exc),
                "account_id": account_id,
                "debug": {"auth": debug, "body": body},
            }
        ), 400
    except ValueError as exc:
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "invalid_payout_request",
                "root_cause": str(exc),
                "account_id": account_id,
                "debug": {"auth": debug, "body": body},
            }
        ), 400
    except Exception as exc:
        logger.exception("[%s] referral_payout_request failed account_id=%s", ROUTE_VERSION, account_id)
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "referral_payout_request_failed",
                "root_cause": repr(exc),
                "account_id": account_id,
                "debug": {"auth": debug, "body": body},
            }
        ), 500
