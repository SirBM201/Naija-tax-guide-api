# app/routes/channel_payment_return.py
from __future__ import annotations

import logging
import re
from flask import Blueprint, request, jsonify, redirect

from app.services.outbound_service import send_whatsapp_text, send_telegram_text
from app.services.paystack_service import verify_transaction
from app.services.channel_subscription_service import activate_subscription, validate_plan_code
from app.services.channel_credit_service import add_credits_to_account

try:
    from app.services.promo_service import qualify_promo_after_successful_payment
except Exception:  # pragma: no cover
    qualify_promo_after_successful_payment = None  # type: ignore

logger = logging.getLogger(__name__)

bp = Blueprint("channel_payment_return", __name__)

CHANNEL_PAYMENT_RETURN_VERSION = "2026-05-31-batch36C-channel-return-promo-qualification"


def _clean(value):
    return str(value or "").strip()


def _clip(value, limit: int = 900) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _get_whatsapp_deeplink(phone_number: str) -> str:
    clean_number = re.sub(r"\D", "", phone_number or "")
    if not clean_number.startswith("234"):
        if clean_number.startswith("0"):
            clean_number = "234" + clean_number[1:]
        elif clean_number:
            clean_number = "234" + clean_number
    return f"https://wa.me/{clean_number}"


def _get_telegram_deeplink(username: str = "naija_tax_guide_bot") -> str:
    return f"https://t.me/{username}"


def _qualify_channel_promo_safely(account_id: str, reference: str, plan_code: str, tx_data: dict) -> dict:
    if qualify_promo_after_successful_payment is None:
        return {"ok": True, "qualified": False, "reason": "promo_service_unavailable"}
    if not account_id or not reference or not plan_code:
        return {"ok": True, "qualified": False, "reason": "missing_account_reference_or_plan"}
    try:
        data = tx_data.get("data") if isinstance(tx_data.get("data"), dict) else {}
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        result = qualify_promo_after_successful_payment(
            paying_account_id=account_id,
            payment_reference=reference,
            plan_code=plan_code,
            metadata={
                **metadata,
                "paid_at": data.get("paid_at") or data.get("created_at"),
                "amount_kobo": data.get("amount") or metadata.get("amount_kobo"),
                "gateway_response": data.get("gateway_response"),
                "source": "channel_payment_return_batch36C",
                "channel_payment_return_version": CHANNEL_PAYMENT_RETURN_VERSION,
            },
        )
        return result if isinstance(result, dict) else {"ok": True, "qualified": False, "raw": result}
    except Exception as exc:
        logger.exception("Channel payment return promo qualification failed")
        return {"ok": False, "qualified": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


@bp.route("/channel/payment/return", methods=["GET", "POST"])
def channel_payment_return():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        reference = _clean(body.get("reference"))
    else:
        reference = _clean(request.args.get("reference"))

    channel_type = _clean(request.args.get("channel_type") or request.form.get("channel_type"))
    provider_user_id = _clean(request.args.get("provider_user_id") or request.form.get("provider_user_id"))
    account_id = _clean(request.args.get("account_id") or request.form.get("account_id"))
    plan_code = _clean(request.args.get("plan_code") or request.form.get("plan_code"))
    trxref = _clean(request.args.get("trxref"))

    if not reference and trxref:
        reference = trxref

    if not reference:
        return jsonify({"ok": False, "error": "missing_reference", "route_version": CHANNEL_PAYMENT_RETURN_VERSION}), 400

    try:
        tx_data = verify_transaction(reference)
        data = tx_data.get("data", {}) if isinstance(tx_data, dict) else {}
        status = _clean(data.get("status"))
        amount = data.get("amount", 0) or 0
        if amount:
            amount = amount / 100

        promo_result = {"ok": True, "qualified": False, "reason": "not_subscription_or_not_success"}

        if status == "success":
            if plan_code:
                result = activate_subscription(account_id, plan_code, reference)
                plan = validate_plan_code(plan_code)
                promo_result = _qualify_channel_promo_safely(account_id, reference, plan_code, tx_data)

                promo_line = ""
                if promo_result.get("qualified") or promo_result.get("already_qualified"):
                    promo_line = "\n🎟️ Promo reward has also been recorded."
                elif promo_result.get("reason") == "no_promo_redemption":
                    promo_line = ""

                if result.get("ok"):
                    if plan:
                        plan_display = plan["full_name"]
                        credits = plan["credits"]
                        billing_display = {"monthly": "month", "quarterly": "3 months", "yearly": "year"}.get(plan["billing_cycle"], "month")
                        success_message = (
                            f"✅ *SUBSCRIPTION ACTIVATED!*\n\n"
                            f"📋 Plan: {plan_display}\n"
                            f"💰 Amount: ₦{amount:,.0f}\n"
                            f"🎯 Credits: {credits} AI credits per {billing_display}\n"
                            f"🆔 Reference: {reference}\n"
                            f"{promo_line}\n\n"
                            f"💡 Reply with 3 to check your plan status.\n"
                            f"💡 Reply with 7 for menu."
                        )
                    else:
                        plan_display = plan_code.replace("_", " ").title()
                        success_message = (
                            f"✅ *SUBSCRIPTION ACTIVATED!*\n\n"
                            f"📋 Plan: {plan_display}\n"
                            f"💰 Amount: ₦{amount:,.0f}\n"
                            f"🆔 Reference: {reference}\n"
                            f"{promo_line}\n\n"
                            f"💡 Reply with 3 to check your plan status.\n"
                            f"💡 Reply with 7 for menu."
                        )
                else:
                    success_message = (
                        f"⚠️ *PAYMENT RECEIVED - ACTIVATION PENDING*\n\n"
                        f"Reference: {reference}\n\n"
                        f"Your subscription will be activated shortly.\n"
                        f"Please reply with 3 to check status in a few minutes."
                    )
            else:
                success_message = (
                    f"✅ *PAYMENT SUCCESSFUL!*\n\n"
                    f"💰 Amount: ₦{amount:,.0f}\n"
                    f"🆔 Reference: {reference}\n\n"
                    f"Your AI credits have been added to your account.\n"
                    f"💡 Reply with 2 to check your balance.\n"
                    f"💡 Reply with 7 for menu."
                )

            if channel_type == "whatsapp" and provider_user_id:
                send_whatsapp_text(provider_user_id, success_message)
                send_whatsapp_text(provider_user_id, "Reply with 7 anytime to see the main menu.")
                return redirect(_get_whatsapp_deeplink(provider_user_id))
            if channel_type == "telegram" and provider_user_id:
                send_telegram_text(provider_user_id, success_message)
                return redirect(_get_telegram_deeplink())

            return jsonify({"ok": True, "route_version": CHANNEL_PAYMENT_RETURN_VERSION, "status": status, "reference": reference, "promo": promo_result}), 200

        error_message = f"❌ *PAYMENT NOT COMPLETED*\n\nReference: {reference}\nStatus: {status}\n\nPlease try again or contact support.\n\nReply with 4 to see plans or 6 to buy credits."
        if channel_type == "whatsapp" and provider_user_id:
            send_whatsapp_text(provider_user_id, error_message)
            return redirect(_get_whatsapp_deeplink(provider_user_id))
        if channel_type == "telegram" and provider_user_id:
            send_telegram_text(provider_user_id, error_message)
            return redirect(_get_telegram_deeplink())
        return jsonify({"ok": False, "error": f"payment_{status}", "route_version": CHANNEL_PAYMENT_RETURN_VERSION}), 400

    except Exception as exc:
        logger.exception("Payment verification error")
        error_message = f"❌ *PAYMENT VERIFICATION FAILED*\n\nReference: {reference}\nError: {str(exc)[:100]}\n\nPlease contact support with your reference number."
        if channel_type == "whatsapp" and provider_user_id:
            send_whatsapp_text(provider_user_id, error_message)
            return redirect(_get_whatsapp_deeplink(provider_user_id))
        if channel_type == "telegram" and provider_user_id:
            send_telegram_text(provider_user_id, error_message)
            return redirect(_get_telegram_deeplink())
        return jsonify({"ok": False, "error": str(exc), "route_version": CHANNEL_PAYMENT_RETURN_VERSION}), 500


@bp.route("/channel/payment/success")
def payment_success():
    phone = request.args.get("phone", "")
    return f"""
    <!DOCTYPE html><html><head><title>Payment Successful - Naija Tax Guide</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="font-family:Arial,sans-serif;text-align:center;padding:50px"><div style="color:green;font-size:48px">✅</div><h1>Payment Successful!</h1><p>Your payment has been processed successfully.<br>Your subscription/credits are now active.</p><a href="https://wa.me/{phone}">📱 Return to WhatsApp Chat</a> &nbsp; <a href="https://t.me/naija_tax_guide_bot">✈️ Return to Telegram Bot</a></body></html>
    """


@bp.route("/channel/payment/cancel")
def payment_cancel():
    phone = request.args.get("phone", "")
    return f"""
    <!DOCTYPE html><html><head><title>Payment Cancelled - Naija Tax Guide</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="font-family:Arial,sans-serif;text-align:center;padding:50px"><div style="color:orange;font-size:48px">⚠️</div><h1>Payment Cancelled</h1><p>You cancelled the payment process.</p><a href="https://wa.me/{phone}">📱 Return to WhatsApp Chat</a> &nbsp; <a href="https://t.me/naija_tax_guide_bot">✈️ Return to Telegram Bot</a></body></html>
    """
