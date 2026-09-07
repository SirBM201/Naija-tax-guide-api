# app/routes/paystack_webhook.py
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.paystack_service import verify_webhook_signature, verify_transaction
from app.services.channel_subscription_service import activate_subscription
from app.services.channel_credit_service import add_credits_to_account
from app.services.outbound_service import send_whatsapp_text, send_telegram_text

try:
    from app.services.promo_service import qualify_promo_after_successful_payment
except Exception:  # pragma: no cover
    qualify_promo_after_successful_payment = None  # type: ignore

logger = logging.getLogger(__name__)
bp = Blueprint("paystack_webhook", __name__)
PAYSTACK_WEBHOOK_ROUTE_VERSION = "2026-09-07-v1-03b-charge-success-only"


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _clip(value: Any, limit: int = 900) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _normalize_metadata(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        raw = str(value).replace(",", "").strip()
        return int(Decimal(raw)) if raw else default
    except Exception:
        return default


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value is None:
            return default
        if isinstance(value, Decimal):
            return value
        raw = str(value).replace(",", "").strip()
        return Decimal(raw) if raw else default
    except (InvalidOperation, ValueError, TypeError):
        return default


def _decimal_to_money(value: Any) -> str:
    amount = _to_decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    try:
        return f"{int(amount):,}"
    except Exception:
        return "0"


def _amount_ngn_from_payload(metadata: Dict[str, Any], data: Dict[str, Any]) -> Decimal:
    for key in ("final_amount_ngn", "amount_ngn", "paid_amount_ngn", "original_amount_ngn"):
        if metadata.get(key) not in (None, ""):
            return _to_decimal(metadata.get(key))
    for key in ("final_amount_kobo", "amount_kobo"):
        if metadata.get(key) not in (None, ""):
            return _to_decimal(metadata.get(key)) / Decimal("100")
    if data.get("amount") not in (None, ""):
        return _to_decimal(data.get("amount")) / Decimal("100")
    return Decimal("0")


def _send_channel_notification(channel_type: str, provider_user_id: str, message: str) -> Dict[str, Any]:
    try:
        if channel_type == "whatsapp" and provider_user_id:
            send_whatsapp_text(provider_user_id, message)
            return {"ok": True, "channel": "whatsapp"}
        if channel_type == "telegram" and provider_user_id:
            send_telegram_text(provider_user_id, message)
            return {"ok": True, "channel": "telegram"}
        return {"ok": True, "sent": False, "reason": "no_channel_or_provider_user_id"}
    except Exception as exc:
        logger.error("Channel payment notification failed: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


def _transaction_row(reference: str) -> Optional[Dict[str, Any]]:
    if not reference:
        return None
    try:
        res = _sb().table("paystack_transactions").select("*").eq("reference", reference).limit(1).execute()
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None
    except Exception:
        return None


def _expected_amount_kobo(row: Dict[str, Any]) -> int:
    return _to_int(row.get("amount") or row.get("amount_kobo"), 0)


def _verify_payment(reference: str, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    if not reference:
        return {"ok": False, "error": "missing_reference"}
    tx = _transaction_row(reference)
    if not tx:
        return {"ok": False, "error": "unknown_payment_reference", "reference": reference}
    try:
        verified = verify_transaction(reference)
    except Exception as exc:
        return {"ok": False, "error": "paystack_verification_failed", "root_cause": f"{type(exc).__name__}: {_clip(exc)}"}
    verified_data = verified.get("data") if isinstance(verified, dict) and isinstance(verified.get("data"), dict) else {}
    if _lower(verified_data.get("status")) != "success":
        return {"ok": False, "error": "payment_not_successful", "paystack_status": verified_data.get("status")}
    if _clean(verified_data.get("reference")) != reference:
        return {"ok": False, "error": "reference_mismatch"}
    expected_amount = _expected_amount_kobo(tx)
    verified_amount = _to_int(verified_data.get("amount"), 0)
    if expected_amount <= 0 or verified_amount != expected_amount:
        return {"ok": False, "error": "amount_mismatch", "expected_amount_kobo": expected_amount, "verified_amount_kobo": verified_amount}
    expected_currency = _clean(tx.get("currency") or "NGN").upper()
    verified_currency = _clean(verified_data.get("currency") or "NGN").upper()
    if expected_currency != verified_currency:
        return {"ok": False, "error": "currency_mismatch", "expected_currency": expected_currency, "verified_currency": verified_currency}
    stored_metadata = _normalize_metadata(tx.get("metadata"))
    verified_metadata = _normalize_metadata(verified_data.get("metadata"))
    return {"ok": True, "transaction": tx, "data": verified_data, "metadata": {**verified_metadata, **stored_metadata}}


def _already_fulfilled(tx: Dict[str, Any]) -> bool:
    return _lower(tx.get("status")) in {"success", "fulfilled", "completed"}


def _update_transaction(reference: str, *, status: str, paystack_status: str, metadata_patch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not reference:
        return {"ok": False, "updated": False, "reason": "missing_reference"}
    try:
        current = _transaction_row(reference) or {}
        metadata = _normalize_metadata(current.get("metadata"))
        if metadata_patch:
            metadata = {**metadata, **metadata_patch}
        payload: Dict[str, Any] = {"status": status, "paystack_status": paystack_status, "updated_at": datetime.now(timezone.utc).isoformat()}
        if metadata:
            payload["metadata"] = metadata
        resp = _sb().table("paystack_transactions").update(payload).eq("reference", reference).execute()
        return {"ok": True, "updated": True, "data": getattr(resp, "data", None)}
    except Exception as exc:
        logger.error("Transaction update failed: %s", exc)
        return {"ok": False, "updated": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


def _qualify_promo_safely(*, account_id: str, reference: str, plan_code: str, metadata: Dict[str, Any], paystack_data: Dict[str, Any]) -> Dict[str, Any]:
    if qualify_promo_after_successful_payment is None:
        return {"ok": True, "qualified": False, "reason": "promo_service_unavailable"}
    if not account_id or not reference or not plan_code:
        return {"ok": True, "qualified": False, "reason": "missing_account_reference_or_plan"}
    try:
        result = qualify_promo_after_successful_payment(paying_account_id=account_id, payment_reference=reference, plan_code=plan_code, metadata={**metadata, "paid_at": paystack_data.get("paid_at") or paystack_data.get("created_at"), "amount_kobo": paystack_data.get("amount"), "gateway_response": paystack_data.get("gateway_response"), "source": "verified_paystack_webhook", "paystack_webhook_route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION})
        return result if isinstance(result, dict) else {"ok": True, "qualified": False, "raw": result}
    except Exception as exc:
        logger.exception("Promo qualification failed")
        return {"ok": False, "qualified": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


@bp.get("/paystack/webhook/health")
def paystack_webhook_health():
    return jsonify({"ok": True, "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION, "expected_webhook_url": "/api/paystack/webhook", "signature_verification": "required", "server_side_transaction_verification": "required", "idempotency": "transaction_reference", "value_fulfillment_event": "charge.success"}), 200


@bp.post("/paystack/webhook")
def paystack_webhook():
    raw = request.get_data() or b""
    signature = _clean(request.headers.get("x-paystack-signature"))
    if not verify_webhook_signature(raw, signature):
        logger.warning("Rejected Paystack webhook with invalid signature")
        return jsonify({"ok": False, "error": "invalid_signature", "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION}), 401

    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    event_type = _clean(payload.get("event"))
    webhook_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    # Paystack documents charge.success as the successful transaction event for
    # both initial plan transactions and subsequent successful subscription
    # billing cycles. Lifecycle/invoice events are acknowledged but never
    # fulfill customer value here, preventing two event families from applying
    # one economic transaction.
    if event_type != "charge.success":
        return jsonify({"ok": True, "ignored": True, "reason": "non_charge_success_event", "event": event_type, "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION}), 200

    reference = _clean(webhook_data.get("reference"))
    status = _lower(webhook_data.get("status"))
    if status != "success":
        return jsonify({"ok": True, "ignored": True, "reason": "status_not_success", "status": status, "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION}), 200

    verification = _verify_payment(reference, webhook_data)
    if not verification.get("ok"):
        logger.error("Rejected unverified Paystack fulfillment reference=%s result=%s", reference, verification)
        return jsonify({"ok": False, "error": "payment_verification_failed", "reference": reference, "verification": verification, "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION}), 400

    tx = verification.get("transaction") or {}
    verified_data = verification.get("data") or {}
    metadata = verification.get("metadata") or {}
    if _already_fulfilled(tx):
        return jsonify({"ok": True, "processed": False, "duplicate": True, "reason": "reference_already_fulfilled", "reference": reference, "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION}), 200

    account_id = _clean(tx.get("account_id") or metadata.get("account_id"))
    plan_code = _clean(tx.get("plan_code") or metadata.get("plan_code"))
    credits = _to_int(metadata.get("credits"), 0)
    transaction_type = _lower(metadata.get("type") or ("subscription" if plan_code and not plan_code.startswith("credits_") else "credit_purchase"))
    channel_type = _lower(metadata.get("channel_type"))
    provider_user_id = _clean(metadata.get("provider_user_id"))
    amount_ngn_decimal = _amount_ngn_from_payload(metadata, verified_data)
    amount_ngn_display = _decimal_to_money(amount_ngn_decimal)
    if not account_id:
        return jsonify({"ok": False, "error": "missing_account_id", "reference": reference, "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION}), 400

    activation_result: Dict[str, Any]
    notification_result: Dict[str, Any] = {}
    promo_result: Dict[str, Any] = {}
    try:
        if transaction_type == "credit_purchase" and credits > 0:
            success = add_credits_to_account(account_id, credits, reference)
            activation_result = {"ok": bool(success), "type": "credit_purchase", "credits": credits}
            if success:
                notification_result = _send_channel_notification(channel_type, provider_user_id, f"✅ *{credits} CREDITS ADDED!*\n\nYour verified payment of ₦{amount_ngn_display} has been confirmed.\n\n💡 Reply with 2 to check your balance.\n💡 Reply with 7 for menu.")
        elif transaction_type == "subscription" or (plan_code and not plan_code.startswith("credits_")):
            activation_result = activate_subscription(account_id, plan_code, reference)
            if activation_result.get("ok"):
                notification_result = _send_channel_notification(channel_type, provider_user_id, f"✅ *SUBSCRIPTION ACTIVATED!*\n\n📋 Plan: {(plan_code or 'subscription').replace('_', ' ').title()}\n💰 Amount: ₦{amount_ngn_display}\n🆔 Reference: {reference}\n\n✨ Your paid plan is now active.\n💡 Reply with 3 to check your plan status.\n💡 Reply with 7 for menu.")
                promo_result = _qualify_promo_safely(account_id=account_id, reference=reference, plan_code=plan_code, metadata=metadata, paystack_data=verified_data)
        else:
            activation_result = {"ok": False, "error": "unknown_transaction_type", "transaction_type": transaction_type}
    except Exception as exc:
        logger.exception("Verified Paystack fulfillment failed")
        return jsonify({"ok": False, "error": "webhook_processing_failed", "reference": reference, "root_cause": f"{type(exc).__name__}: {_clip(exc)}", "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION}), 500

    if not activation_result.get("ok"):
        _update_transaction(reference, status="verified_unfulfilled", paystack_status="success", metadata_patch={"fulfillment_error": activation_result.get("error"), "verified_at": datetime.now(timezone.utc).isoformat()})
        return jsonify({"ok": False, "error": "payment_verified_but_fulfillment_failed", "reference": reference, "activation": activation_result, "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION}), 500

    tx_update = _update_transaction(reference, status="success", paystack_status="success", metadata_patch={"verified_at": datetime.now(timezone.utc).isoformat(), "fulfilled_at": datetime.now(timezone.utc).isoformat(), "fulfillment_event": "charge.success", "fulfillment_version": PAYSTACK_WEBHOOK_ROUTE_VERSION})
    return jsonify({"ok": True, "processed": True, "reference": reference, "status": "success", "account_id": account_id, "plan_code": plan_code or None, "transaction_type": transaction_type, "amount_ngn": str(amount_ngn_decimal), "activation": activation_result, "promo": promo_result or None, "notification": notification_result or None, "transaction_update": tx_update, "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION}), 200
