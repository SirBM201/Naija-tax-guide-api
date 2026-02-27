# app/services/mail_service.py
from __future__ import annotations

import os
import smtplib
from typing import Optional, Tuple, Dict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return default


def _get_int_env(*names: str, default: int) -> int:
    raw = _get_env(*names, default=str(default))
    try:
        return int(raw)
    except Exception:
        return default


def _mail_config() -> Tuple[bool, Dict[str, str], Dict[str, bool]]:
    """
    Supports BOTH MAIL_* and SMTP_* env naming.
    """
    enabled = _truthy(_get_env("MAIL_ENABLED", "SMTP_ENABLED", default="0"))

    host = _get_env("MAIL_HOST", "SMTP_HOST", default="")
    port = _get_int_env("MAIL_PORT", "SMTP_PORT", default=2525)

    user = _get_env("MAIL_USER", "SMTP_USER", default="")
    password = _get_env("MAIL_PASS", "SMTP_PASS", default="")

    from_name = _get_env("MAIL_FROM_NAME", "SMTP_FROM_NAME", default="NaijaTax Guide")
    from_email = _get_env("MAIL_FROM_EMAIL", "SMTP_FROM_EMAIL", default="no-reply@example.com")

    # TLS/SSL toggles
    use_tls = _truthy(_get_env("MAIL_USE_TLS", "SMTP_USE_TLS", default="1"))
    use_ssl = _truthy(_get_env("MAIL_USE_SSL", "SMTP_USE_SSL", default="0"))

    debug = _truthy(_get_env("MAIL_DEBUG", "SMTP_DEBUG", default="0"))

    cfg = {
        "host": host,
        "port": str(port),
        "user": user,
        "pass": password,
        "from_name": from_name,
        "from_email": from_email,
    }
    flags = {"enabled": enabled, "use_tls": use_tls, "use_ssl": use_ssl, "debug": debug}
    return enabled, cfg, flags


# ---------------------------------------------------------
# SEND EMAIL CORE
# ---------------------------------------------------------
def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
) -> bool:
    to_email = (to_email or "").strip()
    subject = (subject or "").strip()

    enabled, cfg, flags = _mail_config()

    if not enabled:
        print("[mail] disabled (MAIL_ENABLED/SMTP_ENABLED is falsey)")
        return False

    missing = []
    if not cfg["host"]:
        missing.append("MAIL_HOST/SMTP_HOST")
    if not cfg["port"]:
        missing.append("MAIL_PORT/SMTP_PORT")
    if not cfg["user"]:
        missing.append("MAIL_USER/SMTP_USER")
    if not cfg["pass"]:
        missing.append("MAIL_PASS/SMTP_PASS")
    if not cfg["from_email"]:
        missing.append("MAIL_FROM_EMAIL/SMTP_FROM_EMAIL")

    if not to_email:
        missing.append("to_email")

    if not subject:
        missing.append("subject")

    if missing:
        print(f"[mail] Missing config/fields: {', '.join(missing)}")
        if flags["debug"]:
            print("[mail] raw cfg:", {k: ("***" if k == "pass" else v) for k, v in cfg.items()})
            print("[mail] flags:", flags)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f'{cfg["from_name"]} <{cfg["from_email"]}>'
    msg["To"] = to_email

    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body or "", "html", "utf-8"))

    try:
        if flags["debug"]:
            print("[mail] sending with:", {
                "host": cfg["host"],
                "port": cfg["port"],
                "use_tls": flags["use_tls"],
                "use_ssl": flags["use_ssl"],
                "from": cfg["from_email"],
                "to": to_email,
            })

        if flags["use_ssl"]:
            server: smtplib.SMTP = smtplib.SMTP_SSL(cfg["host"], int(cfg["port"]), timeout=20)
        else:
            server = smtplib.SMTP(cfg["host"], int(cfg["port"]), timeout=20)

        with server as s:
            s.ehlo()
            if flags["use_tls"] and not flags["use_ssl"]:
                s.starttls()
                s.ehlo()

            s.login(cfg["user"], cfg["pass"])
            s.sendmail(cfg["from_email"], [to_email], msg.as_string())

        print(f"[mail] Sent -> {to_email}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        print(f"[mail] AUTH ERROR -> {e}")
        return False
    except smtplib.SMTPException as e:
        print(f"[mail] SMTP ERROR -> {e}")
        return False
    except Exception as e:
        print(f"[mail] ERROR -> {repr(e)}")
        return False


# ---------------------------------------------------------
# OTP TEMPLATE
# ---------------------------------------------------------
def send_otp_email(to_email: str, otp_code: str) -> bool:
    otp_code = (otp_code or "").strip()
    subject = (os.getenv("WEB_OTP_EMAIL_SUBJECT") or "Your Login OTP Code").strip()

    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
        <h2 style="margin:0 0 12px 0;">NaijaTax Guide</h2>
        <p style="margin:0 0 12px 0;">Your One-Time Password (OTP) is:</p>
        <div style="
            font-size:32px;
            font-weight:bold;
            letter-spacing:4px;
            background:#f4f4f4;
            padding:15px;
            text-align:center;
            border-radius:8px;
            margin:0 0 12px 0;
        ">{otp_code}</div>
        <p style="margin:0 0 12px 0;">This code expires in 10 minutes.</p>
        <p style="margin:0 0 12px 0;">If you did not request this login, ignore this email.</p>
        <hr style="border:none;border-top:1px solid #ddd;margin:16px 0;">
        <small style="color:#666;">© NaijaTax Guide</small>
    </div>
    """.strip()

    text_body = f"Your OTP code is: {otp_code}\nThis code expires in 10 minutes."

    return send_email(
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
