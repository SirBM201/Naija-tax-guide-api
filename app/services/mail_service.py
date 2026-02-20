# app/services/mail_service.py
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional


# ---------------------------------------------------------
# ENV CONFIG
# ---------------------------------------------------------
MAIL_ENABLED = (os.getenv("MAIL_ENABLED", "false").lower() == "true")

MAIL_HOST = os.getenv("MAIL_HOST", "")
MAIL_PORT = int(os.getenv("MAIL_PORT", "2525") or "2525")

MAIL_USER = os.getenv("MAIL_USER", "")
MAIL_PASS = os.getenv("MAIL_PASS", "")

MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "NaijaTax Guide")
MAIL_FROM_EMAIL = os.getenv("MAIL_FROM_EMAIL", "no-reply@example.com")


# ---------------------------------------------------------
# SEND EMAIL CORE
# ---------------------------------------------------------
def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
) -> bool:
    """
    Sends transactional email via SMTP.
    Returns True if sent successfully.
    """

    if not MAIL_ENABLED:
        print("[mail] MAIL_ENABLED=false → skipping send")
        return False

    if not all([MAIL_HOST, MAIL_PORT, MAIL_USER, MAIL_PASS]):
        print("[mail] Missing SMTP config")
        return False

    try:
        msg = MIMEMultipart("alternative")

        msg["Subject"] = subject
        msg["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM_EMAIL}>"
        msg["To"] = to_email

        if text_body:
            msg.attach(MIMEText(text_body, "plain"))

        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(MAIL_HOST, MAIL_PORT) as server:
            server.starttls()
            server.login(MAIL_USER, MAIL_PASS)
            server.sendmail(
                MAIL_FROM_EMAIL,
                to_email,
                msg.as_string(),
            )

        print(f"[mail] Sent → {to_email}")
        return True

    except Exception as e:
        print(f"[mail] ERROR → {e}")
        return False


# ---------------------------------------------------------
# OTP TEMPLATE
# ---------------------------------------------------------
def send_otp_email(to_email: str, otp_code: str) -> bool:
    """
    Sends OTP email using branded template.
    """

    subject = "Your Login OTP Code"

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

    text_body = f"Your OTP code is: {otp_code}"

    return send_email(
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
