# app/services/web_otp_service.py
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from ..core.supabase_client import supabase

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------

OTP_ENABLED = (os.getenv("WEB_OTP_ENABLED", "0").strip() == "1")
OTP_TTL_MINUTES = int((os.getenv("WEB_OTP_TTL_MINUTES", "10") or "10").strip())
OTP_LEN = int((os.getenv("WEB_OTP_LEN", "6") or "6").strip())

# In stub mode, the OTP returned/accepted can be fixed for testing
STUB_OTP = (os.getenv("WEB_OTP_STUB_CODE", "123456") or "123456").strip()

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _sb():
    try:
        return supabase()
    except TypeError:
        return supabase

def _table(name: str):
    return _sb().table(name)

def _gen_otp() -> str:
    # e.g. "6 digits"
    low = 10 ** (OTP_LEN - 1)
    high = (10 ** OTP_LEN) - 1
    return str(random.randint(low, high))

def _normalize_phone(phone: str) -> str:
    return (phone or "").strip()

# ------------------------------------------------------------
# Public API (these MUST exist to satisfy imports)
# ------------------------------------------------------------

def request_web_login_otp(phone: str) -> Dict[str, Any]:
    """
    Creates an OTP for web login.
    If OTP_ENABLED=0, returns ok (stub mode) so boot never depends on external OTP infra.
    """
    phone = _normalize_phone(phone)
    if not phone:
        return {"ok": False, "error": "missing_phone"}

    # Stub mode (fast dev / no SMS provider / no WhatsApp integration needed)
    if not OTP_ENABLED:
        # You can still store it if table exists, but do not fail if it doesn't.
        _best_effort_store_otp(phone, STUB_OTP)
        return {
            "ok": True,
            "mode": "stub",
            "ttl_minutes": OTP_TTL_MINUTES,
            # Do not return OTP in prod; in stub/dev we allow it for testing.
            "otp": STUB_OTP,
        }

    # Real mode
    otp = _gen_otp()
    _best_effort_store_otp(phone, otp)
    # NOTE: sending SMS/WhatsApp is intentionally out of scope here
    # You can add provider integration later without changing this contract.
    return {"ok": True, "mode": "real", "ttl_minutes": OTP_TTL_MINUTES}


def verify_web_login_otp(phone: str, otp: str) -> Dict[str, Any]:
    """
    Verifies OTP and returns a web auth token if successful.
    If OTP_ENABLED=0, accepts STUB_OTP.
    """
    phone = _normalize_phone(phone)
    otp = (otp or "").strip()

    if not phone or not otp:
        return {"ok": False, "error": "missing_phone_or_otp"}

    if not OTP_ENABLED:
        if otp != STUB_OTP:
            return {"ok": False, "error": "invalid_otp"}
        # In stub mode, issue token best-effort (or return a placeholder)
        token = _best_effort_issue_web_token(phone)
        return {"ok": True, "mode": "stub", "token": token}

    # Real mode: lookup OTP record
    rec = _best_effort_get_latest_otp(phone)
    if not rec:
        return {"ok": False, "error": "otp_not_found"}

    code = (rec.get("otp") or "").strip()
    expires_at = _parse_iso(rec.get("expires_at"))

    if not code or not expires_at:
        return {"ok": False, "error": "otp_record_invalid"}

    if _now_utc() > expires_at:
        return {"ok": False, "error": "otp_expired"}

    if otp != code:
        return {"ok": False, "error": "invalid_otp"}

    # Mark used (best effort)
    _best_effort_mark_otp_used(rec)

    # Issue token
    token = _best_effort_issue_web_token(phone)
    return {"ok": True, "mode": "real", "token": token}


# ------------------------------------------------------------
# Internal: best-effort Supabase storage
# ------------------------------------------------------------

def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = value.replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except Exception:
        return None

def _best_effort_store_otp(phone: str, otp: str) -> None:
    """
    Writes to web_otps if table exists.
    Schema assumed (recommended):
      - id (uuid)
      - phone (text)
      - otp (text)
      - expires_at (timestamptz)
      - used_at (timestamptz, nullable)
      - created_at (timestamptz)
    """
    now = _now_utc()
    expires = now + timedelta(minutes=max(1, OTP_TTL_MINUTES))
    payload = {
        "phone": phone,
        "otp": otp,
        "expires_at": _iso(expires),
        "created_at": _iso(now),
    }
    try:
        _table("web_otps").insert(payload).execute()
    except Exception:
        # Do not crash app if table isn't ready yet
        return

def _best_effort_get_latest_otp(phone: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            _table("web_otps")
            .select("*")
            .eq("phone", phone)
            .is_("used_at", None)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None
    except Exception:
        return None

def _best_effort_mark_otp_used(rec: Dict[str, Any]) -> None:
    try:
        rec_id = rec.get("id")
        if not rec_id:
            return
        _table("web_otps").update({"used_at": _iso(_now_utc())}).eq("id", rec_id).execute()
    except Exception:
        return

def _best_effort_issue_web_token(phone: str) -> str:
    """
    Writes to web_tokens if table exists.
    Recommended schema:
      - token (text, pk)
      - phone (text)
      - account_id (uuid/text nullable until linked)
      - expires_at (timestamptz)
      - created_at
      - revoked_at
    """
    token = os.urandom(24).hex()
    now = _now_utc()
    expires = now + timedelta(days=30)

    payload = {
        "token": token,
        "phone": phone,
        "expires_at": _iso(expires),
        "created_at": _iso(now),
    }
    try:
        _table("web_tokens").insert(payload).execute()
    except Exception:
        # If table doesn't exist, still return token (frontend can store it);
        # your require_auth_plus should then validate via accounts/web_tokens later.
        pass

    return token
