# app/services/web_otp_service.py
from __future__ import annotations

import hashlib
import os
import random
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Dict, Optional

from ..core.supabase_client import supabase

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------

WEB_OTP_ENABLED = (os.getenv("WEB_OTP_ENABLED", "1").strip() == "1")
WEB_OTP_TTL_MINUTES = int((os.getenv("WEB_OTP_TTL_MINUTES", "10") or "10").strip())
WEB_OTP_LEN = int((os.getenv("WEB_OTP_LEN", "6") or "6").strip())

# If you still want a fallback stub in dev only:
WEB_OTP_STUB_CODE = (os.getenv("WEB_OTP_STUB_CODE", "123456") or "123456").strip()

WEB_SESSION_TTL_DAYS = int((os.getenv("WEB_SESSION_TTL_DAYS", "30") or "30").strip())

# OTP hashing (recommended)
WEB_OTP_PEPPER = (os.getenv("WEB_OTP_PEPPER", "") or "").strip()

# ------------------------------------------------------------
# Mail (Mailtrap SMTP)
# Supports both MAIL_* and SMTP_* env names to avoid mismatch.
# ------------------------------------------------------------

def _env_first(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return default

MAIL_ENABLED = _env_first("MAIL_ENABLED", "SMTP_ENABLED", default="0") == "1"
MAIL_HOST = _env_first("MAIL_HOST", "SMTP_HOST")
MAIL_PORT = int((_env_first("MAIL_PORT", "SMTP_PORT", default="0") or "0").strip() or "0")
MAIL_USER = _env_first("MAIL_USER", "SMTP_USER")
MAIL_PASS = _env_first("MAIL_PASS", "SMTP_PASS")
MAIL_FROM_EMAIL = _env_first("MAIL_FROM_EMAIL", default="no-reply@thecre8hub.com")
MAIL_FROM_NAME = _env_first("MAIL_FROM_NAME", default="NaijaTax Guide")

# If not explicitly set, assume STARTTLS on typical Mailtrap ports
MAIL_USE_TLS = _env_first("MAIL_USE_TLS", default="1") == "1"


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except Exception:
        return None

def _sb():
    try:
        return supabase()
    except TypeError:
        return supabase

def _table(name: str):
    return _sb().table(name)

def _clean(s: Any) -> str:
    return (s or "").strip()

def _gen_otp() -> str:
    # numeric OTP with fixed length
    low = 10 ** (WEB_OTP_LEN - 1)
    high = (10 ** WEB_OTP_LEN) - 1
    return str(random.randint(low, high))

def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _otp_hash(contact: str, purpose: str, otp: str) -> str:
    """
    Hash ties OTP to (contact,purpose) plus pepper.
    This prevents OTP reuse across contacts/purposes.
    """
    base = f"{WEB_OTP_PEPPER}:{contact}:{purpose}:{otp}"
    return _sha256_hex(base)

def _smtp_configured() -> bool:
    if not MAIL_ENABLED:
        return False
    if not MAIL_HOST or not MAIL_PORT or not MAIL_USER or not MAIL_PASS:
        return False
    return True

def _send_email_otp(to_email: str, otp: str, ttl_minutes: int) -> Dict[str, Any]:
    """
    Sends OTP to user's email using SMTP (Mailtrap).
    Returns {sent: bool, error?: str}
    """
    if not _smtp_configured():
        return {"sent": False, "error": "smtp_not_configured"}

    msg = EmailMessage()
    msg["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = f"Your NaijaTax Guide login code: {otp}"

    text = (
        f"Your NaijaTax Guide one-time login code is: {otp}\n\n"
        f"This code expires in {ttl_minutes} minutes.\n\n"
        f"If you did not request this code, ignore this email."
    )
    msg.set_content(text)

    try:
        with smtplib.SMTP(MAIL_HOST, MAIL_PORT, timeout=15) as server:
            if MAIL_USE_TLS:
                server.starttls()
            server.login(MAIL_USER, MAIL_PASS)
            server.send_message(msg)
        return {"sent": True}
    except Exception as e:
        return {"sent": False, "error": f"smtp_send_failed:{type(e).__name__}"}


# ------------------------------------------------------------
# Public API (MUST match app/routes/web_auth.py)
# ------------------------------------------------------------

def request_web_login_otp(contact: str, purpose: str = "web_login") -> Dict[str, Any]:
    """
    Generates + stores OTP for a web login flow.

    Returns:
      { ok: True, ttl_minutes, email_sent, email_to, email_error? }
      plus dev_otp ONLY when WEB_DEV_RETURN_OTP=1
    """
    contact = _clean(contact)
    purpose = _clean(purpose) or "web_login"
    if not contact:
        return {"ok": False, "error": "missing_contact"}

    # Generate OTP
    if not WEB_OTP_ENABLED:
        otp = WEB_OTP_STUB_CODE
        mode = "stub"
    else:
        otp = _gen_otp()
        mode = "real"

    # Store hashed OTP (code_hash) + used boolean
    stored = _best_effort_store_otp(contact=contact, purpose=purpose, otp=otp)

    # Send email if contact looks like an email address
    email_sent = False
    email_error = None
    if "@" in contact:
        r = _send_email_otp(to_email=contact, otp=otp, ttl_minutes=WEB_OTP_TTL_MINUTES)
        email_sent = bool(r.get("sent"))
        email_error = r.get("error")

    out: Dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "ttl_minutes": WEB_OTP_TTL_MINUTES,
        "email_to": contact if "@" in contact else None,
        "email_sent": email_sent,
        "email_error": email_error,
        "stored": stored,
    }

    # Optional dev return (ONLY if you still want it)
    if (os.getenv("WEB_DEV_RETURN_OTP", "0").strip() == "1"):
        out["dev_otp"] = otp

    return out


def verify_web_login_otp(contact: str, otp: str, purpose: str = "web_login") -> Dict[str, Any]:
    """
    Verifies OTP and returns a web session token.

    Expected by routes:
      verify_web_login_otp(contact=..., otp=..., purpose=...)

    Returns:
      { ok: True, token: "...", account_id?: "...", expires_at?: "..." }
    """
    contact = _clean(contact)
    otp = _clean(otp)
    purpose = _clean(purpose) or "web_login"

    if not contact or not otp:
        return {"ok": False, "error": "missing_contact_or_otp"}

    # Match record by code_hash, used=False, not expired
    code_hash = _otp_hash(contact=contact, purpose=purpose, otp=otp)
    rec = _best_effort_find_valid_otp(contact=contact, purpose=purpose, code_hash=code_hash)
    if not rec:
        return {"ok": False, "error": "invalid_otp"}

    # Mark used
    _best_effort_mark_otp_used(rec)

    # Issue web token (your project uses web_tokens with token_hash)
    token_info = _issue_web_session_token(contact=contact)
    return {"ok": True, **token_info}


# ------------------------------------------------------------
# Storage (best effort)
# Table expected: web_otps
# Recommended columns:
#   id uuid pk
#   contact text
#   purpose text
#   code_hash text
#   expires_at timestamptz
#   used bool default false
#   used_at timestamptz nullable (optional)
#   created_at timestamptz
#
# Session token expected: web_tokens (NOT web_sessions)
# Recommended columns:
#   id uuid pk
#   token_hash text unique
#   account_id text/uuid
#   expires_at timestamptz
#   revoked bool default false
#   last_seen_at timestamptz
#   created_at timestamptz
# ------------------------------------------------------------

def _best_effort_store_otp(contact: str, purpose: str, otp: str) -> bool:
    now = _now_utc()
    expires = now + timedelta(minutes=max(1, int(WEB_OTP_TTL_MINUTES)))

    payload = {
        "contact": contact,
        "purpose": purpose,
        "code_hash": _otp_hash(contact, purpose, otp),
        "expires_at": _iso(expires),
        "used": False,
        "created_at": _iso(now),
    }

    try:
        _table("web_otps").insert(payload).execute()
        return True
    except Exception:
        return False


def _best_effort_find_valid_otp(contact: str, purpose: str, code_hash: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            _table("web_otps")
            .select("id, expires_at, used")
            .eq("contact", contact)
            .eq("purpose", purpose)
            .eq("code_hash", code_hash)
            .eq("used", False)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return None

        row = rows[0]
        exp = _parse_iso(row.get("expires_at"))
        if not exp:
            return None
        if _now_utc() > exp:
            return None

        return row
    except Exception:
        return None


def _best_effort_mark_otp_used(rec: Dict[str, Any]) -> None:
    try:
        rec_id = rec.get("id")
        if not rec_id:
            return
        _table("web_otps").update({"used": True, "used_at": _iso(_now_utc())}).eq("id", rec_id).execute()
    except Exception:
        return


def _issue_web_session_token(contact: str) -> Dict[str, Any]:
    """
    Your system validates tokens via app/core/auth.py against web_tokens.token_hash,
    so we must insert hashed token into web_tokens.

    We also map contact -> account_id elsewhere (routes/service). For now we store contact
    only if your table has it; otherwise we store account_id only at issuance time in the route.
    """
    token = os.urandom(24).hex()
    now = _now_utc()
    expires = now + timedelta(days=max(1, int(WEB_SESSION_TTL_DAYS)))

    # IMPORTANT: token_hash must match app/core/auth.py _token_hash() logic (pepper + raw token).
    # If your auth.py hashes as sha256(f"{pepper}:{raw_token}"), do same here.
    pepper = (os.getenv("WEB_TOKEN_PEPPER", "") or "").strip()
    token_hash = _sha256_hex(f"{pepper}:{token}")

    payload = {
        "token_hash": token_hash,
        "expires_at": _iso(expires),
        "revoked": False,
        "last_seen_at": _iso(now),
        "created_at": _iso(now),
    }

    # If your web_tokens table includes contact, store it (optional)
    if _has_column("web_tokens", "contact"):
        payload["contact"] = contact

    try:
        _table("web_tokens").insert(payload).execute()
    except Exception:
        # Still return token so you can see failures on auth step if insertion failed
        pass

    return {"token": token, "expires_at": _iso(expires)}


def _has_column(table: str, col: str) -> bool:
    """
    Best-effort: attempt a select with the column; if it errors, assume missing.
    """
    try:
        _table(table).select(col).limit(1).execute()
        return True
    except Exception:
        return False
