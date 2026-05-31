from __future__ import annotations

import os
from html import escape
from typing import Any, Dict

from flask import Blueprint, Response, jsonify, request

from app.services.paystack_service import verify_transaction

bp = Blueprint("channel_payment_return", __name__)

CHANNEL_PAYMENT_RETURN_ROUTE_VERSION = "2026-05-31-batch36C2-channel-payment-return-get-fix"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _humanize_plan_code(plan_code: str) -> str:
    code = _lower(plan_code)
    if not code:
        return "Not available"
    return code.replace("_", " ").title()


def _telegram_bot_username() -> str:
    bot_username = _clean(
        request.args.get("bot")
        or request.form.get("bot")
        or (request.get_json(silent=True) or {}).get("bot")
        or os.getenv("TELEGRAM_BOT_USERNAME")
    )
    if bot_username.startswith("@"):
        bot_username = bot_username[1:]
    return bot_username


def _build_telegram_app_link() -> str:
    bot_username = _telegram_bot_username()
    if bot_username:
        return f"tg://resolve?domain={bot_username}"
    return "tg://resolve"


def _build_telegram_web_link() -> str:
    bot_username = _telegram_bot_username()
    if bot_username:
        return f"https://t.me/{bot_username}"
    return "https://t.me"


def _build_whatsapp_return_link(provider_user_id: str) -> str:
    phone = "".join(ch for ch in _clean(provider_user_id) if ch.isdigit())
    if phone:
        return f"https://wa.me/{phone}?text=Hi"
    return "https://wa.me/"


def _request_value(name: str, default: str = "") -> str:
    """
    Read from querystring first, then form/body JSON.
    Paystack returns to callback_url with GET, but this route also accepts POST
    for safety and manual testing.
    """
    if name in request.args:
        return _clean(request.args.get(name))
    if request.form and name in request.form:
        return _clean(request.form.get(name))
    body = request.get_json(silent=True) or {}
    if isinstance(body, dict):
        return _clean(body.get(name))
    return default


def _button_for_channel(channel_type: str, provider_user_id: str) -> tuple[str, str]:
    channel = _lower(channel_type)

    if channel == "telegram":
        return "Return to Telegram", _build_telegram_web_link()
    if channel == "whatsapp":
        return "Return to WhatsApp", _build_whatsapp_return_link(provider_user_id)

    return "Return to Naija Tax Guide", "https://www.naijataxguides.com/dashboard"


def _channel_button_html(channel_type: str, button_label: str, button_url: str) -> tuple[str, str]:
    channel = _lower(channel_type)

    if channel == "telegram":
        telegram_app_link = _build_telegram_app_link()
        telegram_web_link = button_url or _build_telegram_web_link()
        button_html = f"""
        <a
          class="btn"
          href="{escape(telegram_web_link)}"
          onclick="return openTelegramApp(event)"
        >
          {escape(button_label)}
        </a>
        """
        extra_script = f"""
        <script>
          function openTelegramApp(event) {{
            event.preventDefault();

            var appUrl = {telegram_app_link!r};
            var webUrl = {telegram_web_link!r};

            try {{
              window.location.href = appUrl;
              setTimeout(function() {{
                window.location.href = webUrl;
              }}, 900);
            }} catch (e) {{
              window.location.href = webUrl;
            }}

            return false;
          }}
        </script>
        """
        return button_html, extra_script

    return f'<a class="btn" href="{escape(button_url)}">{escape(button_label)}</a>', ""


def _render_page(
    *,
    title: str,
    message: str,
    badge: str,
    badge_class: str,
    button_label: str,
    button_url: str,
    reference: str,
    plan_code: str,
    status_text: str,
    channel_type: str,
    amount_text: str = "",
    promo_text: str = "",
) -> Response:
    pretty_plan = _humanize_plan_code(plan_code)
    button_html, extra_script = _channel_button_html(channel_type, button_label, button_url)

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: dark;
    }}
    body {{
      margin: 0;
      padding: 0;
      font-family: Arial, sans-serif;
      background: #0f172a;
      color: #e5e7eb;
    }}
    .wrap {{
      max-width: 680px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    .card {{
      background: #111827;
      border: 1px solid #1f2937;
      border-radius: 20px;
      padding: 24px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.25);
    }}
    .badge {{
      display: inline-block;
      padding: 8px 12px;
      border-radius: 999px;
      font-weight: 800;
      margin-bottom: 16px;
    }}
    .success {{
      background: #052e16;
      color: #86efac;
    }}
    .pending {{
      background: #3b2f0b;
      color: #fde68a;
    }}
    .error {{
      background: #3b0a0a;
      color: #fca5a5;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 28px;
      line-height: 1.2;
    }}
    p {{
      margin: 0 0 14px;
      font-size: 17px;
      line-height: 1.6;
      color: #d1d5db;
    }}
    .meta {{
      margin-top: 20px;
      padding: 16px;
      border-radius: 14px;
      background: #0b1220;
      border: 1px solid #1f2937;
      font-size: 15px;
      line-height: 1.7;
      overflow-wrap: anywhere;
    }}
    .btn {{
      display: inline-block;
      margin-top: 22px;
      padding: 14px 18px;
      border-radius: 12px;
      background: #4f46e5;
      color: white;
      text-decoration: none;
      font-weight: 800;
      font-size: 16px;
    }}
    .small {{
      margin-top: 16px;
      font-size: 14px;
      color: #9ca3af;
      line-height: 1.6;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="badge {escape(badge_class)}">{escape(badge)}</div>
      <h1>{escape(title)}</h1>
      <p>{escape(message)}</p>

      <div class="meta">
        <div><strong>Status:</strong> {escape(status_text or "unknown")}</div>
        <div><strong>Plan:</strong> {escape(pretty_plan)}</div>
        <div><strong>Reference:</strong> {escape(reference or "Not available")}</div>
        {f'<div><strong>Amount:</strong> {escape(amount_text)}</div>' if amount_text else ''}
        {f'<div><strong>Promo:</strong> {escape(promo_text)}</div>' if promo_text else ''}
      </div>

      {button_html}

      <div class="small">
        You can now return to your channel and continue using Naija Tax Guide.<br>
        Your channel may also receive an automatic confirmation message after webhook processing.
      </div>
    </div>
  </div>
  {extra_script}
</body>
</html>"""
    return Response(html, status=200, mimetype="text/html")


def _kobo_to_naira_text(value: Any) -> str:
    try:
        kobo = int(str(value or "0").replace(",", ""))
        if kobo <= 0:
            return ""
        return f"₦{(kobo / 100):,.0f}"
    except Exception:
        return ""


@bp.route("/channel/payment/return", methods=["GET", "POST"])
def channel_payment_return():
    """
    Channel-aware Paystack return page.

    Important:
    - This route is UX only.
    - Paystack webhook and /api/billing/verify remain the source of truth.
    - Paystack redirects browser callback_url using GET, so GET must be allowed.
    """
    reference = _request_value("reference") or _request_value("trxref")
    channel_type = _lower(_request_value("channel_type"))
    provider_user_id = _request_value("provider_user_id")
    plan_code = _request_value("plan_code")

    button_label, button_url = _button_for_channel(channel_type, provider_user_id)

    if not reference:
        return _render_page(
            title="Payment reference missing",
            message="We could not find the payment reference in the return URL.",
            badge="Missing reference",
            badge_class="error",
            button_label=button_label,
            button_url=button_url,
            reference="",
            plan_code=plan_code,
            status_text="missing_reference",
            channel_type=channel_type,
        )

    try:
        verified = verify_transaction(reference)
        tx = (verified or {}).get("data") or {}
        status_text = _lower(tx.get("status"))
        metadata = tx.get("metadata") or {}

        if not plan_code:
            plan_code = _clean(metadata.get("plan_code"))
        if not channel_type:
            channel_type = _lower(metadata.get("channel_type"))
        if not provider_user_id:
            provider_user_id = _clean(metadata.get("provider_user_id"))

        button_label, button_url = _button_for_channel(channel_type, provider_user_id)

        amount_text = _kobo_to_naira_text(
            metadata.get("final_amount_kobo")
            or metadata.get("amount_kobo")
            or tx.get("amount")
        )
        promo_code = _clean(metadata.get("promo_code"))
        promo_text = promo_code if promo_code else ""

        if status_text == "success":
            return _render_page(
                title="Payment successful",
                message=(
                    "Your payment was received successfully. Your subscription activation "
                    "and channel confirmation are being finalized."
                ),
                badge="Payment processed",
                badge_class="success",
                button_label=button_label,
                button_url=button_url,
                reference=reference,
                plan_code=plan_code,
                status_text=status_text,
                channel_type=channel_type,
                amount_text=amount_text,
                promo_text=promo_text,
            )

        return _render_page(
            title="Payment not completed",
            message=(
                "The payment has not returned with a successful status yet. "
                "If you completed payment, wait a moment and check your channel confirmation."
            ),
            badge="Verification pending",
            badge_class="pending",
            button_label=button_label,
            button_url=button_url,
            reference=reference,
            plan_code=plan_code,
            status_text=status_text or "pending",
            channel_type=channel_type,
            amount_text=amount_text,
            promo_text=promo_text,
        )

    except Exception as exc:
        return _render_page(
            title="Payment verification pending",
            message=(
                "Your payment is being checked. If payment was completed successfully, "
                "your channel confirmation message should arrive shortly."
            ),
            badge="Verification pending",
            badge_class="pending",
            button_label=button_label,
            button_url=button_url,
            reference=reference,
            plan_code=plan_code,
            status_text=f"verify_error: {type(exc).__name__}",
            channel_type=channel_type,
        )


@bp.get("/channel/payment/return/health")
def channel_payment_return_health():
    return jsonify(
        {
            "ok": True,
            "route_version": CHANNEL_PAYMENT_RETURN_ROUTE_VERSION,
            "message": "Channel payment return GET/POST route is active.",
            "methods": ["GET", "POST"],
            "example": "/api/channel/payment/return?reference=NTG-xxx&channel_type=whatsapp",
        }
    ), 200
