# app/services/web_auth_service.py
from __future__ import annotations

import os
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, List

from app.core.supabase_client import supabase
from app.core.mailer import send_mail  # if present in your repo; keep as-is
from app.core.config import (
    WEB_AUTH_ENABLED,
    WEB_AUTH_OTP_TTL_SECONDS,
    WEB_AUTH_TOKEN_TTL_DAYS,
    WEB_AUTH_COOKIE_NAME,
    WEB_AUTH_DEV_OTP_ENABLED,
    WEB_AUTH_DEV_ALLOWED_CONTACTS,
    WEB_AUTH_DEV_SHARED_SECRET,
    WEB_AUTH_MASTER_OTP,
)

# --------------------------------------------------
# TABLE NAMES (env-overridable)
# --------------------------------------------------
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()

ACCOUNTS_TABLE = _env("ACCOUNTS_TABLE", "accounts")
WEB_OTPS_TABLE = _env("WEB_OTPS_TABLE", "web_otps")
WEB_TOKENS_TABLE = _env("WEB_TOKENS_TABLE", "web_tokens")

# --------------------------------------------------
# IMPORTANT ARCHITECTURE RULE
# --------------------------------------------------
# This project uses accounts.account_id as the GLOBAL account identifier.
# Therefore:
#   - web_tokens.account_id MUST store accounts.account_id
#   - FK(web_tokens.account_id) should reference accounts.account_id
#
# If you store accounts.id in web_tokens.account_id while FK points to account_id,
# you will get: 23503 foreign_key_violation
# --------------------------------------------------


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _hash_otp(contact: str, purpose: str, otp: str) -> str:
    # deterministic server-side hash; otp never stored in clear
    payload = f"{contact}|{purpose}|{otp}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _cookie_name() -> str:
    return (WEB_AUTH_COOKIE_NAME or _env("WEB_AUTH_COOKIE_NAME") or "ntg_session").strip() or "ntg_session"


# --------------------------------------------------
# DEV MODE GUARD (optional)
# --------------------------------------------------
def _dev_guard(contact: str, shared_secret: Optional[str]) -> Optional[str]:
    if not WEB_AUTH_DEV_OTP_ENABLED:
        return None

    allowed = (WEB_AUTH_DEV_ALLOWED_CONTACTS or "").strip()
    if allowed:
        allowed_list = [x.strip() for x in allowed.split(",") if x.strip()]
        if allowed_list and contact not in allowed_list:
            return "Contact is not allowed in DEV mode"

    secret = (WEB_AUTH_DEV_SHARED_SECRET or "").strip()
    if secret:
        if not shared_secret or shared_secret != secret:
            return "Invalid shared_secret"

    return None


# --------------------------------------------------
# ACCOUNT BINDING (provider=web, provider_user_id=contact)
#
# Canonical account identifier returned here MUST be accounts.account_id
# --------------------------------------------------
def _extract_global_account_id(row: Dict[str, Any]) -> Optional[str]:
    """
    Return the canonical global account identifier.
    Must be accounts.account_id.
    """
    v = row.get("account_id")
    if v:
        return str(v)
    return None


def _get_or_create_web_account(contact: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Ensures a web account exists for the contact, returns (ok, global_account_id, err).
    """
    # 1) Try fetch existing
    q = (
        _sb()
        .table(ACCOUNTS_TABLE)
        .select("id, account_id")
        .eq("provider", "web")
        .eq("provider_user_id", contact)
        .limit(1)
        .execute()
    )

    if getattr(q, "data", None):
        row = q.data[0]
        gid = _extract_global_account_id(row)

        # If account_id is missing, backfill to id (one-time repair)
        if not gid:
            pk = row.get("id")
            if not pk:
                return False, None, "Account exists but missing primary key (id)"
            try:
                _sb().table(ACCOUNTS_TABLE).update({"account_id": str(pk)}).eq("id", str(pk)).execute()
            except Exception:
                # even if update failed, we still cannot proceed safely
                return False, None, "Account exists but account_id is missing and could not be repaired"
            gid = str(pk)

        return True, gid, None

    # 2) Create new account
    ins = (
        _sb()
        .table(ACCOUNTS_TABLE)
        .insert(
            {
                "provider": "web",
                "provider_user_id": contact,
                "display_name": contact,
                "phone_e164": contact,  # compatibility if your schema reuses phone_e164 for email
            }
        )
        .select("id, account_id")
        .execute()
    )

    if not getattr(ins, "data", None):
        return False, None, "Failed to create account"

    row = ins.data[0]
    pk = row.get("id")
    gid = row.get("account_id")

    # If DB didn’t auto-populate account_id, backfill it to pk.
    if not gid:
        if not pk:
            return False, None, "Created account but missing id"
        try:
            _sb().table(ACCOUNTS_TABLE).update({"account_id": str(pk)}).eq("id", str(pk)).execute()
        except Exception:
            return False, None, "Created account but could not set account_id"
        gid = str(pk)

    return True, str(gid), None


# --------------------------------------------------
# OTP REQUEST (web_otps)
# --------------------------------------------------
def request_web_otp(
    contact: str,
    purpose: str,
    device_id: Optional[str] = None,
    shared_secret: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    contact = (contact or "").strip()
    purpose = (purpose or "").strip() or "web_login"

    if not contact:
        return {"ok": False, "error": "missing_contact"}
    if not WEB_AUTH_ENABLED:
        return {"ok": False, "error": "web_auth_disabled"}

    if WEB_AUTH_DEV_OTP_ENABLED:
        err = _dev_guard(contact, shared_secret)
        if err:
            return {"ok": False, "error": err}

    now = _now_utc()

    # revoke old unused OTPs
    _sb().table(WEB_OTPS_TABLE).update({"revoked_at": _iso(now)}).eq("contact", contact).eq(
        "purpose", purpose
    ).is_("used_at", "null").is_("revoked_at", "null").execute()

    otp = f"{secrets.randbelow(1000000):06d}"
    expires_at = now + timedelta(seconds=int(WEB_AUTH_OTP_TTL_SECONDS))
    code_hash = _hash_otp(contact, purpose, otp)

    payload: Dict[str, Any] = {
        "contact": contact,
        "purpose": purpose,
        "code_hash": code_hash,
        "expires_at": _iso(expires_at),
        "used_at": None,
        "revoked_at": None,
    }
    if device_id:
        payload["device_id"] = device_id
    if ip:
        payload["ip"] = ip
    if user_agent:
        payload["user_agent"] = user_agent

    _sb().table(WEB_OTPS_TABLE).insert(payload).execute()

    out: Dict[str, Any] = {
        "ok": True,
        "ttl_minutes": int(int(WEB_AUTH_OTP_TTL_SECONDS) / 60),
    }

    # DEV mode shows OTP for testing
    if WEB_AUTH_DEV_OTP_ENABLED:
        out["dev_otp"] = otp

    # If you also email OTP in prod, do it here (keep your existing mail logic)
    # Example (only if your repo uses it):
    # send_mail(to=contact, subject="Your Naija Tax Guide OTP", text=f"Your OTP is {otp}")

    return out


# --------------------------------------------------
# OTP VERIFY (web_otps -> web_tokens)
# --------------------------------------------------
def verify_web_otp(
    contact: str,
    purpose: str,
    otp: str,
    device_id: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    contact = (contact or "").strip()
    purpose = (purpose or "").strip() or "web_login"
    otp = (otp or "").strip()

    if not contact or not otp:
        return {"ok": False, "error": "missing_contact_or_otp"}

    now = _now_utc()

    # Master OTP bypass
    if WEB_AUTH_MASTER_OTP and otp == WEB_AUTH_MASTER_OTP:
        ok, global_account_id, err = _get_or_create_web_account(contact)
        if not ok:
            return {"ok": False, "error": err}
        tok = _create_web_token(global_account_id, ip=ip, user_agent=user_agent, device_id=device_id)
        return {
            "ok": True,
            "account_id": global_account_id,
            "auth_mode": "cookie+bearer",
            "token": tok["token"],
            "expires_at": tok["expires_at"],
        }

    code_hash = _hash_otp(contact, purpose, otp)

    q = (
        _sb()
        .table(WEB_OTPS_TABLE)
        .select("*")
        .eq("contact", contact)
        .eq("purpose", purpose)
        .eq("code_hash", code_hash)
        .is_("used_at", "null")
        .is_("revoked_at", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if not getattr(q, "data", None):
        return {"ok": False, "error": "invalid_or_expired_otp"}

    row = q.data[0]
    exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if now > exp:
        _sb().table(WEB_OTPS_TABLE).update({"revoked_at": _iso(now)}).eq("id", row["id"]).execute()
        return {"ok": False, "error": "otp_expired"}

    _sb().table(WEB_OTPS_TABLE).update({"used_at": _iso(now)}).eq("id", row["id"]).execute()

    ok, global_account_id, err = _get_or_create_web_account(contact)
    if not ok:
        return {"ok": False, "error": err}

    tok = _create_web_token(global_account_id, ip=ip, user_agent=user_agent, device_id=device_id)

    return {
        "ok": True,
        "account_id": global_account_id,
        "auth_mode": "cookie+bearer",
        "token": tok["token"],
        "expires_at": tok["expires_at"],
    }


# --------------------------------------------------
# TOKEN CREATE (web_tokens)
# account_id stored here MUST match accounts.account_id (FK)
# --------------------------------------------------
def _create_web_token(
    global_account_id: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    raw_token = secrets.token_hex(32)  # 64 hex chars
    token_hash = _hash_token(raw_token)
    now = _now_utc()
    expires_at = now + timedelta(days=int(WEB_AUTH_TOKEN_TTL_DAYS))

    payload: Dict[str, Any] = {
        "account_id": global_account_id,  # ✅ accounts.account_id
        "token_hash": token_hash,
        "expires_at": _iso(expires_at),
        "revoked": False,
        "last_seen_at": _iso(now),
    }
    if ip:
        payload["ip"] = ip
    if user_agent:
        payload["user_agent"] = user_agent
    if device_id:
        payload["device_id"] = device_id

    _sb().table(WEB_TOKENS_TABLE).insert(payload).execute()

    return {"token": raw_token, "expires_at": _iso(expires_at)}
