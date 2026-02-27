# app/services/mail_service.py
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass, asdict
from email.message import EmailMessage
from typing import Any, Dict, Optional, Tuple


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass
class MailConfig:
    enabled: bool
    host: str
    port: int
    user: str
    password: str
    from_email: str
    from_name: str
    use_tls: bool
    use_ssl: bool
    timeout: int


def _load_mail_config() -> MailConfig:
    """
    Supports both styles:
      - MAIL_HOST, MAIL_PORT, MAIL_USER, MAIL_PASS, MAIL_FROM_EMAIL, MAIL_FROM_NAME
      - SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM (optional SMTP_FROM_NAME)
    """

    enabled = _truthy(os.getenv("MAIL_ENABLED", "false"))

    # Prefer MAIL_* first, fallback to SMTP_*
    host = (os.getenv("MAIL_HOST") or os.getenv("SMTP_HOST") or "").strip()
    port_s = (os.getenv("MAIL_PORT") or os.getenv("SMTP_PORT") or "587").strip()
    user = (os.getenv("MAIL_USER") or os.getenv("SMTP_USER") or "").strip()
    password = (os.getenv("MAIL_PASS") or os.getenv("SMTP_PASS") or "").strip()

    # From
    from_email = (os.getenv("MAIL_FROM_EMAIL") or os.getenv("SMTP_FROM") or user or "").strip()
    from_name = (os.getenv("MAIL_FROM_NAME") or os.getenv("SMTP_FROM_NAME") or "NaijaTax Guide").strip()

    # Transport flags
    # Default to STARTTLS (TLS-on-upgrade) unless explicitly overridden
    use_tls = _truthy(os.getenv("MAIL_USE_TLS", os.getenv("SMTP_USE_TLS", "true")))
    use_ssl = _truthy(os.getenv("MAIL_USE_SSL", os.getenv("SMTP_USE_SSL", "false")))
    timeout = int((os.getenv("MAIL_TIMEOUT", os.getenv("SMTP_TIMEOUT", "20")) or "20").strip())

    try:
        port = int(port_s)
    except Exception:
        port = 587

    return MailConfig(
        enabled=enabled,
        host=host,
        port=port,
        user=user,
        password=password,
        from_email=from_email,
        from_name=from_name,
        use_tls=use_tls,
        use_ssl=use_ssl,
        timeout=timeout,
    )


def _safe_cfg_snapshot(cfg: MailConfig) -> Dict[str, Any]:
    d = asdict(cfg)
    if d.get("password"):
        d["password"] = "***redacted***"
    return d


def send_email_result(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns:
      {
        "sent": bool,
        "error": "<string or None>",
        "root_cause": "<repr(e) or None>",
        "config": { ... safe snapshot ... }
      }
    """
    cfg = _load_mail_config()

    out: Dict[str, Any] = {
        "sent": False,
        "error": None,
        "root_cause": None,
        "config": _safe_cfg_snapshot(cfg),
    }

    if not cfg.enabled:
        out["error"] = "mail_disabled"
        return out

    # Basic config validation
    missing = []
    if not cfg.host:
        missing.append("MAIL_HOST/SMTP_HOST")
    if not cfg.port:
        missing.append("MAIL_PORT/SMTP_PORT")
    if not cfg.user:
        missing.append("MAIL_USER/SMTP_USER")
    if not cfg.password:
        missing.append("MAIL_PASS/SMTP_PASS")
    if not cfg.from_email:
        missing.append("MAIL_FROM_EMAIL/SMTP_FROM")

    if missing:
        out["error"] = "mail_not_configured"
        out["root_cause"] = f"Missing: {', '.join(missing)}"
        return out

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{cfg.from_name} <{cfg.from_email}>"
    msg["To"] = to_email
    msg.set_content(text_body)

    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        print(f"[mail] sending with: {out['config']}")

        if cfg.use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=cfg.timeout, context=context) as server:
                server.login(cfg.user, cfg.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout) as server:
                if cfg.use_tls:
                    context = ssl.create_default_context()
                    server.starttls(context=context)
                server.login(cfg.user, cfg.password)
                server.send_message(msg)

        out["sent"] = True
        print(f"[mail] Sent -> {to_email}")
        return out

    except Exception as e:
        out["error"] = "mail_send_failed"
        out["root_cause"] = repr(e)
        print(f"[mail] ERROR -> {out['root_cause']}")
        return out


def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
) -> bool:
    """
    Compatibility wrapper (keeps your existing callers working).
    """
    r = send_email_result(
        to_email=to_email,
        subject=subject,
        text_body=text_body or "",
        html_body=html_body,
    )
    return bool(r.get("sent"))


def send_otp_email(to_email: str, otp_code: str) -> Dict[str, Any]:
    """
    OTP email with branded template.
    Returns a detailed result dict (sent/error/root_cause/config).
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

    text_body = f"Your OTP code is: {otp_code}\n\nThis code expires in 10 minutes."

    return send_email_result(
        to_email=to_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )
