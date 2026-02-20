# app/services/email_service.py
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Optional


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _bool_env(name: str, default: str = "false") -> bool:
    v = _env(name, default).lower()
    return v in ("1", "true", "yes", "on")


SMTP_HOST = _env("SMTP_HOST", "")
SMTP_PORT = int(_env("SMTP_PORT", "587") or "587")
SMTP_USER = _env("SMTP_USER", "")
SMTP_PASS = _env("SMTP_PASS", "")
SMTP_FROM = _env("SMTP_FROM", SMTP_USER)  # fallback to SMTP_USER
SMTP_USE_TLS = _bool_env("SMTP_USE_TLS", "true")


def smtp_is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_PORT and SMTP_FROM)


def send_email_otp(*, to_email: str, otp: str, purpose: str, ttl_minutes: int) -> Optional[str]:
    """
    Sends OTP email via SMTP.
    Returns None on success, or error string on failure.
    """
    if not smtp_is_configured():
        return "smtp_not_configured"

    to_email = (to_email or "").strip()
    if not to_email or "@" not in to_email:
        return "invalid_email"

    subject = "Your NaijaTax Guide verification code"

    text_body = (
        f"Your verification code is: {otp}\n\n"
        f"Purpose: {purpose}\n"
        f"This code expires in {ttl_minutes} minutes.\n\n"
        f"If you did not request this code, you can ignore this message."
    )

    html_body = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.5;">
      <h2 style="margin:0 0 10px 0;">NaijaTax Guide</h2>
      <p>Your verification code is:</p>
      <p style="font-size:22px; font-weight:bold; letter-spacing:2px;">{otp}</p>
      <p><b>Purpose:</b> {purpose}<br/>
         <b>Expires in:</b> {ttl_minutes} minutes</p>
      <p style="color:#666;">If you did not request this code, ignore this email.</p>
    </div>
    """

    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            if SMTP_USE_TLS:
                server.starttls()
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        return None
    except Exception as e:
        return f"smtp_send_failed:{e.__class__.__name__}"
