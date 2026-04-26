from __future__ import annotations

import os
import smtplib
import ssl
import socket
from typing import Optional, Dict, Any
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


# ---------------------------------------------------------
# ENV CONFIG (MAIL_* primary; SMTP_* fallback)
# ---------------------------------------------------------
MAIL_ENABLED = _truthy(os.getenv("MAIL_ENABLED", "1"))  # Changed default to 1

MAIL_HOST = (os.getenv("MAIL_HOST") or os.getenv("SMTP_HOST") or "live.smtp.mailtrap.io").strip()
MAIL_PORT = int((os.getenv("MAIL_PORT") or os.getenv("SMTP_PORT") or "587").strip() or "587")

MAIL_USER = (os.getenv("MAIL_USER") or os.getenv("SMTP_USER") or "").strip()
MAIL_PASS = (os.getenv("MAIL_PASS") or os.getenv("SMTP_PASS") or "").strip()

MAIL_FROM_NAME = (os.getenv("MAIL_FROM_NAME") or "NaijaTax Guide").strip()
MAIL_FROM_EMAIL = (os.getenv("MAIL_FROM_EMAIL") or os.getenv("SMTP_FROM") or "noreply@naijataxguides.com").strip()

MAIL_USE_SSL = _truthy(os.getenv("MAIL_USE_SSL", "0"))
MAIL_USE_TLS = _truthy(os.getenv("MAIL_USE_TLS", "1"))

DEFAULT_OTP_SUBJECT = (os.getenv("WEB_OTP_EMAIL_SUBJECT") or "Your NaijaTax Guide OTP").strip()
SMTP_TIMEOUT_SECONDS = int((os.getenv("MAIL_TIMEOUT_SECONDS") or "10").strip() or "10")


def _smtp_config_snapshot(to_email: str) -> Dict[str, Any]:
    return {
        "enabled": MAIL_ENABLED,
        "host": MAIL_HOST,
        "port": MAIL_PORT,
        "use_ssl": MAIL_USE_SSL,
        "use_tls": MAIL_USE_TLS,
        "timeout": SMTP_TIMEOUT_SECONDS,
        "user_present": bool(MAIL_USER),
        "pass_present": bool(MAIL_PASS),
        "from": f"{MAIL_FROM_NAME} <{MAIL_FROM_EMAIL}>",
        "to": to_email,
    }


def _log(stage: str, **kwargs: Any) -> None:
    try:
        print(f"[mail_service] {stage} | {kwargs}", flush=True)
    except Exception:
        pass


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
    Returns a structured result:
      { ok: bool, error?: str, root_cause?: str, debug?: {...} }
    """
    to_email = (to_email or "").strip().lower()
    if not to_email:
        return {"ok": False, "error": "to_email_required"}

    if not MAIL_ENABLED:
        return {
            "ok": False,
            "error": "mail_disabled",
            "debug": _smtp_config_snapshot(to_email),
        }

    # Check for missing credentials
    missing = []
    if not MAIL_HOST:
        missing.append("MAIL_HOST")
    if not MAIL_PORT:
        missing.append("MAIL_PORT")
    if not MAIL_USER:
        missing.append("MAIL_USER")
    if not MAIL_PASS:
        missing.append("MAIL_PASS")
    if not MAIL_FROM_EMAIL:
        missing.append("MAIL_FROM_EMAIL")
    
    if missing:
        _log("mail_not_configured", missing=missing)
        return {
            "ok": False,
            "error": "mail_not_configured",
            "missing": missing,
            "debug": _smtp_config_snapshot(to_email),
        }

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM_EMAIL}>"
    msg["To"] = to_email

    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    _log("prepare_send", to=to_email, subject=subject, config=_smtp_config_snapshot(to_email))

    try:
        if MAIL_USE_SSL:
            _log("connect_ssl_start", host=MAIL_HOST, port=MAIL_PORT, timeout=SMTP_TIMEOUT_SECONDS)
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                MAIL_HOST,
                MAIL_PORT,
                timeout=SMTP_TIMEOUT_SECONDS,
                context=context,
            ) as server:
                _log("connect_ssl_ok")
                server.login(MAIL_USER, MAIL_PASS)
                _log("login_ok")
                server.sendmail(MAIL_FROM_EMAIL, [to_email], msg.as_string())
                _log("sendmail_ok")
        else:
            _log("connect_start", host=MAIL_HOST, port=MAIL_PORT, timeout=SMTP_TIMEOUT_SECONDS)
            with smtplib.SMTP(MAIL_HOST, MAIL_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
                _log("connect_ok")
                server.ehlo()
                _log("ehlo_ok")

                if MAIL_USE_TLS:
                    _log("starttls_start")
                    context = ssl.create_default_context()
                    server.starttls(context=context)
                    _log("starttls_ok")
                    server.ehlo()
                    _log("ehlo_after_starttls_ok")

                _log("login_start", user=MAIL_USER)
                server.login(MAIL_USER, MAIL_PASS)
                _log("login_ok")

                _log("sendmail_start", from_email=MAIL_FROM_EMAIL, to=to_email)
                server.sendmail(MAIL_FROM_EMAIL, [to_email], msg.as_string())
                _log("sendmail_ok")

        return {"ok": True, "debug": _smtp_config_snapshot(to_email)}

    except smtplib.SMTPAuthenticationError as e:
        _log("smtp_auth_failed", error=repr(e))
        return {
            "ok": False,
            "error": "smtp_auth_failed",
            "message": "SMTP authentication failed. Please check your Mailtrap username and password.",
            "root_cause": repr(e),
            "debug": _smtp_config_snapshot(to_email),
        }
    except smtplib.SMTPConnectError as e:
        _log("smtp_connect_failed", error=repr(e))
        return {
            "ok": False,
            "error": "smtp_connect_failed",
            "message": f"Could not connect to {MAIL_HOST}:{MAIL_PORT}",
            "root_cause": repr(e),
            "debug": _smtp_config_snapshot(to_email),
        }
    except smtplib.SMTPServerDisconnected as e:
        _log("smtp_server_disconnected", error=repr(e))
        return {
            "ok": False,
            "error": "smtp_server_disconnected",
            "message": "SMTP server disconnected unexpectedly",
            "root_cause": repr(e),
            "debug": _smtp_config_snapshot(to_email),
        }
    except socket.timeout as e:
        _log("smtp_timeout", error=repr(e))
        return {
            "ok": False,
            "error": "smtp_timeout",
            "message": f"Connection timed out after {SMTP_TIMEOUT_SECONDS} seconds",
            "root_cause": repr(e),
            "debug": _smtp_config_snapshot(to_email),
        }
    except TimeoutError as e:
        _log("timeout_error", error=repr(e))
        return {
            "ok": False,
            "error": "smtp_timeout",
            "message": f"Connection timed out after {SMTP_TIMEOUT_SECONDS} seconds",
            "root_cause": repr(e),
            "debug": _smtp_config_snapshot(to_email),
        }
    except Exception as e:
        _log("mail_send_failed", error=repr(e))
        return {
            "ok": False,
            "error": "mail_send_failed",
            "message": f"Unexpected error: {str(e)}",
            "root_cause": repr(e),
            "debug": _smtp_config_snapshot(to_email),
        }


# ---------------------------------------------------------
# OTP TEMPLATE - Enhanced for better deliverability
# ---------------------------------------------------------
def send_otp_email(to_email: str, otp_code: str) -> Dict[str, Any]:
    subject = DEFAULT_OTP_SUBJECT

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>NaijaTax Guide OTP</title>
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #1a73e8 0%, #0d47a1 100%); padding: 30px 20px; border-radius: 12px 12px 0 0; text-align: center;">
            <h1 style="color: white; margin: 0; font-size: 28px;">NaijaTax Guide</h1>
            <p style="color: rgba(255,255,255,0.9); margin: 10px 0 0 0;">Your Trusted Tax Filing Platform</p>
        </div>
        
        <div style="background: #ffffff; padding: 30px; border-radius: 0 0 12px 12px; border: 1px solid #e0e0e0; border-top: none;">
            <p style="font-size: 16px; margin: 0 0 20px 0;">Hello,</p>
            <p style="font-size: 16px; margin: 0 0 10px 0;">Your One-Time Password (OTP) for login is:</p>
            
            <div style="text-align: center; margin: 30px 0;">
                <span style="font-size: 42px; font-weight: bold; letter-spacing: 8px; background: #f5f5f5; padding: 15px 25px; border-radius: 12px; border: 2px solid #1a73e8; color: #1a73e8; font-family: monospace;">{otp_code}</span>
            </div>
            
            <p style="font-size: 14px; color: #666; margin: 20px 0 10px 0;">This code will expire in <strong>10 minutes</strong>.</p>
            <p style="font-size: 14px; color: #666; margin: 0 0 20px 0;">If you didn't request this, please ignore this email.</p>
            
            <hr style="margin: 30px 0; border: none; border-top: 1px solid #eee;">
            
            <p style="font-size: 12px; color: #999; text-align: center; margin: 0;">
                NaijaTax Guide - Igniting Ideas. Building the Future.<br>
                <a href="https://www.naijataxguides.com" style="color: #1a73e8; text-decoration: none;">www.naijataxguides.com</a>
            </p>
        </div>
    </body>
    </html>
    """

    text_body = f"""NaijaTax Guide - Your OTP Code

Hello,

Your One-Time Password (OTP) for login is: {otp_code}

This code will expire in 10 minutes.

If you didn't request this, please ignore this email.

---
NaijaTax Guide - Igniting Ideas. Building the Future.
https://www.naijataxguides.com
"""

    return send_email(
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
