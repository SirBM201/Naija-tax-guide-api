# app/services/whatsapp_notify.py
from __future__ import annotations

import os
from typing import Any, Dict

import requests


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalize_phone(value: Any) -> str:
    digits = "".join(ch for ch in _clean(value) if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def send_whatsapp_text(to: str, body: str) -> Dict[str, Any]:
    to = _normalize_phone(to)
    body = _clean(body)

    access_token = _clean(os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("META_WHATSAPP_TOKEN"))
    phone_number_id = _clean(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or os.getenv("META_WHATSAPP_PHONE_NUMBER_ID"))

    if not to or not body:
        return {"ok": False, "error": "missing_to_or_body"}

    if not access_token or not phone_number_id:
        return {
            "ok": False,
            "error": "whatsapp_send_not_configured",
            "missing": {
                "WHATSAPP_ACCESS_TOKEN": not bool(access_token),
                "WHATSAPP_PHONE_NUMBER_ID": not bool(phone_number_id),
            },
        }

    url = f"https://graph.facebook.com/v20.0/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body[:3900]},
    }

    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        result: Dict[str, Any] = {"ok": response.status_code < 400, "status_code": response.status_code}
        try:
            result["response"] = response.json()
        except Exception:
            result["response_text"] = response.text[:700]
        return result
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:700]}"}


def build_whatsapp_payment_success_message(
    *,
    payment_type: str,
    plan_name: str = "",
    topup_name: str = "",
    credits_added: int = 0,
    current_balance: int = 0,
    reference: str = "",
) -> str:
    if payment_type == "topup":
        return (
            "✅ Top-up successful.\n\n"
            f"Add-on: {topup_name or 'Usage Credit Add-on'}\n"
            f"Credits added: {credits_added}\n"
            f"Current balance: {current_balance}\n"
            f"Reference: {reference}\n\n"
            "Reply 2 to check credits or 0 for main menu."
        )

    return (
        "✅ Payment successful.\n\n"
        f"Plan activated: {plan_name or 'Your selected plan'}\n"
        f"Included Usage Credits: {credits_added}\n"
        f"Current balance: {current_balance}\n"
        f"Reference: {reference}\n\n"
        "Reply 2 to check credits or 0 for main menu."
    )
