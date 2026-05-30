# app/routes/paystack_webhook.py
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.paystack_service import verify_webhook_signature
from app.services.channel_subscription_service import activate_subscription
from app.services.channel_credit_service import add_credits_to_account
from app.services.outbound_service import send_whatsapp_text, send_telegram_text

try:
    from app.services.promo_service import qualify_promo_after_successful_payment
except Exception:  # pragma: no cover
    qualify_promo_after_successful_payment = None  # type: ignore


logger = logging.getLogger(__name__)

bp = Blueprint("paystack_webhook", __name__)

PAYSTACK_WEBHOOK_ROUTE_VERSION = "2026-05-30-batch35B1-amount-formatting-safe-webhook"


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _clip(value: Any, limit: int = 900) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        raw = str(value).replace(",", "").strip()
        if raw == "":
            return default
        return int(Decimal(raw))
    except Exception:
        return default


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value is None:
            return default
        if isinstance(value, Decimal):
            return value
        raw = str(value).replace(",", "").strip()
        if raw == "":
            return default
        return Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        return default


def _decimal_to_money(value: Any) -> str:
    amount = _to_decimal(value, Decimal("0")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    try:
        return f"{int(amount):,}"
    except Exception:
        return "0"


def _amount_ngn_from_payload(metadata: Dict[str, Any], data: Dict[str, Any]) -> Decimal:
    """
    Paystack sends data.amount in kobo.
    Our app metadata may store amount_ngn/final_amount_ngn as string or number.
    Always return a Decimal NGN value so f-string comma formatting never crashes.
    """
    for key in ("final_amount_ngn", "amount_ngn", "paid_amount_ngn", "original_amount_ngn"):
        if metadata.get(key) not in (None, ""):
            return _to_decimal(metadata.get(key), Decimal("0"))

    for key in ("final_amount_kobo", "amount_kobo"):
        if metadata.get(key) not in (None, ""):
            return (_to_decimal(metadata.get(key), Decimal("0")) / Decimal("100"))

    if data.get("amount") not in (None, ""):
        return (_to_decimal(data.get("amount"), Decimal("0")) / Decimal("100"))

    return Decimal("0")


def _send_channel_notification(channel_type: str, provider_user_id: str, message: str) -> Dict[str, Any]:
    """
    Send notification to user's channel.

    This function must never crash the webhook.
    Paystack expects a 200 response once we have processed or safely ignored the event.
    """
    try:
        if channel_type == "whatsapp" and provider_user_id:
            send_whatsapp_text(provider_user_id, message)
            return {"ok": True, "channel": "whatsapp"}
        if channel_type == "telegram" and provider_user_id:
            send_telegram_text(provider_user_id, message)
            return {"ok": True, "channel": "telegram"}
        return {"ok": True, "sent": False, "reason": "no_channel_or_provider_user_id"}
    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {_clip(e)}"}


def _qualify_promo_safely(
    *,
    account_id: str,
    reference: str,
    plan_code: str,
    metadata: Dict[str, Any],
    paystack_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Batch 35B1 safety:
    /api/paystack/webhook is the configured Paystack webhook URL in production.
    Therefore this legacy route should also call promo qualification idempotently.
    The promo service already prevents duplicate reward rows by payment_reference.
    """
    if qualify_promo_after_successful_payment is None:
        return {"ok": True, "qualified": False, "reason": "promo_service_unavailable"}

    if not account_id or not reference or not plan_code:
        return {"ok": True, "qualified": False, "reason": "missing_account_reference_or_plan"}

    try:
        result = qualify_promo_after_successful_payment(  # type: ignore[misc]
            paying_account_id=account_id,
            payment_reference=reference,
            plan_code=plan_code,
            metadata={
                **(metadata or {}),
                "paid_at": paystack_data.get("paid_at") or paystack_data.get("created_at"),
                "amount_kobo": paystack_data.get("amount") or metadata.get("amount_kobo"),
                "gateway_response": paystack_data.get("gateway_response"),
                "source": "paystack_webhook_batch35B1",
                "paystack_webhook_route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION,
            },
        )
        return result if isinstance(result, dict) else {"ok": True, "qualified": False, "raw": result}
    except Exception as exc:
        logger.exception("Promo qualification from /api/paystack/webhook failed")
        return {"ok": False, "qualified": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


def _update_transaction_success(reference: str, status: str) -> Dict[str, Any]:
    if not reference:
        return {"ok": False, "updated": False, "reason": "missing_reference"}

    try:
        resp = (
            _sb()
            .table("paystack_transactions")
            .update(
                {
                    "status": "success",
                    "paystack_status": status,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("reference", reference)
            .execute()
        )
        return {"ok": True, "updated": True, "data": getattr(resp, "data", None)}
    except Exception as e:
        logger.error(f"Error updating transaction: {e}")
        return {"ok": False, "updated": False, "error": f"{type(e).__name__}: {_clip(e)}"}


@bp.get("/paystack/webhook/health")
def paystack_webhook_health():
    return jsonify(
        {
            "ok": True,
            "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION,
            "message": "Paystack webhook route is active and amount formatting is safe.",
            "fixed_error": "ValueError: Cannot specify ',' with 's'",
            "expected_webhook_url": "/api/paystack/webhook",
        }
    ), 200


@bp.post("/paystack/webhook")
def paystack_webhook():
    raw = request.get_data() or b""
    sig = _clean(request.headers.get("x-paystack-signature"))

    # Existing production behavior retained.
    # Signature verification remains optional here because your current route had it commented out.
    # To enforce later, uncomment the next three lines after confirming Paystack secret setup.
    # if not verify_webhook_signature(raw, sig):
    #     return jsonify({"ok": False, "error": "invalid_signature"}), 401

    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    event_type = _clean(payload.get("event"))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    reference = _clean(data.get("reference"))
    status = _lower(data.get("status"))
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

    logger.info(f"Paystack webhook: event={event_type}, reference={reference}, status={status}")

    # Only process successful charge events
    if event_type not in ["charge.success", "subscription.create", "invoice.payment_succeeded"]:
        return jsonify(
            {
                "ok": True,
                "ignored": True,
                "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION,
                "reason": "event_type_not_processed",
                "event": event_type,
            }
        ), 200

    if status != "success":
        return jsonify(
            {
                "ok": True,
                "ignored": True,
                "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION,
                "reason": "status_not_success",
                "status": status,
            }
        ), 200

    # Extract metadata safely.
    account_id = _clean(metadata.get("account_id"))
    plan_code = _clean(metadata.get("plan_code"))
    credits = _to_int(metadata.get("credits", 0), 0)
    transaction_type = _lower(metadata.get("type", "credit_purchase"))
    channel_type = _lower(metadata.get("channel_type"))
    provider_user_id = _clean(metadata.get("provider_user_id"))
    amount_ngn_decimal = _amount_ngn_from_payload(metadata, data)
    amount_ngn_display = _decimal_to_money(amount_ngn_decimal)

    if not account_id:
        logger.error(f"Missing account_id for reference: {reference}")
        return jsonify(
            {
                "ok": False,
                "error": "missing_account_id",
                "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION,
                "reference": reference,
            }
        ), 400

    activation_result: Dict[str, Any] = {}
    notification_result: Dict[str, Any] = {}
    promo_result: Dict[str, Any] = {}

    # Process based on transaction type.
    try:
        if transaction_type == "credit_purchase" and credits > 0:
            success = add_credits_to_account(account_id, credits, reference)
            activation_result = {"ok": bool(success), "type": "credit_purchase", "credits": credits}

            if success:
                message = (
                    f"✅ *{credits} CREDITS ADDED!*\n\n"
                    f"Your payment of ₦{amount_ngn_display} for {credits} AI credits has been confirmed.\n\n"
                    "💡 Reply with 2 to check your balance.\n"
                    "💡 Reply with 7 for menu."
                )
                notification_result = _send_channel_notification(channel_type, provider_user_id, message)
                logger.info(f"Added {credits} credits to {account_id}")
            else:
                logger.error(f"Failed to add credits to {account_id}")

        elif transaction_type == "subscription" or plan_code:
            result = activate_subscription(account_id, plan_code, reference)
            activation_result = result if isinstance(result, dict) else {"ok": bool(result)}

            if activation_result.get("ok"):
                plan_display = (plan_code or "subscription").replace("_", " ").title()

                message = (
                    "✅ *SUBSCRIPTION ACTIVATED!*\n\n"
                    f"📋 Plan: {plan_display}\n"
                    f"💰 Amount: ₦{amount_ngn_display}\n"
                    f"🆔 Reference: {reference}\n\n"
                    "✨ Your paid plan is now active.\n"
                    "💡 Reply with 3 to check your plan status.\n"
                    "💡 Reply with 7 for menu."
                )

                notification_result = _send_channel_notification(channel_type, provider_user_id, message)
                promo_result = _qualify_promo_safely(
                    account_id=account_id,
                    reference=reference,
                    plan_code=plan_code,
                    metadata=metadata,
                    paystack_data=data,
                )
                logger.info(f"Subscription activated for {account_id}: {plan_code}")
            else:
                logger.error(f"Failed to activate subscription for {account_id}: {activation_result.get('error')}")
                message = (
                    "❌ *SUBSCRIPTION ACTIVATION FAILED*\n\n"
                    "Your payment was received but we could not activate your subscription.\n\n"
                    f"Please contact support with reference: {reference}"
                )
                notification_result = _send_channel_notification(channel_type, provider_user_id, message)

        else:
            activation_result = {
                "ok": True,
                "processed": False,
                "reason": "unknown_transaction_type",
                "transaction_type": transaction_type,
            }

    except Exception as exc:
        logger.exception("Paystack webhook processing failed after payload parsing")
        return jsonify(
            {
                "ok": False,
                "error": "webhook_processing_failed",
                "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION,
                "reference": reference,
                "root_cause": f"{type(exc).__name__}: {_clip(exc)}",
            }
        ), 500

    tx_update = _update_transaction_success(reference, status)

    return jsonify(
        {
            "ok": True,
            "processed": True,
            "route_version": PAYSTACK_WEBHOOK_ROUTE_VERSION,
            "event": event_type,
            "reference": reference,
            "status": status,
            "account_id": account_id,
            "plan_code": plan_code or None,
            "transaction_type": transaction_type,
            "amount_ngn": str(amount_ngn_decimal),
            "activation": activation_result,
            "promo": promo_result or None,
            "notification": notification_result or None,
            "transaction_update": tx_update,
        }
    ), 200
