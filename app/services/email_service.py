# app/services/mail_service.py
from __future__ import annotations

import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, Dict, Any


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


# ---------------------------------------------------------
# ENV CONFIG (supports both MAIL_* and SMTP_* styles)
# ---------------------------------------------------------
MAIL_ENABLED = _truthy(_get_env("MAIL_ENABLED", _get_env("SMTP_ENABLED", "0")))

MAIL_HOST = _get_env("MAIL_HOST", _get_env("SMTP_HOST", ""))
MAIL_PORT = int(_get_env("MAIL_PORT", _get_env("SMTP_PORT", "587")) or "587")

MAIL_USER = _get_env("MAIL_USER", _get_env("SMTP_USER", ""))
MAIL_PASS = _get_env("MAIL_PASS", _get_env("SMTP_PASS", ""))

MAIL_FROM_EMAIL = _get_env("MAIL_FROM_EMAIL", _get_env("SMTP_FROM", MAIL_USER))
MAIL_FROM_NAME = _get_env("MAIL_FROM_NAME", "NaijaTax Guide")

MAIL_USE_TLS = _truthy(_get_env("MAIL_USE_TLS", "1"))
MAIL_USE_SSL = _truthy(_get_env("MAIL_USE_SSL", "0"))

MAIL_TIMEOUT = int(_get_env("MAIL_TIMEOUT", "20") or "20")


def smtp_status() -> Dict[str, Any]:
    """
    Returns a debug-safe snapshot of smtp config status (no secrets).
    """
    return {
        "enabled": MAIL_ENABLED,
        "host_present": bool(MAIL_HOST),
        "port": MAIL_PORT,
        "user_present": bool(MAIL_USER),
        "pass_present": bool(MAIL_PASS),
        "from": MAIL_FROM_EMAIL,
        "use_tls": MAIL_USE_TLS,
        "use_ssl": MAIL_USE_SSL,
        "timeout": MAIL_TIMEOUT,
    }


# ---------------------------------------------------------
# SEND EMAIL CORE
# ---------------------------------------------------------
def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends transactional email via SMTP.

    Returns:
      {"ok": True}
      {"ok": False, "error": "...", "root_cause": "...", "debug": {...}}
    """

    if not MAIL_ENABLED:
        return {"ok": False, "error": "mail_disabled", "debug": smtp_status()}

    if not all([MAIL_HOST, str(MAIL_PORT), MAIL_USER, MAIL_PASS, MAIL_FROM_EMAIL]):
        return {"ok": False, "error": "smtp_not_configured", "debug": smtp_status()}

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM_EMAIL}>"
        msg["To"] = to_email

        if text_body:
            msg.attach(MIMEText(text_body, "plain"))

        msg.attach(MIMEText(html_body, "html"))

        if MAIL_USE_SSL:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(MAIL_HOST, MAIL_PORT, timeout=MAIL_TIMEOUT, context=ctx) as server:
                server.login(MAIL_USER, MAIL_PASS)
                server.sendmail(MAIL_FROM_EMAIL, to_email, msg.as_string())
        else:
            with smtplib.SMTP(MAIL_HOST, MAIL_PORT, timeout=MAIL_TIMEOUT) as server:
                if MAIL_USE_TLS:
                    ctx = ssl.create_default_context()
                    server.starttls(context=ctx)
                server.login(MAIL_USER, MAIL_PASS)
                server.sendmail(MAIL_FROM_EMAIL, to_email, msg.as_string())

        return {"ok": True}

    except Exception as e:
        return {
            "ok": False,
            "error": "smtp_send_failed",
            "root_cause": repr(e),
            "debug": smtp_status(),
        }


# ---------------------------------------------------------
# OTP TEMPLATE
# ---------------------------------------------------------
def send_otp_email(to_email: str, otp_code: str) -> Dict[str, Any]:
    """
    Sends OTP email using branded template.
    """
    subject = _get_env("WEB_OTP_EMAIL_SUBJECT", "Your NaijaTax Guide OTP Code")

    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
        <h2>NaijaTax Guide</h2>
        <p>Your One-Time Password (OTP) is:</p>
        <div style="
            font-size:32px;
            font-weight:bold;
            letter-spacing:4px;
            background:#f4f4f4;
            padding:15px;
            text-align:center;
            border-radius:8px;
        ">
            {otp_code}
        </div>
        <p>This code expires in 10 minutes.</p>
        <p>If you did not request this login, ignore this email.</p>
        <hr>
        <small>© NaijaTax Guide</small>
    </div>
    """

    text_body = f"Your OTP code is: {otp_code}\n\nThis code expires soon. If you did not request this, ignore this email."

    return send_email(
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
